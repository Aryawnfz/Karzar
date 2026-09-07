# -*- coding: utf-8 -*-
"""
ثبت امضا در کارزار (karzar.net) با Playwright و پروفایل ذخیره‌شدهٔ هر اکانت.

هر «کار» (job) در یک فرایندِ مستقل از وب‌سرور اجرا می‌شود
(`python karzar_sign.py --run <jid>`) و وضعیتش را روی دیسک نگه می‌دارد؛
بنابراین بستن تب مرورگر، رفتن به صفحهٔ دیگر یا ری‌استارت شدنِ worker های
Gunicorn آن را متوقف نمی‌کند:
  data/sign_jobs/<jid>.json            ← وضعیت کل کار و هر اکانت
  data/sign_jobs/<jid>.log             ← خروجی فرایندِ اجراکننده
  data/screenshots/<jid>/<acc_id>.png  ← اسکرین‌شات پیغام موفقیت هر اکانت

اکانت‌ها نوبت به نوبت (یکی پس از دیگری) امضا می‌کنند.

اگر کارزار در فرم امضا ورودی اضافه بخواهد (مثل «شهر» یا «سن»)، با اکانت اول کار
به حالت «waiting_input» می‌رود و فهرست فیلدها را در job می‌نویسد؛ سامانه آن‌ها را
در یک مودال از کاربر می‌گیرد و پس از ثبت، همان مقادیر برای همهٔ اکانت‌ها جایگذاری می‌شود.
"""

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from typing import Optional

from playwright.async_api import async_playwright, TimeoutError as PwTimeout

from karzar_login import _CHROME_ARGS, _USER_AGENT, _goto, _visible_text

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
JOBS_DIR = os.path.join(DATA_DIR, "sign_jobs")
SHOTS_DIR = os.path.join(DATA_DIR, "screenshots")

KARZAR_CAMPAIGN_URL = "https://www.karzar.net/{code}"

# انتخابگرهای صفحهٔ کارزار
SEL_SIGN_BTN = "#signup-submit"
SEL_SIGN_BTN_ALT = "#goto-signup"
SEL_SIGN_WRAPPER = "#signup-form-wrapper"
SEL_SHEET = "#bottom-sheet-signup"
SEL_NEXT_BTN = "#bottom-sheet-signup .signup-wizard-item.active .next-step"
SEL_SUCCESS = "#bottom-sheet-signup .signup-wizard-end__title"
SEL_WIZARD_ERROR = "#bottom-sheet-signup .signup-wizard-item.active .signup-wizard-item__error"
SEL_OTP_INPUT = "#bottom-sheet-signup #otp-code-input"
SEL_CAPTCHA = "#bottom-sheet-signup #captcha"
SEL_ACTIVE_ITEM = "#bottom-sheet-signup .signup-wizard-item.active"
SEL_ALL_ITEMS = "#bottom-sheet-signup .signup-wizard-item"

# فیلدهایی که جزو «ورودی‌های کارزار» نیستند (احراز هویت/کد امنیتی)
AUTH_FIELD_IDS = ("fullname", "yourmail", "captcha", "otp-code-input", "code", "a11y")
MAX_WIZARD_STEPS = 5
INPUT_WAIT_TIMEOUT = 15 * 60  # حداکثر انتظار برای ورود اطلاعات توسط کاربر (ثانیه)

SUCCESS_TEXT = "با موفقیت"

# وضعیت هر اکانت
A_PENDING = "pending"
A_RUNNING = "running"
A_DONE = "done"
A_FAILED = "failed"

# وضعیت کل کار
J_RUNNING = "running"
J_WAITING = "waiting_input"  # منتظر ورودی‌های کارزار از کاربر
J_FINISHED = "finished"
ACTIVE_STATUSES = (J_RUNNING, J_WAITING)

_file_lock = threading.Lock()


