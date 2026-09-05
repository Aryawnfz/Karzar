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
"""

import asyncio
import json
import os
import re
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

SUCCESS_TEXT = "با موفقیت"

# وضعیت هر اکانت
A_PENDING = "pending"
A_RUNNING = "running"
A_DONE = "done"
A_FAILED = "failed"

# وضعیت کل کار
J_RUNNING = "running"
J_FINISHED = "finished"

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
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    return True


def _reconcile(job: dict) -> dict:
    """اگر فرایندِ اجراکنندهٔ کار دیگر زنده نباشد ولی وضعیت «در حال اجرا» مانده باشد، کار را بسته اعلام می‌کند."""
    if job.get("status") != J_RUNNING:
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


def _update_account(job: dict, acc_id, **fields) -> None:
    for a in job["accounts"]:
        if a["id"] == acc_id:
            a.update(fields)
            break
    _save_job(job)


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

        # ── دکمهٔ «مرحله‌ی بعد» ────────────────────────────────────────
        _update_account(job, acc_id, message="در حال کلیک روی «مرحله‌ی بعد»...")
        next_btn = await page.wait_for_selector(SEL_NEXT_BTN, state="visible", timeout=15_000)
        await next_btn.click()

        # ── انتظار برای پیغام موفقیت ──────────────────────────────────
        _update_account(job, acc_id, message="در انتظار تأیید ثبت امضا...")
        deadline = time.monotonic() + 60
        success = False
        while time.monotonic() < deadline:
            txt = await _visible_text(page, SEL_SUCCESS)
            if txt and SUCCESS_TEXT in txt:
                success = True
                break
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
            await asyncio.sleep(0.5)

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


async def _run_job(job: dict) -> None:
    try:
        async with async_playwright() as p:
            for acc in job["accounts"]:
                if acc["status"] == A_PENDING:
                    await _sign_one(p, job, acc)
    except Exception as exc:
        for a in job["accounts"]:
            if a["status"] in (A_PENDING, A_RUNNING):
                a["status"] = A_FAILED
                a["message"] = str(exc)
    job["status"] = J_FINISHED
    job["finished_at"] = datetime.now().strftime("%Y/%m/%d %H:%M")
    _save_job(job)


# --------------------------------------------------------------------------
# API عمومی
# --------------------------------------------------------------------------
def start_job(campaign_code: str, accounts: list) -> str:
    """
    شروع ثبت امضای نوبتی برای فهرست اکانت‌ها. شناسهٔ job را برمی‌گرداند.
    accounts: لیست دیکشنری‌های اکانت از data/accounts.json
    """
    jid = uuid.uuid4().hex
    job = {
        "id": jid,
        "campaign_code": campaign_code,
        "campaign_url": KARZAR_CAMPAIGN_URL.format(code=campaign_code),
        "status": J_RUNNING,
        "pid": None,
        "started_ts": time.time(),
        "created_at": datetime.now().strftime("%Y/%m/%d %H:%M"),
        "finished_at": None,
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
    log = open(os.path.join(JOBS_DIR, f"{jid}.log"), "ab")
    try:
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--run", jid],
            cwd=BASE_DIR,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
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
