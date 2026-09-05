# -*- coding: utf-8 -*-
"""
سامانه مدیریت داخلی
--------------------
اپلیکیشن Flask با ذخیره‌سازی مبتنی بر JSON، بدون دیتابیس.
آماده اجرا با: gunicorn app:app
"""

import json
import os
import shutil
import threading
import time
import uuid
from datetime import datetime
from functools import wraps

from flask import (
    Flask,
    Response,
    g,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    jsonify,
    send_file,
    abort,
)
from werkzeug.exceptions import HTTPException
from werkzeug.security import generate_password_hash, check_password_hash

import activity_log
import karzar_login
import karzar_sign
from activity_log import log_event

# --------------------------------------------------------------------------
# پیکربندی پایه
# --------------------------------------------------------------------------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
ACCOUNTS_FILE = os.path.join(DATA_DIR, "accounts.json")
_accounts_lock = threading.Lock()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-secret-key-in-production")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


# --------------------------------------------------------------------------
# توابع کمکی برای کار با فایل‌های JSON
# --------------------------------------------------------------------------
def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def load_json(path, default):
    _ensure_data_dir()
    if not os.path.exists(path):
        save_json(path, default)
        return default
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return default


def save_json(path, data):
    _ensure_data_dir()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_users_data():
    return load_json(USERS_FILE, {"next_id": 1, "users": []})


def get_accounts_data():
    return load_json(ACCOUNTS_FILE, {"next_id": 1, "accounts": []})


def find_user_by_username(username):
    data = get_users_data()
    for u in data["users"]:
        if u["username"].strip().lower() == username.strip().lower():
            return u
    return None


def find_user_by_id(user_id):
    data = get_users_data()
    for u in data["users"]:
        if u["id"] == user_id:
            return u
    return None


# --------------------------------------------------------------------------
# دکوراتورهای احراز هویت
# --------------------------------------------------------------------------
def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)

    return wrapped


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login", next=request.path))
        if session.get("role") != "admin":
            log_event("auth.forbidden", f"تلاش برای دسترسی به بخش مدیریتی {request.path}", level="security", category="auth")
            flash("شما دسترسی لازم برای مشاهده این صفحه را ندارید.", "error")
            return redirect(url_for("dashboard"))
        return view_func(*args, **kwargs)

    return wrapped


# --------------------------------------------------------------------------
# ثبت خودکار همهٔ درخواست‌ها در گزارش فعالیت‌ها
# --------------------------------------------------------------------------
@app.before_request
def _activity_before():
    g.request_id = uuid.uuid4().hex[:12]
    g.request_started = time.perf_counter()


@app.after_request
def _activity_after(response):
    started = g.get("request_started")
    duration = (time.perf_counter() - started) * 1000 if started else None
    endpoint = request.endpoint or ""
    code = response.status_code
    if code >= 500:
        level = "error"
    elif code in (401, 403):
        level = "security"
    elif code >= 400:
        level = "warning"
    elif endpoint in activity_log.NOISY_ENDPOINTS or request.method in ("GET", "HEAD"):
        level = "debug"
    else:
        level = "info"
    details = {
        "content_length": response.calculate_content_length(),
        "referrer": request.referrer,
        "query": request.args.to_dict(flat=False) if request.args else {},
    }
    if request.view_args:
        details["view_args"] = request.view_args
    if request.method not in ("GET", "HEAD") and endpoint != "static":
        body = request.get_json(silent=True)
        keys = list(request.form.keys()) + (list(body.keys()) if isinstance(body, dict) else [])
        details["form_fields"] = sorted(k for k in keys if k not in ("password", "code"))
    try:
        log_event(
            f"http.{request.method.lower()}",
            f"{request.method} {request.path} → {code}",
            level=level,
            category="http",
            status_code=code,
            duration_ms=duration,
            details=details,
        )
    except Exception:  # لاگ نباید پاسخ را خراب کند
        app.logger.exception("activity log failed")
    response.headers["X-Request-ID"] = g.get("request_id", "")
    return response