# --------------------------------------------------------------------------
# کد کارزار از لینک/کد
# --------------------------------------------------------------------------
def parse_campaign_code(text: str) -> Optional[str]:
    """
    «https://www.karzar.net/344536» یا «karzar.net/344536/» یا «344536» → «344536»
    """
    text = (text or "").strip()
    if not text:
        return None
    # اعداد فارسی/عربی → انگلیسی
    text = text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    if re.fullmatch(r"\d{2,12}", text):
        return text
    m = re.search(r"karzar\.net/(?:campaigns?/)?(\d{2,12})", text)
    if m:
        return m.group(1)
    return None


# --------------------------------------------------------------------------
# فایل وضعیت
# --------------------------------------------------------------------------
def _ensure_dirs():
    os.makedirs(JOBS_DIR, exist_ok=True)
    os.makedirs(SHOTS_DIR, exist_ok=True)


def _job_path(jid: str) -> str:
    return os.path.join(JOBS_DIR, f"{jid}.json")


def screenshot_path(jid: str, acc_id) -> str:
    return os.path.join(SHOTS_DIR, jid, f"{acc_id}.png")


def _save_job(job: dict) -> None:
    _ensure_dirs()
    with _file_lock:
        tmp = _job_path(job["id"]) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(job, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _job_path(job["id"]))


def _read_job(jid: str) -> Optional[dict]:
    if not re.fullmatch(r"[0-9a-f]{32}", jid or ""):
        return None
    path = _job_path(jid)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _pid_alive(pid) -> bool:
    if not pid:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if sys.platform == "win32":
        # روی ویندوز os.kill(pid, 0) فرایند را می‌کُشد؛ پس با OpenProcess بررسی می‌کنیم.
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            still_active = kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value == 259
            return bool(still_active)
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _reconcile(job: dict) -> dict:
    """اگر فرایندِ اجراکنندهٔ کار دیگر زنده نباشد ولی وضعیت «در حال اجرا» مانده باشد، کار را بسته اعلام می‌کند."""
    if job.get("status") not in ACTIVE_STATUSES:
        return job
    started = job.get("started_ts") or 0
    pid = job.get("pid")
    # به فرایند تازه‌شروع‌شده چند ثانیه فرصت بده تا pid خود را بنویسد
    if pid is None and time.time() - started < 30:
        return job
    if _pid_alive(pid):
        return job
    for a in job["accounts"]:
        if a["status"] in (A_PENDING, A_RUNNING):
            a["status"] = A_FAILED
            a["message"] = "فرایند ثبت امضا به‌طور غیرمنتظره متوقف شد."
    job["status"] = J_FINISHED
    job["finished_at"] = datetime.now().strftime("%Y/%m/%d %H:%M")
    _save_job(job)
    _log(job, "signatures.worker_died", f"فرایند ثبت امضای کارزار {job['campaign_code']} به‌طور غیرمنتظره متوقف شد",
         level="error", pid=pid)
    return job


def get_job(jid: str) -> Optional[dict]:
    job = _read_job(jid)
    return _reconcile(job) if job else None


def list_jobs(limit: int = 10) -> list:
    """فهرست کارهای اخیر (جدیدترین اول)."""
    _ensure_dirs()
    jobs = []
    for name in os.listdir(JOBS_DIR):
        if not name.endswith(".json"):
            continue
        job = get_job(name[:-5])
        if job:
            jobs.append(job)
    jobs.sort(key=lambda j: j.get("started_ts", 0), reverse=True)
    return jobs[:limit]


def delete_job(jid: str) -> bool:
    """حذف فایل وضعیت، لاگ و اسکرین‌شات‌های یک کار. کارِ در حال اجرا حذف نمی‌شود."""
    job = get_job(jid)
    if not job or job.get("status") in ACTIVE_STATUSES:
        return False
    with _file_lock:
        for path in (_job_path(jid), os.path.join(JOBS_DIR, f"{jid}.log")):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
    shutil.rmtree(os.path.join(SHOTS_DIR, jid), ignore_errors=True)
    return True


