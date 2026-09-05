# -*- coding: utf-8 -*-
"""
گزارش فعالیت‌ها (Activity Log)
------------------------------
ثبت ریزِ همهٔ فعالیت‌های سامانه به‌صورت append-only در فایل‌های JSONL ماهانه:
  data/activity_log/YYYY-MM.jsonl

هر رکورد یک JSON در یک خط است با فیلدهای:
  id, ts (ISO), ts_epoch, level, category, action, message,
  user_id, username, full_name, role, ip, user_agent, method, path, endpoint,
  status_code, duration_ms, request_id, details (dict)

سطح‌ها (level):   debug < info < warning < error < security
دسته‌ها (category): auth, users, accounts, signatures, activity, http, system

هر رویداد در یک نوشتنِ واحد با حالت append ذخیره می‌شود (خطوط JSON کوچک)؛
بنابراین با چند worker گونیکورن و فرایندِ مستقلِ ثبت امضا، و همچنین روی
ویندوز و لینوکس، بدون نیاز به قفل فایل کار می‌کند.
"""

import csv
import io
import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Iterable, Optional

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
LOG_DIR = os.path.join(DATA_DIR, "activity_log")

_write_lock = threading.Lock()

LEVELS = ("debug", "info", "warning", "error", "security")
LEVEL_RANK = {lv: i for i, lv in enumerate(LEVELS)}
CATEGORIES = ("auth", "users", "accounts", "signatures", "activity", "http", "system")

LEVEL_LABELS = {
    "debug": "جزئیات",
    "info": "اطلاع",
    "warning": "هشدار",
    "error": "خطا",
    "security": "امنیتی",
}
CATEGORY_LABELS = {
    "auth": "ورود و خروج",
    "users": "مدیریت کاربران",
    "accounts": "اکانت‌های کارزار",
    "signatures": "ثبت امضا",
    "activity": "گزارش فعالیت‌ها",
    "http": "درخواست‌های HTTP",
    "system": "سیستم",
}

# مسیرهایی که با فاصلهٔ کوتاه poll می‌شوند؛ با سطح debug ثبت می‌شوند تا گزارش را شلوغ نکنند.
NOISY_ENDPOINTS = {
    "accounts_add_status",
    "signatures_status",
    "signatures_jobs",
    "signatures_screenshot",
    "activity_events",
    "activity_stats",
    "static",
}


# --------------------------------------------------------------------------
# نوشتن
# --------------------------------------------------------------------------
def _ensure_dir():
    os.makedirs(LOG_DIR, exist_ok=True)


def _file_for(ts: datetime) -> str:
    return os.path.join(LOG_DIR, ts.strftime("%Y-%m") + ".jsonl")


def _client_ip() -> Optional[str]:
    from flask import request

    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.remote_addr


def _context_fields() -> dict:
    """اطلاعات کاربر/درخواست جاری از Flask (اگر در حال پاسخ به درخواست باشیم)."""
    try:
        from flask import g, has_request_context, request, session
    except ImportError:  # pragma: no cover
        return {}
    if not has_request_context():
        return {}
    fields = {
        "user_id": session.get("user_id"),
        "username": session.get("username"),
        "full_name": session.get("full_name"),
        "role": session.get("role"),
        "ip": _client_ip(),
        "user_agent": (request.user_agent.string or "")[:300],
        "method": request.method,
        "path": request.full_path.rstrip("?") if request.query_string else request.path,
        "endpoint": request.endpoint,
        "request_id": g.get("request_id"),
    }
    return fields


def log_event(
    action: str,
    message: str,
    *,
    level: str = "info",
    category: str = "system",
    details: Optional[dict] = None,
    actor: Optional[dict] = None,
    status_code: Optional[int] = None,
    duration_ms: Optional[float] = None,
) -> dict:
    """
    ثبت یک رویداد. در متن درخواست Flask، کاربر/IP/مسیر خودکار پر می‌شود.
    خارج از درخواست (مثلاً فرایند ثبت امضا) می‌توان actor را صریح داد:
      actor = {"user_id": 1, "username": "admin", "full_name": "...", "role": "admin"}
    """
    if level not in LEVEL_RANK:
        level = "info"
    if category not in CATEGORIES:
        category = "system"
    now = datetime.now()
    event = {
        "id": uuid.uuid4().hex,
        "ts": now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3],
        "ts_epoch": round(time.time(), 3),
        "level": level,
        "category": category,
        "action": action,
        "message": message,
        "user_id": None,
        "username": None,
        "full_name": None,
        "role": None,
        "ip": None,
        "user_agent": None,
        "method": None,
        "path": None,
        "endpoint": None,
        "status_code": status_code,
        "duration_ms": round(duration_ms, 1) if duration_ms is not None else None,
        "request_id": None,
        "pid": os.getpid(),
        "details": details or {},
    }
    event.update({k: v for k, v in _context_fields().items() if v is not None})
    if actor:
        event.update({k: actor.get(k) for k in ("user_id", "username", "full_name", "role") if actor.get(k) is not None})

    _ensure_dir()
    line = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
    with _write_lock:
        with open(_file_for(now), "ab") as f:
            f.write(line)
    return event


