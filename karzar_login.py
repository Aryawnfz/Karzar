# -*- coding: utf-8 -*-
"""
ورود به سامانهٔ کارزار (karzar.net) با Playwright و ذخیرهٔ پروفایل کروم هر اکانت.

وضعیت هر فرایندِ ورود روی دیسک نگهداری می‌شود تا با Gunicorn چند-worker هم کار کند:
  data/login_sessions/<sid>.json  ← وضعیت (status / message / error)
  data/login_sessions/<sid>.otp   ← کدی که کاربر وارد کرده

پروفایل کروم هر اکانت در data/profiles/<slug> ذخیره می‌شود و بعداً با همان
پروفایل (بدون لاگین مجدد) می‌توان وارد کارزار شد.
"""

import asyncio
import json
import os
import re
import threading
import time
import uuid
from typing import Optional

from playwright.async_api import async_playwright, TimeoutError as PwTimeout

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
PROFILES_DIR = os.path.join(DATA_DIR, "profiles")
SESSIONS_DIR = os.path.join(DATA_DIR, "login_sessions")

KARZAR_URL = "https://www.karzar.net/"
KARZAR_PANEL_URL = "https://www.karzar.net/panel"

# انتخابگرهای صفحهٔ ورود کارزار (مودال «ورود / ثبت نام»)
SEL_LOGIN_LINK = "#login-link, a:has-text('پنل کاربری')"
SEL_LOGIN_MODAL = "#login-modal"
SEL_IDENTIFIER = "#email_or_mobile"
SEL_OTP_BTN = "#otp-btn"
SEL_OTP_GROUP = "#otp-code"
SEL_CODE = "#code"
SEL_LOGIN_BTN = "#login-btn"
SEL_MOBILE_ERROR = ".otp-email .help-block"
SEL_CODE_ERROR = "#otp-code .help-block"

ST_STARTING = "starting"
ST_SENDING = "sending_code"
ST_WAIT_OTP = "waiting_otp"
ST_VERIFYING = "verifying"
ST_SUCCESS = "success"
ST_ERROR = "error"
ST_TIMEOUT = "timeout"
ST_CANCELLED = "cancelled"

OTP_WAIT_SECONDS = 300

_CHROME_ARGS = [
    "--no-sandbox",
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--disable-infobars",
    "--lang=fa-IR",
]
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# --------------------------------------------------------------------------
# فایل‌های وضعیت
# --------------------------------------------------------------------------
def _ensure_dirs():
    os.makedirs(PROFILES_DIR, exist_ok=True)
    os.makedirs(SESSIONS_DIR, exist_ok=True)


def _state_path(sid: str) -> str:
    return os.path.join(SESSIONS_DIR, f"{sid}.json")


def _otp_path(sid: str) -> str:
    return os.path.join(SESSIONS_DIR, f"{sid}.otp")


def _cancel_path(sid: str) -> str:
    return os.path.join(SESSIONS_DIR, f"{sid}.cancel")


def _write_state(sid: str, status: str, message: str = "", error: str = ""):
    _ensure_dirs()
    tmp = _state_path(sid) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"status": status, "message": message, "error": error}, f, ensure_ascii=False)
    os.replace(tmp, _state_path(sid))


def get_status(sid: str) -> dict:
    path = _state_path(sid)
    if not os.path.exists(path):
        return {"status": "not_found", "message": "", "error": ""}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"status": "not_found", "message": "", "error": ""}


def submit_otp(sid: str, code: str) -> bool:
    _ensure_dirs()
    try:
        with open(_otp_path(sid), "w", encoding="utf-8") as f:
            f.write(code.strip())
        return True
    except Exception:
        return False


def cancel(sid: str) -> None:
    _ensure_dirs()
    with open(_cancel_path(sid), "w") as f:
        f.write("1")


def cleanup(sid: str) -> None:
    for path in (_state_path(sid), _otp_path(sid), _cancel_path(sid)):
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass


def slugify(name: str) -> str:
    slug = re.sub(r"[^\w\-]+", "_", name.strip(), flags=re.UNICODE).strip("_")
    return slug or "account"


def profile_dir_for(name: str) -> str:
    _ensure_dirs()
    base = slugify(name)
    path = os.path.join(PROFILES_DIR, base)
    n = 2
    while os.path.exists(path):
        path = os.path.join(PROFILES_DIR, f"{base}_{n}")
        n += 1
    return path


# --------------------------------------------------------------------------
# event loop پس‌زمینه (per-process)
# --------------------------------------------------------------------------
_loop: Optional[asyncio.AbstractEventLoop] = None
_bg_thread: Optional[threading.Thread] = None
_loop_lock = threading.Lock()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop, _bg_thread
    with _loop_lock:
        if _loop is None or _bg_thread is None or not _bg_thread.is_alive():
            _loop = asyncio.new_event_loop()
            _bg_thread = threading.Thread(target=_loop.run_forever, daemon=True, name="karzar-login-loop")
            _bg_thread.start()
            time.sleep(0.1)
    return _loop


# --------------------------------------------------------------------------
# Playwright
# --------------------------------------------------------------------------
async def _visible_text(page, selector: str) -> str:
    try:
        el = await page.query_selector(selector)
        if el and await el.is_visible():
            return (await el.inner_text()).strip()
    except Exception:
        pass
    return ""


async def _goto(page, url: str, attempts: int = 3) -> None:
    last_exc = None
    for i in range(attempts):
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            if resp is None or resp.status < 500:
                return
            last_exc = RuntimeError(f"کارزار پاسخ {resp.status} داد.")
        except PwTimeout as exc:
            last_exc = exc
        await asyncio.sleep(2 * (i + 1))
    raise RuntimeError(f"اتصال به کارزار برقرار نشد ({last_exc}).")


async def _wait_otp(sid: str) -> Optional[str]:
    otp_file = _otp_path(sid)
    for _ in range(OTP_WAIT_SECONDS):
        if os.path.exists(_cancel_path(sid)):
            return None
        if os.path.exists(otp_file):
            try:
                with open(otp_file, "r", encoding="utf-8") as f:
                    code = f.read().strip()
                os.remove(otp_file)
            except Exception:
                code = ""
            if code:
                return code
        await asyncio.sleep(1)
    return None