def _update_account(job: dict, acc_id, **fields) -> None:
    for a in job["accounts"]:
        if a["id"] == acc_id:
            a.update(fields)
            break
    _save_job(job)


# --------------------------------------------------------------------------
# ورودی‌های اضافهٔ کارزار (شهر، سن، ...)
# --------------------------------------------------------------------------
def submit_inputs(jid: str, values: dict):
    """
    ثبت مقادیر واردشدهٔ کاربر برای فیلدهای کارزار. فقط وقتی کار در حالت
    waiting_input است پذیرفته می‌شود. خروجی: (ok, error_message).
    """
    job = _read_job(jid)
    if not job:
        return False, "کار پیدا نشد."
    if job.get("status") != J_WAITING:
        return False, "این کار منتظر ورودی نیست."
    if not isinstance(values, dict):
        return False, "فرمت ورودی‌ها معتبر نیست."

    clean = {}
    for f in job.get("input_fields") or []:
        name = f["name"]
        raw = values.get(name, "")
        if isinstance(raw, bool):
            raw = "1" if raw else ""
        val = str(raw if raw is not None else "").strip()
        if f.get("required") and not val:
            return False, f"فیلد «{f.get('label') or name}» الزامی است."
        maxlen = f.get("maxlength")
        if maxlen and len(val) > int(maxlen):
            return False, f"فیلد «{f.get('label') or name}» حداکثر {maxlen} کاراکتر است."
        opts = f.get("options")
        if opts and val and val not in {str(o["value"]) for o in opts}:
            return False, f"مقدار انتخاب‌شده برای «{f.get('label') or name}» معتبر نیست."
        clean[name] = val

    job["inputs"] = clean
    job["inputs_cancelled"] = False
    job["status"] = J_RUNNING
    _save_job(job)
    return True, None


def cancel_inputs(jid: str) -> bool:
    """لغو ورود اطلاعات؛ اکانت‌های باقی‌مانده ناموفق می‌شوند."""
    job = _read_job(jid)
    if not job or job.get("status") != J_WAITING:
        return False
    job["inputs_cancelled"] = True
    job["status"] = J_RUNNING
    _save_job(job)
    return True


# فهرست فیلدهای قابل‌پرکردن در مرحلهٔ فعال ویزارد (به‌جز توکن/کپچا/کد تأیید/فایل)
_COLLECT_FIELDS_JS = """
([itemSel, skipIds]) => {
    const item = document.querySelector(itemSel);
    if (!item) return [];
    const skip = new Set(skipIds);
    const isVis = (el) => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
    };
    const txt = (el) => (el ? (el.textContent || '').replace(/\\s+/g, ' ').trim() : '');
    const groupLabel = (el) => {
        const grp = el.closest('.form-group, .signup-wizard-item__group, fieldset, div');
        const lab = grp && grp.querySelector('label.signup-wizard-item__label, legend, label');
        return txt(lab);
    };
    const fields = [];
    item.querySelectorAll('input, select, textarea').forEach((el) => {
        const tag = el.tagName.toLowerCase();
        const type = tag === 'input' ? (el.getAttribute('type') || 'text').toLowerCase() : tag;
        if (['hidden', 'file', 'submit', 'button', 'image', 'reset'].includes(type)) return;
        if (skip.has(el.id) || skip.has(el.name)) return;
        const name = el.name || el.id;
        if (!name) return;
        const ownLabel = el.id ? item.querySelector('label[for="' + el.id + '"]') : null;

        if (type === 'radio' || type === 'checkbox') {
            if (!isVis(el) && !isVis(ownLabel)) return;
            let f = fields.find((x) => x.name === name && x.type === type);
            if (!f) {
                f = { name, id: el.id || '', type, label: groupLabel(el) || name, required: el.required, options: [] };
                fields.push(f);
            }
            f.options.push({ value: el.value, label: txt(ownLabel) || el.value });
            return;
        }
        if (!isVis(el)) return;
        const rules = el.getAttribute('data-rules') || '';
        const f = {
            name, id: el.id || '', type,
            label: txt(ownLabel) || groupLabel(el) || el.placeholder || name,
            placeholder: el.placeholder || '',
            maxlength: el.maxLength > 0 ? el.maxLength : null,
            required: el.required || /(^|\\|)required(\\||$)/.test(rules),
            value: el.value || '',
        };
        if (tag === 'select') {
            f.options = Array.from(el.options).map((o) => ({ value: o.value, label: txt(o) }));
        }
        fields.push(f);
    });
    return fields;
}
"""