# --------------------------------------------------------------------------
# خواندن و فیلتر
# --------------------------------------------------------------------------
def _log_files() -> list:
    _ensure_dir()
    files = [os.path.join(LOG_DIR, n) for n in os.listdir(LOG_DIR) if n.endswith(".jsonl")]
    return sorted(files)


def _iter_events(date_from: Optional[datetime] = None, date_to: Optional[datetime] = None) -> Iterable[dict]:
    for path in _log_files():
        month = os.path.basename(path)[:-6]  # YYYY-MM
        if date_from and month < date_from.strftime("%Y-%m"):
            continue
        if date_to and month > date_to.strftime("%Y-%m"):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
        except FileNotFoundError:
            continue


def _parse_dt(value: Optional[str], end: bool = False) -> Optional[datetime]:
    """«YYYY-MM-DD» یا «YYYY-MM-DDTHH:MM» → datetime (برای انتها، تا پایان روز)."""
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value, fmt)
            if end and fmt == "%Y-%m-%d":
                dt = dt + timedelta(days=1) - timedelta(milliseconds=1)
            return dt
        except ValueError:
            continue
    return None


def _as_set(values) -> set:
    if values is None:
        return set()
    if isinstance(values, str):
        values = [v for v in values.split(",")]
    return {str(v).strip() for v in values if str(v).strip()}


def filter_events(
    *,
    user_ids=None,
    levels=None,
    categories=None,
    actions=None,
    q: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    min_level: Optional[str] = None,
    only_user_id: Optional[int] = None,
) -> list:
    """
    فیلتر رویدادها؛ نتیجه از جدید به قدیم مرتب است.
    user_ids: فهرست شناسهٔ کاربران («anonymous» برای رویدادهای بدون کاربر)
    only_user_id: محدودکردنِ اجباری به یک کاربر (برای کاربران غیرادمین)
    """
    uid_set = _as_set(user_ids)
    lvl_set = _as_set(levels) & set(LEVELS)
    cat_set = _as_set(categories) & set(CATEGORIES)
    act_set = _as_set(actions)
    q = (q or "").strip().lower()
    dt_from = _parse_dt(date_from)
    dt_to = _parse_dt(date_to, end=True)
    min_rank = LEVEL_RANK.get(min_level or "", 0)

    out = []
    for ev in _iter_events(dt_from, dt_to):
        if only_user_id is not None and ev.get("user_id") != only_user_id:
            continue
        if uid_set:
            key = "anonymous" if ev.get("user_id") is None else str(ev.get("user_id"))
            if key not in uid_set:
                continue
        if lvl_set and ev.get("level") not in lvl_set:
            continue
        if LEVEL_RANK.get(ev.get("level"), 0) < min_rank:
            continue
        if cat_set and ev.get("category") not in cat_set:
            continue
        if act_set and ev.get("action") not in act_set:
            continue
        if dt_from or dt_to:
            try:
                ts = datetime.strptime(ev["ts"][:19], "%Y-%m-%dT%H:%M:%S")
            except (KeyError, ValueError):
                continue
            if dt_from and ts < dt_from:
                continue
            if dt_to and ts > dt_to:
                continue
        if q:
            hay = " ".join(
                str(ev.get(k) or "")
                for k in ("message", "action", "username", "full_name", "path", "ip", "endpoint", "request_id")
            ).lower()
            if q not in hay and q not in json.dumps(ev.get("details") or {}, ensure_ascii=False).lower():
                continue
        out.append(ev)

    out.sort(key=lambda e: e.get("ts_epoch", 0), reverse=True)
    return out


def paginate(events: list, page: int = 1, per_page: int = 50) -> dict:
    per_page = max(1, min(int(per_page or 50), 500))
    page = max(1, int(page or 1))
    total = len(events)
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    start = (page - 1) * per_page
    return {
        "items": events[start:start + per_page],
        "page": page,
        "pages": pages,
        "per_page": per_page,
        "total": total,
    }


# --------------------------------------------------------------------------
# آمار و نمودار
# --------------------------------------------------------------------------
def _bucket(ts: str, granularity: str) -> str:
    if granularity == "minute":
        return ts[:16]
    if granularity == "hour":
        return ts[:13] + ":00"
    return ts[:10]


