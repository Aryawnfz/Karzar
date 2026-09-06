#!/usr/bin/env bash
# ==============================================================================
# Karzar — deploy.sh
# نصب و راه‌اندازی کاملاً خودکار روی سرور اوبونتو (فقط یک بار اجرا کنید):
#     sudo bash /Karzar/Publish/deploy.sh
#
# کارهایی که انجام می‌دهد:
#   1. نصب پایتون، pip، venv و وابستگی‌های سیستمی کرومیوم
#   2. ساخت virtualenv و نصب requirements + مرورگر Chromium برای Playwright
#   3. ساخت SECRET_KEY تصادفی (یک بار) در فایل .env
#   4. ساخت سرویس systemd «karzar» با Gunicorn روی پورت 8089
#      (ری‌استارت خودکار در صورت crash، اجرا بعد از ریبوت سرور)
#   5. باز کردن پورت در فایروال ufw (اگر فعال باشد)
#   6. بالا آوردن سرویس و بررسی سلامت
#
# اجرای دوباره همین اسکریپت = به‌روزرسانی (وابستگی‌ها را نصب و سرویس را ری‌استارت می‌کند).
# ==============================================================================
set -Eeuo pipefail

APP_NAME="karzar"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # پوشهٔ همین فایل (مثلاً /Karzar/Publish)
PORT="${PORT:-8089}"
BIND_HOST="${BIND_HOST:-0.0.0.0}"
RUN_USER="${RUN_USER:-$APP_NAME}"
VENV="$APP_DIR/.venv"
ENV_FILE="$APP_DIR/.env"
SERVICE_FILE="/etc/systemd/system/$APP_NAME.service"
WORKERS="${WORKERS:-2}"
PLAYWRIGHT_BROWSERS_PATH="$APP_DIR/.ms-playwright"

c_ok()   { printf '\e[32m✔ %s\e[0m\n' "$*"; }
c_info() { printf '\e[36m▶ %s\e[0m\n' "$*"; }
c_err()  { printf '\e[31m✖ %s\e[0m\n' "$*" >&2; }
trap 'c_err "خطا در خط $LINENO. برای جزئیات: journalctl -u '"$APP_NAME"' -n 50 --no-pager"' ERR

# ---------------------------------------------------------------- پیش‌نیازها
if [[ $EUID -ne 0 ]]; then
    c_err "این اسکریپت باید با root اجرا شود:  sudo bash $0"
    exit 1
fi
if [[ ! -f "$APP_DIR/app.py" || ! -f "$APP_DIR/requirements.txt" ]]; then
    c_err "app.py یا requirements.txt در $APP_DIR پیدا نشد. deploy.sh باید کنار فایل‌های پروژه باشد."
    exit 1
fi
export DEBIAN_FRONTEND=noninteractive

c_info "پوشهٔ برنامه: $APP_DIR  |  پورت: $PORT  |  کاربر سرویس: $RUN_USER"

# ---------------------------------------------------------------- 1) پکیج‌های سیستمی
c_info "نصب پکیج‌های سیستمی..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip curl ca-certificates fonts-liberation >/dev/null
c_ok "پکیج‌های سیستمی نصب شد"

# ---------------------------------------------------------------- 2) کاربر سرویس
if ! id -u "$RUN_USER" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "/var/lib/$APP_NAME" --shell /usr/sbin/nologin "$RUN_USER"
    c_ok "کاربر سیستمی $RUN_USER ساخته شد"
fi

# ---------------------------------------------------------------- 3) venv + وابستگی‌ها
c_info "ساخت virtualenv و نصب وابستگی‌های پایتون..."
if [[ ! -x "$VENV/bin/python" ]]; then
    python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --upgrade pip wheel
"$VENV/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
c_ok "وابستگی‌های پایتون نصب شد"

c_info "نصب Chromium و وابستگی‌های آن برای Playwright (ممکن است چند دقیقه طول بکشد)..."
export PLAYWRIGHT_BROWSERS_PATH
"$VENV/bin/python" -m playwright install-deps chromium >/dev/null
"$VENV/bin/python" -m playwright install chromium >/dev/null
c_ok "Chromium نصب شد ($PLAYWRIGHT_BROWSERS_PATH)"

# ---------------------------------------------------------------- 4) فایل .env (SECRET_KEY یک‌بار ساخته می‌شود)
if [[ ! -f "$ENV_FILE" ]] || ! grep -q '^SECRET_KEY=' "$ENV_FILE"; then
    {
        echo "SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
        echo "PLAYWRIGHT_BROWSERS_PATH=$PLAYWRIGHT_BROWSERS_PATH"
    } >> "$ENV_FILE"
    c_ok "SECRET_KEY تصادفی در $ENV_FILE ساخته شد"
fi
grep -q '^PLAYWRIGHT_BROWSERS_PATH=' "$ENV_FILE" || echo "PLAYWRIGHT_BROWSERS_PATH=$PLAYWRIGHT_BROWSERS_PATH" >> "$ENV_FILE"

# ---------------------------------------------------------------- 5) مجوزها
mkdir -p "$APP_DIR/data"
chown -R "$RUN_USER:$RUN_USER" "$APP_DIR"
chmod 600 "$ENV_FILE"
chmod +x "$0" || true

# ---------------------------------------------------------------- 6) سرویس systemd
c_info "ساخت سرویس systemd..."
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Karzar management panel (Gunicorn)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
Environment=PYTHONUNBUFFERED=1
ExecStart=$VENV/bin/gunicorn app:app \\
    --bind $BIND_HOST:$PORT \\
    --workers $WORKERS \\
    --threads 4 \\
    --timeout 120 \\
    --graceful-timeout 30 \\
    --access-logfile - \\
    --error-logfile -
ExecReload=/bin/kill -s HUP \$MAINPID
Restart=always
RestartSec=3
StartLimitIntervalSec=0
KillMode=mixed
TimeoutStopSec=30
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$APP_NAME" >/dev/null
systemctl restart "$APP_NAME"
c_ok "سرویس $APP_NAME فعال شد (اجرای خودکار بعد از ریبوت + ری‌استارت خودکار در صورت خطا)"

# ---------------------------------------------------------------- 7) فایروال
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
    ufw allow "$PORT"/tcp >/dev/null && c_ok "پورت $PORT در ufw باز شد"
fi

# ---------------------------------------------------------------- 8) بررسی سلامت
c_info "بررسی سلامت سرویس..."
for i in $(seq 1 20); do
    if curl -fsS -o /dev/null "http://127.0.0.1:$PORT/login" 2>/dev/null; then
        SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
        echo
        c_ok "سایت بالا آمد:  http://${SERVER_IP:-109.236.50.188}:$PORT"
        echo
        echo "دستورات مفید:"
        echo "  وضعیت:      systemctl status $APP_NAME"
        echo "  لاگ زنده:   journalctl -u $APP_NAME -f"
        echo "  ری‌استارت:   systemctl restart $APP_NAME"
        echo "  به‌روزرسانی: فایل‌های جدید را در $APP_DIR بگذارید و دوباره  sudo bash $APP_DIR/deploy.sh  را اجرا کنید"
        exit 0
    fi
    sleep 1
done

c_err "سرویس در ۲۰ ثانیه پاسخ نداد. لاگ:"
journalctl -u "$APP_NAME" -n 40 --no-pager || true
exit 1