async def _collect_fields(page) -> list:
    try:
        fields = await page.evaluate(_COLLECT_FIELDS_JS, [SEL_ACTIVE_ITEM, list(AUTH_FIELD_IDS)])
    except Exception:
        return []
    return [f for f in (fields or []) if isinstance(f, dict) and f.get("name")]


async def _fill_fields(page, fields: list, inputs: dict) -> None:
    for f in fields:
        name, ftype = f["name"], f.get("type")
        val = (inputs or {}).get(name)
        if val is None or val == "":
            continue
        base = f'{SEL_ACTIVE_ITEM} [name="{name}"]'
        try:
            if ftype == "select":
                await page.select_option(base, value=str(val))
            elif ftype == "radio":
                await page.check(f'{base}[value="{val}"]', force=True)
            elif ftype == "checkbox":
                opts = f.get("options") or []
                if len(opts) <= 1:
                    await page.set_checked(base, str(val) not in ("", "0", "false"), force=True)
                else:
                    for v in str(val).split(","):
                        v = v.strip()
                        if v:
                            await page.check(f'{base}[value="{v}"]', force=True)
            else:
                await page.locator(base).first.fill(str(val))
        except Exception as e:
            raise RuntimeError(f"جایگذاری «{f.get('label') or name}» ناموفق بود: {e}")


async def _active_step_index(page) -> int:
    try:
        return await page.evaluate(
            "(sel) => Array.from(document.querySelectorAll(sel)).findIndex(e => e.classList.contains('active'))",
            SEL_ALL_ITEMS,
        )
    except Exception:
        return -1