def stats(events: list, granularity: Optional[str] = None) -> dict:
    """شمارش بر اساس سطح، دسته، کاربر، عمل، IP و سری زمانی."""
    by_level = {lv: 0 for lv in LEVELS}
    by_category = {c: 0 for c in CATEGORIES}
    by_user: dict = {}
    by_action: dict = {}
    by_ip: dict = {}
    by_status: dict = {}
    durations = []

    if events:
        span = (events[0].get("ts_epoch", 0) - events[-1].get("ts_epoch", 0)) if len(events) > 1 else 0
    else:
        span = 0
    if not granularity:
        granularity = "minute" if span <= 3 * 3600 else "hour" if span <= 3 * 86400 else "day"

    timeline: dict = {}
    for ev in events:
        by_level[ev.get("level", "info")] = by_level.get(ev.get("level", "info"), 0) + 1
        by_category[ev.get("category", "system")] = by_category.get(ev.get("category", "system"), 0) + 1
        ukey = "anonymous" if ev.get("user_id") is None else str(ev["user_id"])
        u = by_user.setdefault(ukey, {"user_id": ev.get("user_id"), "username": ev.get("username"), "full_name": ev.get("full_name") or "ناشناس", "count": 0, "errors": 0})
        u["count"] += 1
        if ev.get("level") in ("error", "security"):
            u["errors"] += 1
        by_action[ev.get("action", "")] = by_action.get(ev.get("action", ""), 0) + 1
        if ev.get("ip"):
            by_ip[ev["ip"]] = by_ip.get(ev["ip"], 0) + 1
        if ev.get("status_code"):
            fam = f"{str(ev['status_code'])[0]}xx"
            by_status[fam] = by_status.get(fam, 0) + 1
        if ev.get("duration_ms") is not None:
            durations.append(ev["duration_ms"])

        b = _bucket(ev.get("ts", ""), granularity)
        t = timeline.setdefault(b, {"total": 0, "errors": 0, "warnings": 0, "security": 0})
        t["total"] += 1
        if ev.get("level") == "error":
            t["errors"] += 1
        elif ev.get("level") == "warning":
            t["warnings"] += 1
        elif ev.get("level") == "security":
            t["security"] += 1

    durations.sort()
    p95 = durations[int(len(durations) * 0.95) - 1] if len(durations) >= 2 else (durations[0] if durations else None)
    return {
        "total": len(events),
        "granularity": granularity,
        "by_level": by_level,
        "by_category": by_category,
        "by_user": sorted(by_user.values(), key=lambda x: x["count"], reverse=True),
        "top_actions": sorted(({"action": a, "count": c} for a, c in by_action.items()), key=lambda x: x["count"], reverse=True)[:12],
        "top_ips": sorted(({"ip": a, "count": c} for a, c in by_ip.items()), key=lambda x: x["count"], reverse=True)[:8],
        "by_status": by_status,
        "avg_duration_ms": round(sum(durations) / len(durations), 1) if durations else None,
        "p95_duration_ms": p95,
        "timeline": [{"bucket": k, **v} for k, v in sorted(timeline.items())],
        "first_ts": events[-1]["ts"] if events else None,
        "last_ts": events[0]["ts"] if events else None,
    }


# --------------------------------------------------------------------------
# خروجی و پاک‌سازی
# --------------------------------------------------------------------------
CSV_COLUMNS = (
    "ts", "level", "category", "action", "message", "user_id", "username", "full_name", "role",
    "ip", "method", "path", "endpoint", "status_code", "duration_ms", "request_id", "details",
)


def to_csv(events: list) -> str:
    buf = io.StringIO()
    buf.write("\ufeff")  # BOM برای نمایش درست فارسی در اکسل
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for ev in events:
        row = []
        for c in CSV_COLUMNS:
            v = ev.get(c)
            if c == "details":
                v = json.dumps(v or {}, ensure_ascii=False)
            row.append("" if v is None else v)
        w.writerow(row)
    return buf.getvalue()


def clear_all() -> int:
    """حذف همهٔ فایل‌های لاگ؛ تعداد رکوردهای حذف‌شده را برمی‌گرداند."""
    count = 0
    for path in _log_files():
        try:
            with open(path, "r", encoding="utf-8") as f:
                count += sum(1 for line in f if line.strip())
            os.remove(path)
        except FileNotFoundError:
            pass
    return count


def storage_info() -> dict:
    files = _log_files()
    size = sum(os.path.getsize(p) for p in files if os.path.exists(p))
    return {"files": len(files), "size_bytes": size}
