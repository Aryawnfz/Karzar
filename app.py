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
from datetime import datetime
from functools import wraps

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    jsonify,
)
from werkzeug.security import generate_password_hash, check_password_hash

import karzar_login

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
            flash("شما دسترسی لازم برای مشاهده این صفحه را ندارید.", "error")
            return redirect(url_for("dashboard"))
        return view_func(*args, **kwargs)

    return wrapped


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
            flash(f"خوش آمدید، {user['full_name']}", "success")
            next_page = request.args.get("next")
            return redirect(next_page or url_for("dashboard"))

        flash("نام کاربری یا رمز عبور اشتباه است.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
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

    target["full_name"] = full_name
    target["username"] = username
    if password:
        if len(password) < 4:
            flash("رمز عبور باید حداقل ۴ کاراکتر باشد.", "error")
            return redirect(url_for("users_list"))
        target["password_hash"] = generate_password_hash(password)

    save_json(USERS_FILE, data)
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
        flash("امکان حذف حساب مدیر اصلی وجود ندارد.", "error")
        return redirect(url_for("users_list"))

    data["users"] = [u for u in data["users"] if u["id"] != user_id]
    save_json(USERS_FILE, data)
    flash(f"کاربر «{target['full_name']}» حذف شد.", "success")
    return redirect(url_for("users_list"))


# --------------------------------------------------------------------------
# مسیرها: مدیریت اکانت‌های کارزار
# --------------------------------------------------------------------------
@app.route("/accounts")
@login_required
def accounts_list():
    data = get_accounts_data()
    accounts = sorted(data["accounts"], key=lambda a: a["id"])
    return render_template("accounts.html", accounts=accounts)


def _identifier_exists(identifier):
    data = get_accounts_data()
    return any(a["identifier"].strip().lower() == identifier.strip().lower() for a in data["accounts"])


def _append_account(name, identifier, user_data_dir):
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


@app.route("/accounts/add/start", methods=["POST"])
@login_required
def accounts_add_start():
    payload = request.get_json(silent=True) or request.form
    name = (payload.get("name") or "").strip()
    identifier = (payload.get("identifier") or "").strip()

    if not name or not identifier:
        return jsonify({"ok": False, "error": "نام اکانت و شماره موبایل/ایمیل را وارد کنید."}), 400
    if _identifier_exists(identifier):
        return jsonify({"ok": False, "error": "اکانتی با این شماره/ایمیل قبلاً ثبت شده است."}), 400

    user_data_dir = karzar_login.profile_dir_for(name)
    sid = karzar_login.start_login(
        identifier,
        user_data_dir,
        on_success=lambda: _append_account(name, identifier, user_data_dir),
    )
    return jsonify({"ok": True, "sid": sid})


@app.route("/accounts/add/status/<sid>")
@login_required
def accounts_add_status(sid):
    return jsonify(karzar_login.get_status(sid))


@app.route("/accounts/add/otp/<sid>", methods=["POST"])
@login_required
def accounts_add_otp(sid):
    payload = request.get_json(silent=True) or request.form
    code = (payload.get("code") or "").strip()
    if not code:
        return jsonify({"ok": False, "error": "کد را وارد کنید."}), 400
    if karzar_login.get_status(sid)["status"] != karzar_login.ST_WAIT_OTP:
        return jsonify({"ok": False, "error": "فرایند ورود در مرحلهٔ دریافت کد نیست."}), 400
    karzar_login.submit_otp(sid, code)
    return jsonify({"ok": True})


@app.route("/accounts/add/cancel/<sid>", methods=["POST"])
@login_required
def accounts_add_cancel(sid):
    karzar_login.cancel(sid)
    return jsonify({"ok": True})


@app.route("/accounts/delete/<int:account_id>", methods=["POST"])
@login_required
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
    if profile and os.path.isdir(profile) and os.path.abspath(profile).startswith(karzar_login.PROFILES_DIR):
        shutil.rmtree(profile, ignore_errors=True)

    flash(f"اکانت «{target['name']}» حذف شد.", "success")
    return redirect(url_for("accounts_list"))


# --------------------------------------------------------------------------
# مسیرها: ثبت امضا (فقط رابط کاربری)
# --------------------------------------------------------------------------
@app.route("/signatures")
@login_required
def signatures():
    data = get_accounts_data()
    accounts = data["accounts"]
    return render_template("signatures.html", accounts=accounts)


@app.route("/signatures/submit", methods=["POST"])
@login_required
def signatures_submit():
    # منطق پردازش در آینده اضافه خواهد شد. در حال حاضر فقط پیام تأیید نمایش داده می‌شود.
    link_or_code = request.form.get("link_or_code", "").strip()
    selected_ids = request.form.getlist("account_ids")

    if not link_or_code:
        flash("لطفاً لینک یا کد را وارد کنید.", "error")
        return redirect(url_for("signatures"))

    if not selected_ids:
        flash("لطفاً حداقل یک اکانت را انتخاب کنید.", "error")
        return redirect(url_for("signatures"))

    flash(f"درخواست ثبت امضا برای {len(selected_ids)} اکانت با موفقیت ارسال شد.", "success")
    return redirect(url_for("signatures"))


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