async def _wait_step_outcome(page, prev_idx: int, timeout: float = 60) -> str:
    """
    بعد از کلیک «مرحله‌ی بعد»: 'success' اگر پیغام موفقیت آمد، 'next' اگر مرحلهٔ
    دیگری از ویزارد فعال شد؛ در صورت خطا/کد تأیید استثنا می‌دهد.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        txt = await _visible_text(page, SEL_SUCCESS)
        if txt and SUCCESS_TEXT in txt:
            return "success"
        if await _is_visible(page, SEL_OTP_INPUT):
            raise RuntimeError("کارزار کد تأیید پیامکی خواست؛ نشست این اکانت معتبر نیست. اکانت را دوباره وارد کنید.")
        for el in await page.query_selector_all(SEL_WIZARD_ERROR):
            try:
                if await el.is_visible():
                    err = (await el.inner_text()).strip()
                    if err:
                        raise RuntimeError(f"کارزار: {err}")
            except RuntimeError:
                raise
            except Exception:
                pass
        idx = await _active_step_index(page)
        if idx != -1 and idx != prev_idx:
            await asyncio.sleep(0.8)
            return "next"
        await asyncio.sleep(0.5)
    raise RuntimeError("پیغام موفقیت ثبت امضا نمایش داده نشد.")


async def _wait_for_inputs(job: dict, acc_id, fields: list) -> dict:
    """کار را به حالت waiting_input می‌برد و تا ثبت مقادیر توسط کاربر (از طریق سامانه) منتظر می‌ماند."""
    job["status"] = J_WAITING
    job["input_fields"] = fields
    job["inputs"] = None
    job["inputs_cancelled"] = False
    job["waiting_since"] = time.time()
    _update_account(job, acc_id, message="در انتظار ورود اطلاعات موردنیاز کارزار...")
    _log(job, "signatures.inputs_requested",
         f"کارزار {job['campaign_code']} {len(fields)} ورودی اضافه می‌خواهد",
         fields=[{"name": f["name"], "label": f.get("label"), "type": f.get("type")} for f in fields])

    deadline = time.monotonic() + INPUT_WAIT_TIMEOUT
    while time.monotonic() < deadline:
        cur = _read_job(job["id"]) or {}
        if cur.get("inputs_cancelled"):
            job["inputs_cancelled"] = True
            job["status"] = J_RUNNING
            _save_job(job)
            raise RuntimeError("ورود اطلاعات کارزار توسط کاربر لغو شد.")
        if cur.get("inputs") is not None:
            job["inputs"] = cur["inputs"]
            job["status"] = J_RUNNING
            job["waiting_since"] = None
            _save_job(job)
            _log(job, "signatures.inputs_received",
                 f"ورودی‌های کارزار {job['campaign_code']} ثبت شد ({len(job['inputs'])} فیلد)")
            return job["inputs"]
        await asyncio.sleep(1)

    job["inputs_cancelled"] = True
    job["status"] = J_RUNNING
    _save_job(job)
    raise RuntimeError("ورودی‌های کارزار در زمان مقرر وارد نشد.")


# --------------------------------------------------------------------------
# امضای یک اکانت
# --------------------------------------------------------------------------
async def _sign_one(p, job: dict, acc: dict) -> None:
    jid, acc_id = job["id"], acc["id"]
    code = job["campaign_code"]
    url = KARZAR_CAMPAIGN_URL.format(code=code)
    user_data_dir = acc["user_data_dir"]

    if not user_data_dir or not os.path.isdir(user_data_dir):
        _update_account(job, acc_id, status=A_FAILED, message="پروفایل این اکانت پیدا نشد؛ اکانت را دوباره اضافه کنید.")
        return

    _update_account(job, acc_id, status=A_RUNNING, message="در حال باز کردن مرورگر...")
    ctx = None
    try:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=True,
            args=_CHROME_ARGS,
            user_agent=_USER_AGENT,
            viewport={"width": 1280, "height": 900},
            locale="fa-IR",
            timezone_id="Asia/Tehran",
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        _update_account(job, acc_id, message="در حال رفتن به صفحهٔ کارزار...")
        await _goto(page, url)

        if "/404" in page.url or (await page.title()).strip() in ("", "404"):
            raise RuntimeError("کارزاری با این کد پیدا نشد.")

        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass

        # ── دکمهٔ «ثبت امضا» ────────────────────────────────────────────
        _update_account(job, acc_id, message="در حال کلیک روی «ثبت امضا»...")
        sign_btn = await page.query_selector(SEL_SIGN_BTN)
        if not (sign_btn and await sign_btn.is_visible()):
            sign_btn = await page.query_selector(SEL_SIGN_BTN_ALT)
        if not (sign_btn and await sign_btn.is_visible()):
            note = await _visible_text(page, SEL_SIGN_WRAPPER)
            raise RuntimeError(note or "دکمهٔ «ثبت امضا» در صفحه پیدا نشد (احتمالاً قبلاً امضا شده یا کارزار بسته است).")
        await sign_btn.scroll_into_view_if_needed()
        await sign_btn.click()

        await page.wait_for_selector(SEL_SHEET, state="visible", timeout=20_000)
        await asyncio.sleep(1)

        # اگر مرحلهٔ کد تأیید یا کپچا نمایش داده شود، یعنی اکانت لاگین نیست
        if await _is_visible(page, SEL_CAPTCHA):
            raise RuntimeError("کارزار کد امنیتی خواست؛ نشست این اکانت معتبر نیست. اکانت را دوباره وارد کنید.")

        # ── مراحل ویزارد: (پر کردن ورودی‌های کارزار) → «مرحله‌ی بعد» → تا پیغام موفقیت ──
        success = False
        for _step in range(MAX_WIZARD_STEPS):
            fields = await _collect_fields(page)
            if fields:
                inputs = job.get("inputs")
                if inputs is None:
                    inputs = await _wait_for_inputs(job, acc_id, fields)
                _update_account(job, acc_id, message="در حال جایگذاری ورودی‌های کارزار...")
                await _fill_fields(page, fields, inputs)

            _update_account(job, acc_id, message="در حال کلیک روی «مرحله‌ی بعد»...")
            step_idx = await _active_step_index(page)
            next_btn = await page.wait_for_selector(SEL_NEXT_BTN, state="visible", timeout=15_000)
            await next_btn.click()

            _update_account(job, acc_id, message="در انتظار تأیید ثبت امضا...")
            outcome = await _wait_step_outcome(page, step_idx)
            if outcome == "success":
                success = True
                break
            # مرحلهٔ بعدی ویزارد نمایش داده شد؛ دوباره فیلدها را بررسی می‌کنیم

        if not success:
            raise RuntimeError("پیغام موفقیت ثبت امضا نمایش داده نشد.")

        # ── اسکرین‌شات ────────────────────────────────────────────────
        await asyncio.sleep(1)
        shot = screenshot_path(jid, acc_id)
        os.makedirs(os.path.dirname(shot), exist_ok=True)
        sheet = await page.query_selector(SEL_SHEET)
        try:
            await sheet.screenshot(path=shot)
        except Exception:
            await page.screenshot(path=shot)

        _update_account(
            job, acc_id,
            status=A_DONE,
            message="امضا با موفقیت ثبت شد.",
            screenshot=True,
            finished_at=datetime.now().strftime("%Y/%m/%d %H:%M"),
        )

    except Exception as exc:
        msg = str(exc)
        if isinstance(exc, PwTimeout):
            msg = "زمان انتظار به پایان رسید (عنصر مورد نظر در صفحه پیدا نشد)."
        # اسکرین‌شات خطا برای عیب‌یابی
        try:
            if ctx and ctx.pages:
                shot = screenshot_path(jid, acc_id)
                os.makedirs(os.path.dirname(shot), exist_ok=True)
                await ctx.pages[0].screenshot(path=shot)
                _update_account(job, acc_id, screenshot=True)
        except Exception:
            pass
        _update_account(job, acc_id, status=A_FAILED, message=msg)
    finally:
        if ctx:
            try:
                await ctx.close()
            except Exception:
                pass


async def _is_visible(page, selector: str) -> bool:
    try:
        el = await page.query_selector(selector)
        return bool(el and await el.is_visible())
    except Exception:
        return False


def _log(job: dict, action: str, message: str, level: str = "info", **details) -> None:
    """ثبت رویداد کار در گزارش فعالیت‌ها (از داخل فرایند مستقل)."""
    try:
        from activity_log import log_event

        log_event(
            action, message, level=level, category="signatures",
            actor=job.get("actor") or None,
            details={"jid": job["id"], "campaign_code": job["campaign_code"], **details},
        )
    except Exception as exc:  # لاگ نباید کار را متوقف کند
        print(f"activity log failed: {exc}", file=sys.stderr)


async def _run_job(job: dict) -> None:
    t0 = time.monotonic()
    _log(job, "signatures.worker_start", f"فرایند ثبت امضای کارزار {job['campaign_code']} آغاز شد",
         pid=os.getpid(), accounts=len(job["accounts"]))
    try:
        async with async_playwright() as p:
            for acc in job["accounts"]:
                if acc["status"] != A_PENDING:
                    continue
                if job.get("inputs_cancelled"):
                    _update_account(job, acc["id"], status=A_FAILED,
                                    message="ورود اطلاعات کارزار لغو شد.",
                                    finished_at=datetime.now().strftime("%H:%M:%S"))
                    continue
                t1 = time.monotonic()
                await _sign_one(p, job, acc)
                ok = acc["status"] == A_DONE
                _log(job,
                     "signatures.account_done" if ok else "signatures.account_failed",
                     f"اکانت «{acc['name']}»: {acc.get('message')}",
                     level="info" if ok else "error",
                     account_id=acc["id"], account_name=acc["name"], identifier=acc.get("identifier"),
                     result=acc["status"], duration_s=round(time.monotonic() - t1, 1))
    except Exception as exc:
        for a in job["accounts"]:
            if a["status"] in (A_PENDING, A_RUNNING):
                a["status"] = A_FAILED
                a["message"] = str(exc)
        _log(job, "signatures.worker_crash", f"خطای کلی در فرایند ثبت امضا: {exc}", level="error", error=str(exc)[:500])
    job["status"] = J_FINISHED
    job["finished_at"] = datetime.now().strftime("%Y/%m/%d %H:%M")
    _save_job(job)
    done = sum(1 for a in job["accounts"] if a["status"] == A_DONE)
    failed = len(job["accounts"]) - done
    _log(job, "signatures.job_finished",
         f"ثبت امضای کارزار {job['campaign_code']} پایان یافت: {done} موفق، {failed} ناموفق",
         level="info" if failed == 0 else "warning",
         done=done, failed=failed, duration_s=round(time.monotonic() - t0, 1))


# --------------------------------------------------------------------------
# API عمومی
# --------------------------------------------------------------------------
def campaign_url(campaign_code: str) -> str:
    return KARZAR_CAMPAIGN_URL.format(code=campaign_code)


def start_job(campaign_code: str, accounts: list, actor: Optional[dict] = None) -> str:
    """
    شروع ثبت امضای نوبتی برای فهرست اکانت‌ها. شناسهٔ job را برمی‌گرداند.
    accounts: لیست دیکشنری‌های اکانت از data/accounts.json
    actor: کاربرِ درخواست‌دهنده (برای گزارش فعالیت‌ها)
    """
    jid = uuid.uuid4().hex
    job = {
        "id": jid,
        "campaign_code": campaign_code,
        "campaign_url": campaign_url(campaign_code),
        "status": J_RUNNING,
        "pid": None,
        "actor": actor or {},
        "started_ts": time.time(),
        "created_at": datetime.now().strftime("%Y/%m/%d %H:%M"),
        "finished_at": None,
        "input_fields": [],
        "inputs": None,
        "inputs_cancelled": False,
        "waiting_since": None,
        "accounts": [
            {
                "id": a["id"],
                "name": a["name"],
                "identifier": a.get("identifier", ""),
                "user_data_dir": a.get("user_data_dir", ""),
                "status": A_PENDING,
                "message": "در صف انتظار",
                "screenshot": False,
                "finished_at": None,
            }
            for a in accounts
        ],
    }
    _save_job(job)
    _spawn_worker(jid)
    return jid


def _spawn_worker(jid: str) -> None:
    """اجرای کار در یک فرایندِ جدا و مستقل از وب‌سرور (بدون وابستگی به درخواست HTTP یا تب مرورگر)."""
    if sys.platform == "win32":
        detach = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        detach = {"start_new_session": True}
    log = open(os.path.join(JOBS_DIR, f"{jid}.log"), "ab")
    try:
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--run", jid],
            cwd=BASE_DIR,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            close_fds=True,
            **detach,
        )
    finally:
        log.close()


def _worker_main(jid: str) -> int:
    job = _read_job(jid)
    if not job:
        print(f"job {jid} not found", file=sys.stderr)
        return 1
    job["pid"] = os.getpid()
    _save_job(job)
    asyncio.run(_run_job(job))
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--run":
        sys.exit(_worker_main(sys.argv[2]))
    print("usage: python karzar_sign.py --run <jid>", file=sys.stderr)
    sys.exit(2)