async def _do_login(sid: str, identifier: str, user_data_dir: str, on_success) -> None:
    os.makedirs(user_data_dir, exist_ok=True)
    _write_state(sid, ST_STARTING, "در حال باز کردن کارزار...")

    ctx = None
    try:
        async with async_playwright() as p:
            ctx = await p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                headless=True,
                args=_CHROME_ARGS,
                user_agent=_USER_AGENT,
                viewport={"width": 1280, "height": 800},
                locale="fa-IR",
                timezone_id="Asia/Tehran",
            )
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await _goto(page, KARZAR_URL)

            # ── کلیک روی «پنل کاربری» ─────────────────────────────────
            _write_state(sid, ST_SENDING, "در حال باز کردن فرم ورود...")
            try:
                await page.locator(SEL_LOGIN_LINK).first.click(timeout=20_000)
            except PwTimeout:
                # لینک ورود نیست ← احتمالاً این پروفایل از قبل لاگین است
                await _goto(page, KARZAR_PANEL_URL)
                if "/panel" in page.url:
                    await ctx.close()
                    ctx = None
                    on_success()
                    _write_state(sid, ST_SUCCESS, "این پروفایل از قبل وارد کارزار بود.")
                    return
                _write_state(sid, ST_ERROR, error="دکمهٔ «پنل کاربری» در سایت کارزار پیدا نشد.")
                await ctx.close()
                return

            ident_el = await page.wait_for_selector(SEL_IDENTIFIER, state="visible", timeout=15_000)

            # ── وارد کردن شماره/ایمیل و درخواست کد ────────────────────
            _write_state(sid, ST_SENDING, "در حال ارسال کد ورود...")
            await ident_el.click()
            await ident_el.fill(identifier)
            await asyncio.sleep(0.3)
            await page.click(SEL_OTP_BTN, timeout=10_000)

            # منتظر ظاهر شدن فیلد کد یا خطا
            code_visible = False
            for _ in range(40):
                try:
                    grp = await page.query_selector(SEL_OTP_GROUP)
                    if grp and await grp.is_visible():
                        code_visible = True
                        break
                except Exception:
                    pass
                err = await _visible_text(page, SEL_MOBILE_ERROR)
                if err:
                    _write_state(sid, ST_ERROR, error=f"کارزار: {err}")
                    await ctx.close()
                    return
                await asyncio.sleep(0.5)

            if not code_visible:
                _write_state(sid, ST_ERROR, error="کد ورود ارسال نشد (پاسخی از کارزار دریافت نشد).")
                await ctx.close()
                return

            # ── منتظر کد از کاربر ────────────────────────────────────
            _write_state(sid, ST_WAIT_OTP, "کد ورود ارسال شد. کد دریافتی را وارد کنید.")
            code = await _wait_otp(sid)
            if not code:
                if os.path.exists(_cancel_path(sid)):
                    _write_state(sid, ST_CANCELLED, error="فرایند ورود لغو شد.")
                else:
                    _write_state(sid, ST_TIMEOUT, error="زمان وارد کردن کد به پایان رسید.")
                await ctx.close()
                return

            # ── وارد کردن کد و ورود ──────────────────────────────────
            _write_state(sid, ST_VERIFYING, "در حال تأیید کد...")
            code_el = await page.wait_for_selector(SEL_CODE, state="visible", timeout=10_000)
            await code_el.click()
            await code_el.fill(code)
            await asyncio.sleep(0.3)
            await page.click(SEL_LOGIN_BTN, timeout=10_000)

            logged_in = False
            for _ in range(60):
                if "/panel" in page.url:
                    logged_in = True
                    break
                err = await _visible_text(page, SEL_CODE_ERROR)
                if err:
                    _write_state(sid, ST_ERROR, error=f"کارزار: {err}")
                    await ctx.close()
                    return
                await asyncio.sleep(0.5)

            if not logged_in:
                _write_state(sid, ST_ERROR, error="کد نادرست بود یا ورود انجام نشد.")
                await ctx.close()
                return

            # ── ذخیرهٔ پروفایل ───────────────────────────────────────
            _write_state(sid, ST_VERIFYING, "در حال ذخیرهٔ پروفایل...")
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass
            await asyncio.sleep(2)
            try:
                await ctx.storage_state(path=os.path.join(user_data_dir, "storage_state.json"))
            except Exception:
                pass
            await ctx.close()
            ctx = None

            on_success()
            _write_state(sid, ST_SUCCESS, "ورود موفق! اکانت کارزار ذخیره شد.")

    except Exception as exc:
        _write_state(sid, ST_ERROR, error=str(exc))
    finally:
        if ctx:
            try:
                await ctx.close()
            except Exception:
                pass


# --------------------------------------------------------------------------
# API عمومی
# --------------------------------------------------------------------------
def start_login(identifier: str, user_data_dir: str, on_success) -> str:
    """
    شروع فرایند ورود در پس‌زمینه. شناسهٔ session را برمی‌گرداند.
    on_success بعد از ورودِ موفق (قبل از ثبت وضعیت success) صدا زده می‌شود.
    """
    sid = uuid.uuid4().hex
    _write_state(sid, ST_STARTING, "در حال راه‌اندازی...")
    loop = _ensure_loop()
    asyncio.run_coroutine_threadsafe(_do_login(sid, identifier, user_data_dir, on_success), loop)
    return sid