@app.errorhandler(Exception)
def _unhandled_error(e):
    if isinstance(e, HTTPException):
        return e
    log_event(
        "system.exception",
        f"خطای پیش‌بینی‌نشده: {type(e).__name__}: {e}",
        level="error",
        category="system",
        details={"exception": type(e).__name__, "detail": str(e)[:500]},
    )
    raise e


@app.context_processor
def inject_globals():
    return {
        "current_user": {
            "id": session.get("user_id"),
            "username": session.get("username"),
            "full_name": session.get("full_name"),
            "role": session.get("role"),
        },
        "now": datetime.now(),
    }


# --------------------------------------------------------------------------
# مسیرها: احراز هویت
# --------------------------------------------------------------------------
@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = find_user_by_username(username)
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["full_name"] = user["full_name"]
            session["role"] = user["role"]
            session.permanent = True
            log_event("auth.login_success", f"ورود موفق «{user['full_name']}»", level="security", category="auth",
                      details={"next": request.args.get("next")})
            flash(f"خوش آمدید، {user['full_name']}", "success")
            next_page = request.args.get("next")
            return redirect(next_page or url_for("dashboard"))

        log_event("auth.login_failed", f"ورود ناموفق با نام کاربری «{username}»", level="security", category="auth",
                  details={"username": username, "user_exists": bool(user)})
        flash("نام کاربری یا رمز عبور اشتباه است.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    if "user_id" in session:
        log_event("auth.logout", f"خروج «{session.get('full_name')}»", level="security", category="auth")
    session.clear()
    flash("با موفقیت از سیستم خارج شدید.", "success")
    return redirect(url_for("login"))


# --------------------------------------------------------------------------
# مسیرها: داشبورد
# --------------------------------------------------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    users_data = get_users_data()
    accounts_data = get_accounts_data()

    total_users = len(users_data["users"])
    total_admins = len([u for u in users_data["users"] if u["role"] == "admin"])
    total_accounts = len(accounts_data["accounts"])
    active_accounts = len([a for a in accounts_data["accounts"] if a.get("status") == "active"])

    stats = {
        "total_users": total_users,
        "total_admins": total_admins,
        "total_regular": total_users - total_admins,
        "total_accounts": total_accounts,
        "active_accounts": active_accounts,
    }
    recent_users = sorted(users_data["users"], key=lambda u: u["id"], reverse=True)[:5]

    return render_template("dashboard.html", stats=stats, recent_users=recent_users)


# --------------------------------------------------------------------------
# مسیرها: مدیریت کاربران (فقط ادمین)
# --------------------------------------------------------------------------
@app.route("/users")
@admin_required
def users_list():
    data = get_users_data()
    users = sorted(data["users"], key=lambda u: u["id"])
    return render_template("users.html", users=users)


@app.route("/users/add", methods=["POST"])
@admin_required
def users_add():
    username = request.form.get("username", "").strip()
    full_name = request.form.get("full_name", "").strip()
    password = request.form.get("password", "")

    if not username or not full_name or not password:
        flash("لطفاً تمام فیلدها را تکمیل کنید.", "error")
        return redirect(url_for("users_list"))

    if find_user_by_username(username):
        flash("این نام کاربری قبلاً استفاده شده است.", "error")
        return redirect(url_for("users_list"))

    if len(password) < 4:
        flash("رمز عبور باید حداقل ۴ کاراکتر باشد.", "error")
        return redirect(url_for("users_list"))

    data = get_users_data()
    new_user = {
        "id": data["next_id"],
        "username": username,
        "password_hash": generate_password_hash(password),
        "full_name": full_name,
        "role": "user",  # همه کاربران ساخته‌شده توسط ادمین، نقش عادی دارند
        "created_at": datetime.now().strftime("%Y/%m/%d"),
    }
    data["users"].append(new_user)
    data["next_id"] += 1
    save_json(USERS_FILE, data)

    log_event("users.create", f"ایجاد کاربر «{full_name}» ({username})", category="users",
              details={"target_user_id": new_user["id"], "username": username, "full_name": full_name, "role": "user"})
    flash(f"کاربر «{full_name}» با موفقیت ایجاد شد.", "success")
    return redirect(url_for("users_list"))


@app.route("/users/edit/<int:user_id>", methods=["POST"])
@admin_required
def users_edit(user_id):
    data = get_users_data()
    target = next((u for u in data["users"] if u["id"] == user_id), None)

    if not target:
        flash("کاربر مورد نظر یافت نشد.", "error")
        return redirect(url_for("users_list"))

    if target["role"] == "admin":
        log_event("users.edit_denied", "تلاش برای ویرایش حساب مدیر اصلی", level="security", category="users",
                  details={"target_user_id": user_id})
        flash("امکان ویرایش حساب مدیر اصلی وجود ندارد.", "error")
        return redirect(url_for("users_list"))

    full_name = request.form.get("full_name", "").strip()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    if not username or not full_name:
        flash("لطفاً تمام فیلدها را تکمیل کنید.", "error")
        return redirect(url_for("users_list"))

    existing = find_user_by_username(username)
    if existing and existing["id"] != user_id:
        flash("این نام کاربری قبلاً استفاده شده است.", "error")
        return redirect(url_for("users_list"))

    changes = {}
    if target["full_name"] != full_name:
        changes["full_name"] = {"from": target["full_name"], "to": full_name}
    if target["username"] != username:
        changes["username"] = {"from": target["username"], "to": username}
    target["full_name"] = full_name
    target["username"] = username
    if password:
        if len(password) < 4:
            flash("رمز عبور باید حداقل ۴ کاراکتر باشد.", "error")
            return redirect(url_for("users_list"))
        target["password_hash"] = generate_password_hash(password)
        changes["password"] = "changed"

    save_json(USERS_FILE, data)
    log_event("users.update", f"ویرایش کاربر «{full_name}»", category="users",
              level="security" if "password" in changes else "info",
              details={"target_user_id": user_id, "changes": changes})
    flash("اطلاعات کاربر با موفقیت به‌روزرسانی شد.", "success")
    return redirect(url_for("users_list"))


@app.route("/users/delete/<int:user_id>", methods=["POST"])
@admin_required
def users_delete(user_id):
    data = get_users_data()
    target = next((u for u in data["users"] if u["id"] == user_id), None)

    if not target:
        flash("کاربر مورد نظر یافت نشد.", "error")
        return redirect(url_for("users_list"))

    if target["role"] == "admin":
        log_event("users.delete_denied", "تلاش برای حذف حساب مدیر اصلی", level="security", category="users",
                  details={"target_user_id": user_id})
        flash("امکان حذف حساب مدیر اصلی وجود ندارد.", "error")
        return redirect(url_for("users_list"))

    data["users"] = [u for u in data["users"] if u["id"] != user_id]
    save_json(USERS_FILE, data)
    log_event("users.delete", f"حذف کاربر «{target['full_name']}» ({target['username']})", level="warning", category="users",
              details={"target_user_id": user_id, "username": target["username"]})
    flash(f"کاربر «{target['full_name']}» حذف شد.", "success")
    return redirect(url_for("users_list"))


# --------------------------------------------------------------------------
# مسیرها: مدیریت اکانت‌های کارزار
# --------------------------------------------------------------------------
@app.route("/accounts")
@admin_required
def accounts_list():
    data = get_accounts_data()
    accounts = sorted(data["accounts"], key=lambda a: a["id"])
    return render_template("accounts.html", accounts=accounts)


def _identifier_exists(identifier):
    data = get_accounts_data()
    return any(a["identifier"].strip().lower() == identifier.strip().lower() for a in data["accounts"])


def _current_actor():
    return {
        "user_id": session.get("user_id"),
        "username": session.get("username"),
        "full_name": session.get("full_name"),
        "role": session.get("role"),
    }


def _append_account(name, identifier, user_data_dir, actor=None):
    with _accounts_lock:
        data = get_accounts_data()
        account = {
            "id": data["next_id"],
            "name": name,
            "identifier": identifier,
            "platform": "Karzar",
            "user_data_dir": user_data_dir,
            "status": "active",
            "created_at": datetime.now().strftime("%Y/%m/%d %H:%M"),
        }
        data["accounts"].append(account)
        data["next_id"] += 1
        save_json(ACCOUNTS_FILE, data)
    log_event("accounts.create", f"اکانت کارزار «{name}» با موفقیت وارد و ثبت شد", category="accounts",
              actor=actor, details={"account_id": account["id"], "name": name, "identifier": identifier})


@app.route("/accounts/add/start", methods=["POST"])
@admin_required
def accounts_add_start():
    payload = request.get_json(silent=True) or request.form
    name = (payload.get("name") or "").strip()
    identifier = (payload.get("identifier") or "").strip()

    if not name or not identifier:
        return jsonify({"ok": False, "error": "نام اکانت و شماره موبایل/ایمیل را وارد کنید."}), 400
    if _identifier_exists(identifier):
        log_event("accounts.add_duplicate", f"تلاش برای ثبت اکانت تکراری «{identifier}»", level="warning", category="accounts",
                  details={"identifier": identifier, "name": name})
        return jsonify({"ok": False, "error": "اکانتی با این شماره/ایمیل قبلاً ثبت شده است."}), 400

    user_data_dir = karzar_login.profile_dir_for(name)
    actor = _current_actor()
    sid = karzar_login.start_login(
        identifier,
        user_data_dir,
        on_success=lambda: _append_account(name, identifier, user_data_dir, actor=actor),
    )
    log_event("accounts.login_start", f"شروع ورود به کارزار برای اکانت «{name}»", category="accounts",
              details={"sid": sid, "name": name, "identifier": identifier})
    return jsonify({"ok": True, "sid": sid})


@app.route("/accounts/add/status/<sid>")
@admin_required
def accounts_add_status(sid):
    return jsonify(karzar_login.get_status(sid))


@app.route("/accounts/add/otp/<sid>", methods=["POST"])
@admin_required
def accounts_add_otp(sid):
    payload = request.get_json(silent=True) or request.form
    code = (payload.get("code") or "").strip()
    if not code:
        return jsonify({"ok": False, "error": "کد را وارد کنید."}), 400
    if karzar_login.get_status(sid)["status"] != karzar_login.ST_WAIT_OTP:
        return jsonify({"ok": False, "error": "فرایند ورود در مرحلهٔ دریافت کد نیست."}), 400
    karzar_login.submit_otp(sid, code)
    log_event("accounts.otp_submit", "ارسال کد تأیید کارزار", category="accounts",
              details={"sid": sid, "code_length": len(code)})
    return jsonify({"ok": True})


@app.route("/accounts/add/cancel/<sid>", methods=["POST"])
@admin_required
def accounts_add_cancel(sid):
    karzar_login.cancel(sid)
    log_event("accounts.login_cancel", "لغو فرایند ورود اکانت کارزار", level="warning", category="accounts", details={"sid": sid})
    return jsonify({"ok": True})


@app.route("/accounts/delete/<int:account_id>", methods=["POST"])
@admin_required
def accounts_delete(account_id):
    with _accounts_lock:
        data = get_accounts_data()
        target = next((a for a in data["accounts"] if a["id"] == account_id), None)
        if not target:
            flash("اکانت مورد نظر یافت نشد.", "error")
            return redirect(url_for("accounts_list"))
        data["accounts"] = [a for a in data["accounts"] if a["id"] != account_id]
        save_json(ACCOUNTS_FILE, data)

    profile = target.get("user_data_dir")
    profile_removed = False
    if profile and os.path.isdir(profile) and os.path.abspath(profile).startswith(karzar_login.PROFILES_DIR):
        shutil.rmtree(profile, ignore_errors=True)
        profile_removed = True

    log_event("accounts.delete", f"حذف اکانت کارزار «{target['name']}»", level="warning", category="accounts",
              details={"account_id": account_id, "name": target["name"], "identifier": target.get("identifier"),
                       "profile_removed": profile_removed})
    flash(f"اکانت «{target['name']}» حذف شد.", "success")
    return redirect(url_for("accounts_list"))


# --------------------------------------------------------------------------
# مسیرها: ثبت امضا (با Playwright و پروفایل هر اکانت)
# --------------------------------------------------------------------------
def _is_admin() -> bool:
    return session.get("role") == "admin"


def _job_visible(job: dict) -> bool:
    """ادمین همهٔ کارها را می‌بیند؛ کاربر عادی فقط کارهایی را که خودش شروع کرده."""
    return _is_admin() or (job.get("actor") or {}).get("user_id") == session.get("user_id")


def _visible_job(jid: str):
    job = karzar_sign.get_job(jid)
    return job if job and _job_visible(job) else None


@app.route("/signatures")
@login_required
def signatures():
    data = get_accounts_data()
    accounts = sorted(data["accounts"], key=lambda a: a["id"])
    return render_template("signatures.html", accounts=accounts, is_admin=_is_admin())


@app.route("/signatures/submit", methods=["POST"])
@login_required
def signatures_submit():
    payload = request.get_json(silent=True)
    if payload is not None:
        link_or_code = (payload.get("link_or_code") or "").strip()
        selected_ids = payload.get("account_ids") or []
    else:
        link_or_code = request.form.get("link_or_code", "").strip()
        selected_ids = request.form.getlist("account_ids")

    code = karzar_sign.parse_campaign_code(link_or_code)
    if not code:
        log_event("signatures.invalid_input", "لینک/کد کارزار نامعتبر برای ثبت امضا", level="warning", category="signatures",
                  details={"input": link_or_code[:200]})
        return jsonify({"ok": False, "error": "لینک یا کد کارزار معتبر نیست. نمونه: https://www.karzar.net/344536 یا 344536"}), 400

    try:
        ids = {int(i) for i in selected_ids}
    except (TypeError, ValueError):
        ids = set()
    if not ids:
        return jsonify({"ok": False, "error": "حداقل یک اکانت را انتخاب کنید."}), 400

    data = get_accounts_data()
    accounts = sorted((a for a in data["accounts"] if a["id"] in ids), key=lambda a: a["id"])
    if not accounts:
        return jsonify({"ok": False, "error": "اکانت انتخاب‌شده پیدا نشد."}), 400

    jid = karzar_sign.start_job(code, accounts, actor=_current_actor())
    log_event("signatures.job_start", f"شروع ثبت امضا برای کارزار {code} با {len(accounts)} اکانت", category="signatures",
              details={"jid": jid, "campaign_code": code, "campaign_url": karzar_sign.campaign_url(code),
                       "account_ids": [a["id"] for a in accounts], "account_names": [a["name"] for a in accounts]})
    return jsonify({"ok": True, "jid": jid, "campaign_code": code})


@app.route("/signatures/jobs")
@login_required
def signatures_jobs():
    jobs = [j for j in karzar_sign.list_jobs(limit=10_000) if _job_visible(j)]
    return jsonify({"ok": True, "jobs": jobs[:10]})


@app.route("/signatures/jobs/<jid>", methods=["DELETE"])
@login_required
def signatures_job_delete(jid):
    job = _visible_job(jid)
    if not job:
        return jsonify({"ok": False, "error": "کار پیدا نشد."}), 404
    if job["status"] == karzar_sign.J_RUNNING:
        return jsonify({"ok": False, "error": "این کار هنوز در حال اجراست و نمی‌توان آن را حذف کرد."}), 409
    karzar_sign.delete_job(jid)
    log_event("signatures.job_delete", f"حذف کار ثبت امضای کارزار {job.get('campaign_code')}", level="warning", category="signatures",
              details={"jid": jid, "campaign_code": job.get("campaign_code"), "status": job.get("status"),
                       "accounts": len(job.get("accounts", []))})
    return jsonify({"ok": True})


@app.route("/signatures/jobs", methods=["DELETE"])
@login_required
def signatures_jobs_delete_all():
    before = [j for j in karzar_sign.list_jobs(limit=10_000) if _job_visible(j)]
    deleted = sum(1 for j in before if karzar_sign.delete_job(j["id"]))
    skipped = len(before) - deleted
    log_event("signatures.jobs_delete_all", f"حذف همهٔ کارهای اخیر ثبت امضا ({deleted} کار)", level="warning", category="signatures",
              details={"deleted": deleted, "skipped_running": skipped})
    return jsonify({"ok": True, "deleted": deleted, "skipped": skipped})


@app.route("/signatures/status/<jid>")
@login_required
def signatures_status(jid):
    job = _visible_job(jid)
    if not job:
        return jsonify({"ok": False, "error": "کار پیدا نشد."}), 404
    return jsonify({"ok": True, "job": job})


@app.route("/signatures/screenshot/<jid>/<int:account_id>")
@login_required
def signatures_screenshot(jid, account_id):
    if not _visible_job(jid):
        abort(404)
    path = karzar_sign.screenshot_path(jid, account_id)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, mimetype="image/png", max_age=0)


# --------------------------------------------------------------------------
# مسیرها: گزارش فعالیت‌ها
# --------------------------------------------------------------------------
def _activity_scope():
    """ادمین همه را می‌بیند؛ کاربر عادی فقط فعالیت‌های خودش را."""
    return None if session.get("role") == "admin" else session.get("user_id")


def _activity_filters_from_request():
    a = request.args
    scope = _activity_scope()
    return {
        "user_ids": None if scope is not None else (a.getlist("user") or a.get("users")),
        "levels": a.getlist("level") or a.get("levels"),
        "categories": a.getlist("category") or a.get("categories"),
        "actions": a.getlist("action") or a.get("actions"),
        "q": a.get("q"),
        "date_from": a.get("from"),
        "date_to": a.get("to"),
        "min_level": a.get("min_level"),
        "only_user_id": scope,
    }


@app.route("/activity")
@admin_required
def activity_logs():
    users = sorted(get_users_data()["users"], key=lambda u: u["id"])
    if session.get("role") != "admin":
        users = [u for u in users if u["id"] == session.get("user_id")]
    return render_template(
        "activity_logs.html",
        users=[{"id": u["id"], "username": u["username"], "full_name": u["full_name"], "role": u["role"]} for u in users],
        levels=[{"key": k, "label": activity_log.LEVEL_LABELS[k]} for k in activity_log.LEVELS],
        categories=[{"key": k, "label": activity_log.CATEGORY_LABELS[k]} for k in activity_log.CATEGORIES],
        is_admin=session.get("role") == "admin",
    )


@app.route("/activity/events")
@admin_required
def activity_events():
    events = activity_log.filter_events(**_activity_filters_from_request())
    page = activity_log.paginate(events, request.args.get("page", 1), request.args.get("per_page", 50))
    return jsonify({"ok": True, **page, "stats": activity_log.stats(events, request.args.get("granularity")),
                    "storage": activity_log.storage_info()})


@app.route("/activity/stats")
@admin_required
def activity_stats():
    events = activity_log.filter_events(**_activity_filters_from_request())
    return jsonify({"ok": True, "stats": activity_log.stats(events, request.args.get("granularity")),
                    "storage": activity_log.storage_info()})


@app.route("/activity/export.<fmt>")
@admin_required
def activity_export(fmt):
    if fmt not in ("csv", "json"):
        abort(404)
    events = activity_log.filter_events(**_activity_filters_from_request())
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_event("activity.export", f"خروجی {fmt.upper()} از گزارش فعالیت‌ها ({len(events)} رکورد)", category="activity",
              details={"format": fmt, "count": len(events), "filters": request.args.to_dict(flat=False)})
    if fmt == "csv":
        body = activity_log.to_csv(events)
        mimetype = "text/csv; charset=utf-8"
    else:
        body = json.dumps(events, ensure_ascii=False, indent=2)
        mimetype = "application/json; charset=utf-8"
    return Response(body, mimetype=mimetype,
                    headers={"Content-Disposition": f"attachment; filename=activity-{stamp}.{fmt}"})


@app.route("/activity/clear", methods=["POST"])
@admin_required
def activity_clear():
    removed = activity_log.clear_all()
    log_event("activity.clear", f"پاک‌سازی کامل گزارش فعالیت‌ها ({removed} رکورد)", level="security", category="activity",
              details={"removed": removed})
    return jsonify({"ok": True, "removed": removed})


# --------------------------------------------------------------------------
# مدیریت خطاها
# --------------------------------------------------------------------------
@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


# --------------------------------------------------------------------------
# اجرا
# --------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
