"""
db.py — نسخه نهایی سازگار با schema فعلی Supabase و bot.py

Schema اصلی:
- app_settings(key,value,updated_at)
- customers(id,telegram_id,full_name,created_at,is_test)
- contractors(id,telegram_id,full_name,phone,city,categories,credits,is_vip,referred_by,created_at,phone2,portfolio_files,bio,resume,social_link,is_test)
- projects(id,code,customer_id,customer_telegram_id,city,category,description,budget,urgency,status,created_at,hired_contractor_id,early_cancel_reason,closed_reason,closed_at,is_test,photo_file_id,posted_vip_at,posted_public_at,rating_reminder_sent,categories)
- declarations(id,contractor_id,project_code,created_at)
- applications(id,project_id,project_code,contractor_telegram_id,contractor_name,contractor_phone,contractor_resume,created_at)
- ratings(id,project_id,project_code,contractor_telegram_id,customer_telegram_id,score,comment,created_at,is_approved,customer_comment)
- scheduled_tasks(id,task_type,payload,run_at,status,created_at)
- events(id,event_type,payload,created_at)
- flow_events(id,telegram_id,flow_type,event_name,created_at)
- payments(id,telegram_id,payment_type,amount,receipt_file_id,discount_code,status,created_at)
"""

import os
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from supabase import create_client, Client

logger = logging.getLogger(__name__)

SUPABASE_URL = (
    os.getenv("SUPABASE_URL")
    or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    or ""
).strip()

SUPABASE_KEY = (
    os.getenv("SUPABASE_KEY")
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_SERVICE_KEY")
    or os.getenv("SUPABASE_ANON_KEY")
    or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
    or ""
).strip()

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL یا SUPABASE_KEY تنظیم نشده است.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ============================================================
# Helpers
# ============================================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _month_start_iso() -> str:
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc).isoformat()


def _data(resp):
    return getattr(resp, "data", None)


def _first(resp):
    data = _data(resp)
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict):
        return data
    return None


def _count(resp):
    return getattr(resp, "count", None)


def _safe_int(value, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def _parse_dt(value):
    """پارس امن رشته‌ی تاریخ - چه با timezone چه بدون آن."""
    if not value:
        return None
    try:
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        text = str(value).replace(",", "").replace("تومان", "").strip()
        return float(text)
    except Exception:
        return default


def _safe_json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _is_uuid_like(value) -> bool:
    return isinstance(value, str) and len(value) == 36 and value.count("-") == 4


def _is_int_like(value) -> bool:
    try:
        int(value)
        return not _is_uuid_like(value)
    except Exception:
        return False


def _select_first(table: str, column: str, value):
    try:
        resp = supabase.table(table).select("*").eq(column, value).limit(1).execute()
        return _first(resp)
    except Exception as e:
        logger.error(f"_select_first خطا در {table}.{column}: {e}")
        return None


def _normalize_status_from_db(status: Optional[str]) -> str:
    if not status:
        return "open"
    if status == "active":
        return "open"
    return status


def _normalize_status_to_db(status: Optional[str]) -> str:
    if not status:
        return "open"
    if status == "active":
        return "open"
    return status


def _normalize_customer(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    item = dict(row)
    item["full_name"] = item.get("full_name") or "مشتری"
    return item


def _normalize_contractor(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    item = dict(row)
    item["full_name"] = item.get("full_name") or ""
    # ستون واقعی در دیتابیس "credits" (جمع) است؛ اینجا هر دو کلید
    # "credit" و "credits" رو برای سازگاری با بقیه‌ی کد ست می‌کنیم.
    item["credits"] = _safe_int(item.get("credits"), 0)
    item["credit"] = item["credits"]
    item["second_phone"] = item.get("phone2")
    item["social_media"] = item.get("social_link")
    item["portfolio_files"] = item.get("portfolio_files") or []
    if not isinstance(item["portfolio_files"], list):
        item["portfolio_files"] = []
    return item


def _normalize_project(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None

    item = dict(row)
    item["code"] = item.get("code")
    item["project_code"] = item.get("code")
    item["status"] = _normalize_status_from_db(item.get("status"))

    categories = item.get("categories") or []
    if isinstance(categories, str):
        categories = [categories]
    item["categories"] = categories

    if not item.get("category") and categories:
        item["category"] = categories[0]

    item["close_reason"] = item.get("closed_reason")
    return item


def _normalize_declaration(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    item = dict(row)

    if item.get("contractors"):
        item["contractor"] = _normalize_contractor(item.get("contractors"))
    elif item.get("contractor_id"):
        item["contractor"] = get_contractor_by_id(item.get("contractor_id"))
        item["contractors"] = item["contractor"]

    return item


def _insert_event(event_type: str, payload: Dict[str, Any] = None) -> bool:
    try:
        supabase.table("events").insert({
            "event_type": event_type,
            "payload": payload or {},
            "created_at": _now_iso(),
        }).execute()
        return True
    except Exception as e:
        logger.warning(f"_insert_event خطا: {e}")
        return False


def _identifier_filter(table_query, identifier):
    if _is_uuid_like(identifier):
        return table_query.eq("id", identifier)
    return table_query.eq("code", str(identifier).strip().upper())


# ============================================================
# Health Check
# ============================================================

def db_health_check() -> bool:
    try:
        supabase.table("app_settings").select("key").limit(1).execute()
        supabase.table("customers").select("id").limit(1).execute()
        supabase.table("contractors").select("id").limit(1).execute()
        supabase.table("projects").select("id").limit(1).execute()
        supabase.table("scheduled_tasks").select("id").limit(1).execute()
        return True
    except Exception as e:
        logger.error(f"db_health_check خطا: {e}")
        return False


# ============================================================
# Settings
# ============================================================

_SETTING_ALIASES = {
    "initial_credit": ["contractor_initial_credits", "free_declarations", "initial_credit"],
    "max_apply_credit": ["contractor_initial_credits", "free_declarations", "initial_credit"],
    "vip_delay_seconds": ["vip_delay_minutes", "vip_delay_seconds"],
    "rating_reminder_days": ["rating_delay_days", "rating_reminder_days"],
    "max_monthly_projects": ["customer_monthly_project_cap", "max_monthly_projects"],
    "close_early_threshold": ["application_cap_count", "customer_flag_threshold", "close_early_threshold"],
    "application_cap_count": ["application_cap_count"],
    "vip_application_cap_count": ["vip_application_cap_count"],
}

_DEFAULT_SETTINGS = {
    "contractor_initial_credits": "10",
    "free_declarations": "10",
    "initial_credit": "10",
    "vip_delay_minutes": "10",
    "vip_delay_seconds": "600",
    "rating_delay_days": "1",
    "rating_reminder_days": "1",
    "customer_monthly_project_cap": "10",
    "max_monthly_projects": "10",
    "application_cap_count": "10",
    "customer_flag_threshold": "10",
    "close_early_threshold": "10",
    "vip_application_cap_count": "50",
    "price_monthly_subscription": "2000000",
    "price_3_credits": "750000",
    "price_10_credits": "2000000",
    "price_30_credits": "5000000",
}


def _setting_keys(key: str) -> List[str]:
    return _SETTING_ALIASES.get(key, [key])


def get_setting(key: str, default=None):
    keys = _setting_keys(key)

    for k in keys:
        try:
            resp = supabase.table("app_settings").select("key,value").eq("key", k).limit(1).execute()
            row = _first(resp)
            if row and row.get("value") is not None:
                return row.get("value")
        except Exception as e:
            logger.warning(f"get_setting خطا برای {k}: {e}")

    for k in keys:
        if k in _DEFAULT_SETTINGS:
            return _DEFAULT_SETTINGS[k]

    return default


def get_setting_int(key: str, default: int = 0) -> int:
    raw = get_setting(key, default)
    value = _safe_int(raw, default)

    if key == "vip_delay_seconds":
        keys = _setting_keys(key)
        if "vip_delay_minutes" in keys:
            stored = get_setting("vip_delay_minutes", None)
            if stored is not None:
                return _safe_int(stored, 10) * 60

    return value


def get_setting_bool(key: str, default: bool = False) -> bool:
    raw = get_setting(key, default)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("true", "1", "yes", "on", "بله")


def set_setting(key: str, value) -> bool:
    db_key = _setting_keys(key)[0]

    if key == "vip_delay_seconds" and db_key == "vip_delay_minutes":
        value = max(1, _safe_int(value, 600) // 60)

    try:
        existing = supabase.table("app_settings").select("key").eq("key", db_key).limit(1).execute()
        payload = {
            "key": db_key,
            "value": str(value),
            "updated_at": _now_iso(),
        }

        if _data(existing):
            supabase.table("app_settings").update({
                "value": str(value),
                "updated_at": _now_iso(),
            }).eq("key", db_key).execute()
        else:
            supabase.table("app_settings").insert(payload).execute()

        return True
    except Exception as e:
        logger.error(f"set_setting خطا برای {key}: {e}")
        return False


def get_all_settings() -> dict:
    result = dict(_DEFAULT_SETTINGS)
    try:
        resp = supabase.table("app_settings").select("key,value").execute()
        for row in (_data(resp) or []):
            result[row.get("key")] = row.get("value")
    except Exception as e:
        logger.error(f"get_all_settings خطا: {e}")
    return result


# ============================================================
# Customers
# ============================================================

def get_customer_by_telegram_id(telegram_id: int):
    row = _select_first("customers", "telegram_id", telegram_id)
    return _normalize_customer(row)


def get_or_create_customer(telegram_id: int, full_name: str = "مشتری", is_test: bool = False):
    existing = get_customer_by_telegram_id(telegram_id)
    if existing:
        return existing

    try:
        resp = supabase.table("customers").insert({
            "telegram_id": telegram_id,
            "full_name": full_name or "مشتری",
            "is_test": bool(is_test),
        }).execute()
        return _normalize_customer(_first(resp))
    except Exception as e:
        logger.error(f"get_or_create_customer خطا: {e}")
        return None


def create_customer(telegram_id: int, name: str, phone: str = None):
    return get_or_create_customer(telegram_id, name or "مشتری")


def update_customer_city(customer_id, city: str):
    return _insert_event("customer_city_updated", {
        "customer_id": customer_id,
        "city": city,
    })


def get_customer_monthly_count(customer_id) -> int:
    try:
        resp = supabase.rpc("get_customer_monthly_count", {
            "p_customer_id": customer_id,
        }).execute()
        if isinstance(resp.data, int):
            return resp.data
        if isinstance(resp.data, list) and resp.data:
            return _safe_int(resp.data[0], 0)
    except Exception as e:
        logger.warning(f"RPC get_customer_monthly_count خطا: {e}")

    try:
        resp = (
            supabase.table("projects")
            .select("id", count="exact")
            .eq("customer_id", customer_id)
            .gte("created_at", _month_start_iso())
            .execute()
        )
        c = _count(resp)
        return int(c) if c is not None else len(_data(resp) or [])
    except Exception as e:
        logger.error(f"get_customer_monthly_count خطا: {e}")
        return 0


def count_customer_monthly_projects(customer_id) -> int:
    return get_customer_monthly_count(customer_id)


def get_customer_monthly_project_count(customer_id) -> int:
    return get_customer_monthly_count(customer_id)


def get_customer_active_project(customer_id):
    projects = get_customer_active_projects(customer_id)
    return projects[0] if projects else None


def get_customer_active_projects(customer_id):
    try:
        resp = (
            supabase.table("projects")
            .select("*")
            .eq("customer_id", customer_id)
            .in_("status", ["open", "active", "in_progress"])
            .order("created_at", desc=True)
            .execute()
        )
        return [_normalize_project(x) for x in (_data(resp) or [])]
    except Exception as e:
        logger.error(f"get_customer_active_projects خطا: {e}")
        return []


def get_customer_open_projects(customer_id):
    return get_customer_active_projects(customer_id)


def get_customer_projects(customer_id, limit: int = 20):
    try:
        resp = (
            supabase.table("projects")
            .select("*")
            .eq("customer_id", customer_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return [_normalize_project(x) for x in (_data(resp) or [])]
    except Exception as e:
        logger.error(f"get_customer_projects خطا: {e}")
        return []


def flag_customer(customer_or_telegram_id: int, reason: str = None) -> bool:
    telegram_id = customer_or_telegram_id

    if _is_uuid_like(str(customer_or_telegram_id)):
        customer = _select_first("customers", "id", customer_or_telegram_id)
        if customer:
            telegram_id = customer.get("telegram_id")

    try:
        supabase.table("customer_flags").insert({
            "telegram_id": telegram_id,
            "reason": reason,
            "created_at": _now_iso(),
        }).execute()
        return True
    except Exception as e:
        logger.warning(f"flag_customer خطا: {e}")
        return _insert_event("customer_flag", {
            "telegram_id": telegram_id,
            "reason": reason,
        })


# ============================================================
# Contractors
# ============================================================

def get_contractor_by_telegram_id(telegram_id: int):
    row = _select_first("contractors", "telegram_id", telegram_id)
    return _normalize_contractor(row)


def get_contractor_by_id(contractor_id):
    row = _select_first("contractors", "id", contractor_id)
    return _normalize_contractor(row)


def get_contractor_by_phone(phone: str):
    row = _select_first("contractors", "phone", phone)
    return _normalize_contractor(row)


def register_contractor(
    telegram_id: int,
    full_name: str,
    phone: str,
    city: str,
    categories: List[str],
    referred_by=None,
    is_test: bool = False,
    is_vip: bool = False,
):
    existing = get_contractor_by_telegram_id(telegram_id)
    if existing:
        return existing

    initial_credit = get_setting_int("initial_credit", 10)

    payload = {
        "telegram_id": telegram_id,
        "full_name": full_name,
        "phone": phone,
        "city": city,
        "categories": categories or [],
        "credits": initial_credit,
        "is_vip": bool(is_vip),
        "is_test": bool(is_test),
    }

    if referred_by and _is_uuid_like(str(referred_by)):
        payload["referred_by"] = referred_by

    try:
        resp = supabase.table("contractors").insert(payload).execute()
        return _normalize_contractor(_first(resp))
    except Exception as e:
        logger.error(f"register_contractor خطا: {e}")
        return None


def create_contractor(
    telegram_id: int,
    name: str,
    phone: str,
    categories: List[str],
    resume: str = None,
    referred_by=None,
):
    contractor = register_contractor(
        telegram_id=telegram_id,
        full_name=name,
        phone=phone,
        city="",
        categories=categories or [],
        referred_by=referred_by,
    )
    if contractor and resume:
        update_contractor_profile(contractor["id"], {"resume": resume})
    return contractor


def update_contractor_profile(contractor_id, updates: Dict[str, Any]) -> bool:
    if not updates:
        return True

    allowed = {
        "full_name", "phone", "city", "categories", "credits", "is_vip",
        "phone2", "portfolio_files", "bio", "resume", "social_link", "is_test",
    }

    mapped = {}

    for key, value in dict(updates).items():
        if key in ("id", "telegram_id", "created_at"):
            continue
        elif key == "name":
            mapped["full_name"] = value
        elif key in ("credit", "credits"):
            # ستون واقعی همیشه "credits" است؛ چه کد داخلی "credit"
            # بفرسته چه "credits"، به همون ستون واقعی نگاشت می‌شه.
            mapped["credits"] = value
        elif key == "second_phone":
            mapped["phone2"] = value
        elif key == "social_media":
            mapped["social_link"] = value
        elif key in allowed:
            mapped[key] = value
        else:
            _insert_event("contractor_profile_extra", {
                "contractor_id": contractor_id,
                "field": key,
                "value": value,
            })

    if not mapped:
        return True

    try:
        supabase.table("contractors").update(mapped).eq("id", contractor_id).execute()
        return True
    except Exception as e:
        logger.error(f"update_contractor_profile خطا: {e}")
        return False


def add_portfolio_file(contractor_id, item: Dict[str, Any]) -> bool:
    try:
        contractor = get_contractor_by_id(contractor_id)
        if not contractor:
            return False
        files = contractor.get("portfolio_files") or []
        files.append(item or {})
        supabase.table("contractors").update({
            "portfolio_files": files,
        }).eq("id", contractor_id).execute()
        return True
    except Exception as e:
        logger.error(f"add_portfolio_file خطا: {e}")
        return False


def set_contractor_vip(contractor_id, is_vip: bool) -> bool:
    return update_contractor_profile(contractor_id, {"is_vip": bool(is_vip)})


def set_contractor_vip_by_phone(phone: str, is_vip: bool) -> bool:
    contractor = get_contractor_by_phone(phone)
    if not contractor:
        return False
    return set_contractor_vip(contractor["id"], is_vip)


def add_contractor_credit(contractor_id, amount: int) -> bool:
    contractor = get_contractor_by_id(contractor_id)
    if not contractor:
        return False
    new_credit = _safe_int(contractor.get("credit"), 0) + int(amount)
    return update_contractor_profile(contractor_id, {"credit": new_credit})


def add_contractor_credits(contractor_id, amount: int):
    contractor = get_contractor_by_id(contractor_id)
    if not contractor:
        return 0
    new_credit = _safe_int(contractor.get("credit"), 0) + int(amount)
    update_contractor_profile(contractor_id, {"credit": new_credit})
    return new_credit


def get_contractors_by_category_and_city(category: str, city: str, vip_only: bool = False):
    try:
        q = (
            supabase.table("contractors")
            .select("*")
            .eq("city", city)
            .contains("categories", [category])
        )
        if vip_only:
            q = q.eq("is_vip", True)
        resp = q.execute()
        return [_normalize_contractor(x) for x in (_data(resp) or [])]
    except Exception as e:
        logger.error(f"get_contractors_by_category_and_city خطا: {e}")
        return []


def get_all_contractors(limit: int = 500):
    try:
        resp = (
            supabase.table("contractors")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return [_normalize_contractor(x) for x in (_data(resp) or [])]
    except Exception as e:
        logger.error(f"get_all_contractors خطا: {e}")
        return []


def count_contractors(vip_only: bool = False) -> int:
    try:
        q = supabase.table("contractors").select("id", count="exact")
        if vip_only:
            q = q.eq("is_vip", True)
        resp = q.execute()
        return _count(resp) or 0
    except Exception as e:
        logger.error(f"count_contractors خطا: {e}")
        return 0


def get_contractor(telegram_id: int):
    return get_contractor_by_telegram_id(telegram_id)


# ============================================================
# Projects
# ============================================================

def generate_project_code() -> str:
    try:
        resp = supabase.rpc("generate_project_code_atomic", {}).execute()
        if resp.data:
            return str(resp.data).strip().upper()
    except Exception as e:
        logger.error(f"generate_project_code خطا: {e}")
    return "PRJ" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def create_project_atomic(
    customer_id,
    customer_telegram_id: int,
    city: str,
    category: str,
    description: str,
    budget: str,
    urgency: str = None,
):
    try:
        resp = supabase.rpc("create_project_atomic", {
            "p_customer_id": customer_id,
            "p_customer_telegram_id": customer_telegram_id,
            "p_city": city,
            "p_category": category,
            "p_description": description,
            "p_budget": budget,
            "p_urgency": urgency,
        }).execute()

        row = resp.data
        if isinstance(row, list) and row:
            row = row[0]

        if isinstance(row, dict):
            return _normalize_project(row)

        if row:
            project = get_project_by_code(str(row))
            if project:
                return project

    except Exception as e:
        logger.warning(f"create_project_atomic RPC خطا، fallback فعال شد: {e}")

    try:
        code = generate_project_code()
        payload = {
            "code": code,
            "project_code": code,  # ستون NOT NULL در دیتابیس واقعی
            "customer_id": customer_id,
            "customer_telegram_id": customer_telegram_id,
            "city": city,
            "category": category,
            "categories": [category] if category else [],
            "description": description,
            "budget": budget,
            "urgency": urgency,
            "status": "open",
            "rating_reminder_sent": False,
        }
        resp = supabase.table("projects").insert(payload).execute()
        return _normalize_project(_first(resp))
    except Exception as e:
        logger.error(f"create_project_atomic fallback خطا: {e}")
        return None


def create_project(
    customer_id,
    city: str,
    categories: List[str],
    description: str,
    photo_file_id: str = None,
    budget: str = None,
):
    customer = _select_first("customers", "id", customer_id)
    customer_telegram_id = customer.get("telegram_id") if customer else None
    category = categories[0] if categories else None

    project = create_project_atomic(
        customer_id=customer_id,
        customer_telegram_id=customer_telegram_id,
        city=city,
        category=category,
        description=description,
        budget=budget,
        urgency=None,
    )

    if project and photo_file_id:
        set_project_photo(project["id"], photo_file_id)

    return project


def get_project_by_id(project_id):
    row = _select_first("projects", "id", project_id)
    return _normalize_project(row)


def get_project_by_code(code: str):
    if not code:
        return None

    code_clean = str(code).strip().upper()

    row = _select_first("projects", "code", code_clean)
    if not row and code_clean != str(code).strip():
        row = _select_first("projects", "code", str(code).strip())

    return _normalize_project(row)


def get_project(project_code: str):
    return get_project_by_code(project_code)


def set_project_photo(project_identifier, file_id: str) -> bool:
    try:
        q = supabase.table("projects").update({"photo_file_id": file_id})
        if _is_uuid_like(project_identifier):
            q = q.eq("id", project_identifier)
        else:
            q = q.eq("code", str(project_identifier).strip().upper())
        q.execute()
        return True
    except Exception as e:
        logger.error(f"set_project_photo خطا: {e}")
        return False


def mark_project_posted_vip(project_identifier) -> bool:
    try:
        q = supabase.table("projects").update({"posted_vip_at": _now_iso()})
        if _is_uuid_like(project_identifier):
            q = q.eq("id", project_identifier)
        else:
            q = q.eq("code", str(project_identifier).strip().upper())
        q.execute()
        return True
    except Exception as e:
        logger.error(f"mark_project_posted_vip خطا: {e}")
        return False


def mark_project_posted_public(project_identifier) -> bool:
    try:
        q = supabase.table("projects").update({"posted_public_at": _now_iso()})
        if _is_uuid_like(project_identifier):
            q = q.eq("id", project_identifier)
        else:
            q = q.eq("code", str(project_identifier).strip().upper())
        q.execute()
        return True
    except Exception as e:
        logger.error(f"mark_project_posted_public خطا: {e}")
        return False


def mark_project_vip(project_identifier):
    return mark_project_posted_vip(project_identifier)


def mark_project_public(project_identifier):
    return mark_project_posted_public(project_identifier)


def is_project_already_posted_public(project_code: str) -> bool:
    project = get_project_by_code(project_code)
    return bool(project and project.get("posted_public_at"))


def close_project(project_id_or_code, *args, **kwargs) -> bool:
    hired_contractor_id = kwargs.get("hired_contractor_id")
    rating_score = kwargs.get("rating_score") or kwargs.get("score")
    close_reason = kwargs.get("close_reason") or kwargs.get("reason")

    if len(args) >= 1:
        if args[0] is not None:
            if _is_uuid_like(str(args[0])):
                hired_contractor_id = args[0]
            elif isinstance(args[0], str) and not _is_uuid_like(args[0]) and len(args) == 1:
                close_reason = args[0]
            else:
                hired_contractor_id = args[0]

    if len(args) >= 2 and args[1] is not None:
        rating_score = args[1]

    if len(args) >= 3 and args[2] is not None:
        close_reason = args[2]

    updates = {
        "status": "closed",
        "closed_at": _now_iso(),
    }

    if close_reason:
        updates["closed_reason"] = close_reason

    if hired_contractor_id and _is_uuid_like(str(hired_contractor_id)):
        updates["hired_contractor_id"] = hired_contractor_id

    if close_reason and not hired_contractor_id:
        updates["early_cancel_reason"] = close_reason

    try:
        q = supabase.table("projects").update(updates)
        if _is_uuid_like(project_id_or_code):
            q = q.eq("id", project_id_or_code)
        else:
            q = q.eq("code", str(project_id_or_code).strip().upper())
        q.execute()
        return True
    except Exception as e:
        logger.error(f"close_project خطا: {e}")
        return False


def close_project_early(project_code: str, reason: str) -> bool:
    try:
        supabase.table("projects").update({
            "status": "closed",
            "closed_at": _now_iso(),
            "closed_reason": reason,
            "early_cancel_reason": reason,
        }).eq("code", str(project_code).strip().upper()).execute()
        return True
    except Exception as e:
        logger.error(f"close_project_early خطا: {e}")
        return False


def close_project_by_customer(project_id, customer_id) -> bool:
    return close_project(project_id, close_reason="closed_by_customer")


def set_project_hired_contractor(project_code: str, contractor_id) -> bool:
    try:
        supabase.table("projects").update({
            "hired_contractor_id": contractor_id,
            "status": "in_progress",
        }).eq("code", str(project_code).strip().upper()).execute()
        return True
    except Exception as e:
        logger.error(f"set_project_hired_contractor خطا: {e}")
        return False


# ============================================================
# Declarations / Applications
# ============================================================

def create_declaration(project_id, project_code: str, contractor_id):
    try:
        resp = supabase.table("declarations").insert({
            "project_code": str(project_code).strip().upper(),
            "contractor_id": contractor_id,
        }).execute()
        return _normalize_declaration(_first(resp))
    except Exception as e:
        logger.error(f"create_declaration خطا: {e}")
        return None


def get_declaration(project_code: str, contractor_id):
    code = str(project_code).strip().upper()

    try:
        resp = (
            supabase.table("declarations")
            .select("*")
            .eq("project_code", code)
            .eq("contractor_id", contractor_id)
            .limit(1)
            .execute()
        )
        row = _first(resp)
        if row:
            return _normalize_declaration(row)
    except Exception as e:
        logger.warning(f"get_declaration declarations خطا: {e}")

    try:
        contractor = get_contractor_by_id(contractor_id)
        if not contractor:
            return None

        resp = (
            supabase.table("applications")
            .select("*")
            .eq("project_code", code)
            .eq("contractor_telegram_id", contractor.get("telegram_id"))
            .limit(1)
            .execute()
        )
        return _first(resp)
    except Exception as e:
        logger.warning(f"get_declaration applications خطا: {e}")
        return None


def has_contractor_declared(contractor_id, project_code: str) -> bool:
    return get_declaration(project_code, contractor_id) is not None


def get_declarations_for_project(project_code: str):
    code = str(project_code).strip().upper()
    result = []

    try:
        resp = (
            supabase.table("declarations")
            .select("*, contractors(*)")
            .eq("project_code", code)
            .order("created_at", desc=False)
            .execute()
        )
        rows = _data(resp) or []
        result.extend([_normalize_declaration(x) for x in rows])
    except Exception as e:
        logger.warning(f"get_declarations_for_project declarations join خطا: {e}")
        try:
            resp = (
                supabase.table("declarations")
                .select("*")
                .eq("project_code", code)
                .order("created_at", desc=False)
                .execute()
            )
            for row in (_data(resp) or []):
                item = _normalize_declaration(row)
                result.append(item)
        except Exception as ee:
            logger.error(f"get_declarations_for_project declarations خطا: {ee}")

    if result:
        return result

    try:
        resp = (
            supabase.table("applications")
            .select("*")
            .eq("project_code", code)
            .order("created_at", desc=False)
            .execute()
        )
        rows = _data(resp) or []

        for row in rows:
            item = dict(row)
            contractor = get_contractor_by_telegram_id(item.get("contractor_telegram_id"))
            item["contractor"] = contractor
            item["contractors"] = contractor
            if contractor:
                item["contractor_id"] = contractor.get("id")
            result.append(item)

        return result
    except Exception as e:
        logger.error(f"get_declarations_for_project applications خطا: {e}")
        return []


def get_project_applications(project_id):
    project = get_project_by_id(project_id)
    if not project:
        return []
    return get_declarations_for_project(project.get("code"))


def get_declarations_count_for_project(project_code: str) -> int:
    code = str(project_code).strip().upper()

    try:
        resp = (
            supabase.table("declarations")
            .select("id", count="exact")
            .eq("project_code", code)
            .execute()
        )
        c = _count(resp)
        if c is not None and int(c) > 0:
            return int(c)
    except Exception as e:
        logger.warning(f"count declarations خطا: {e}")

    try:
        resp = (
            supabase.table("applications")
            .select("id", count="exact")
            .eq("project_code", code)
            .execute()
        )
        c = _count(resp)
        return int(c) if c is not None else len(_data(resp) or [])
    except Exception as e:
        logger.error(f"get_declarations_count_for_project خطا: {e}")
        return 0


def get_declarations_count(project_code: str) -> int:
    return get_declarations_count_for_project(project_code)


def _apply_to_project_python_fallback(contractor_id, project_code: str, is_vip: bool = False) -> Dict[str, Any]:
    """
    fallback مستقیم برای اعلام آمادگی، وقتی RPC Supabase خطا می‌دهد.

    سازگار با schema فعلی:
    - contractors.id uuid
    - contractors.credit
    - projects.code
    - projects.status
    - declarations.contractor_id
    - declarations.project_code
    - applications.project_code / contractor_telegram_id / contractor_name / contractor_phone / contractor_resume
    """
    code = str(project_code).strip().upper()

    try:
        contractor = get_contractor_by_id(contractor_id)

        if not contractor:
            return {
                "success": False,
                "reason": "contractor_not_found",
                "remaining_credit": 0,
                "applications_count": None,
                "cap_just_reached": False,
            }

        project = get_project_by_code(code)

        if not project:
            return {
                "success": False,
                "reason": "project_not_found",
                "remaining_credit": _safe_int(contractor.get("credit"), 0),
                "applications_count": None,
                "cap_just_reached": False,
            }

        project_status = project.get("status")

        if project_status == "closed":
            return {
                "success": False,
                "reason": "project_closed",
                "remaining_credit": _safe_int(contractor.get("credit"), 0),
                "applications_count": get_declarations_count_for_project(code),
                "cap_just_reached": False,
            }

        # جلوگیری از اعلام آمادگی تکراری
        existing = get_declaration(code, contractor["id"])

        if existing:
            return {
                "success": False,
                "reason": "already_applied",
                "remaining_credit": _safe_int(contractor.get("credit"), 0),
                "applications_count": get_declarations_count_for_project(code),
                "cap_just_reached": False,
            }

        cap = get_setting_int(
            "application_cap_count",
            get_setting_int("close_early_threshold", 10),
        )

        current_count = get_declarations_count_for_project(code)

        if current_count >= cap:
            try:
                close_project(project["id"], close_reason="cap_reached")
            except Exception:
                pass

            return {
                "success": False,
                "reason": "project_capacity_full",
                "remaining_credit": _safe_int(contractor.get("credit"), 0),
                "applications_count": current_count,
                "cap_just_reached": False,
            }

        current_credit = _safe_int(contractor.get("credit"), 0)

        if current_credit <= 0:
            return {
                "success": False,
                "reason": "insufficient_credit",
                "remaining_credit": 0,
                "applications_count": current_count,
                "cap_just_reached": False,
            }

        remaining_credit = current_credit - 1

        # کم کردن اعتبار پیمانکار
        try:
            supabase.table("contractors").update({
                "credit": remaining_credit,
            }).eq("id", contractor["id"]).execute()
        except Exception as e:
            logger.error(f"fallback apply: خطا در کم کردن credit: {e}")
            return {
                "success": False,
                "reason": "credit_update_failed",
                "remaining_credit": current_credit,
                "applications_count": current_count,
                "cap_just_reached": False,
            }

        # ثبت در declarations
        declaration_inserted = False

        try:
            supabase.table("declarations").insert({
                "contractor_id": contractor["id"],
                "project_code": code,
                "created_at": _now_iso(),
            }).execute()
            declaration_inserted = True
        except Exception as e:
            logger.error(f"fallback apply: خطا در ثبت declarations: {e}")

            # اگر بعد از کم شدن اعتبار ثبت declaration شکست خورد، اعتبار را برگردانیم
            try:
                supabase.table("contractors").update({
                    "credit": current_credit,
                }).eq("id", contractor["id"]).execute()
            except Exception as rollback_error:
                logger.error(f"fallback apply: rollback credit خطا: {rollback_error}")

            return {
                "success": False,
                "reason": "declaration_insert_failed",
                "remaining_credit": current_credit,
                "applications_count": current_count,
                "cap_just_reached": False,
            }

        # ثبت اختیاری در applications برای سازگاری با بخش‌های قدیمی‌تر
        try:
            app_payload = {
                "project_code": code,
                "contractor_telegram_id": contractor.get("telegram_id"),
                "contractor_name": contractor.get("full_name"),
                "contractor_phone": contractor.get("phone"),
                "contractor_resume": contractor.get("resume"),
                "created_at": _now_iso(),
            }

            # اگر applications.project_id با uuid سازگار باشد، این هم ثبت می‌شود.
            if project.get("id") is not None:
                app_payload["project_id"] = project.get("id")

            supabase.table("applications").insert(app_payload).execute()

        except Exception as e:
            # applications برای فلو اصلی حیاتی نیست؛ declarations کافی است.
            logger.warning(f"fallback apply: ثبت applications ناموفق بود ولی ادامه می‌دهیم: {e}")

        new_count = get_declarations_count_for_project(code)
        cap_just_reached = new_count >= cap

        if cap_just_reached:
            try:
                close_project(project["id"], close_reason="cap_reached")
            except Exception as e:
                logger.warning(f"fallback apply: بستن پروژه بعد از تکمیل ظرفیت ناموفق بود: {e}")

        _insert_event("contractor_applied_to_project", {
            "project_code": code,
            "project_id": project.get("id"),
            "contractor_id": contractor.get("id"),
            "contractor_telegram_id": contractor.get("telegram_id"),
            "remaining_credit": remaining_credit,
            "applications_count": new_count,
            "cap_just_reached": cap_just_reached,
            "source": "python_fallback",
            "created_at": _now_iso(),
        })

        return {
            "success": True,
            "reason": "ok",
            "remaining_credit": remaining_credit,
            "applications_count": new_count,
            "cap_just_reached": cap_just_reached,
        }

    except Exception as e:
        logger.error(f"_apply_to_project_python_fallback خطای کلی: {e}", exc_info=True)
        return {
            "success": False,
            "reason": "fallback_error",
            "remaining_credit": 0,
            "applications_count": None,
            "cap_just_reached": False,
        }


def _apply_to_project_python_fallback(contractor_id, project_code: str, is_vip: bool = False) -> Dict[str, Any]:
    """
    fallback مستقیم برای اعلام آمادگی، وقتی RPC Supabase خطا می‌دهد.

    سازگار با schema فعلی:
    - contractors.id uuid
    - contractors.credit
    - projects.code
    - projects.status
    - declarations.contractor_id
    - declarations.project_code
    - applications.project_code / contractor_telegram_id / contractor_name / contractor_phone / contractor_resume
    """
    code = str(project_code).strip().upper()

    try:
        contractor = get_contractor_by_id(contractor_id)

        if not contractor:
            return {
                "success": False,
                "reason": "contractor_not_found",
                "remaining_credit": 0,
                "applications_count": None,
                "cap_just_reached": False,
            }

        project = get_project_by_code(code)

        if not project:
            return {
                "success": False,
                "reason": "project_not_found",
                "remaining_credit": _safe_int(contractor.get("credit"), 0),
                "applications_count": None,
                "cap_just_reached": False,
            }

        project_status = project.get("status")

        if project_status == "closed":
            return {
                "success": False,
                "reason": "project_closed",
                "remaining_credit": _safe_int(contractor.get("credit"), 0),
                "applications_count": get_declarations_count_for_project(code),
                "cap_just_reached": False,
            }

        existing = get_declaration(code, contractor["id"])

        if existing:
            return {
                "success": False,
                "reason": "already_applied",
                "remaining_credit": _safe_int(contractor.get("credit"), 0),
                "applications_count": get_declarations_count_for_project(code),
                "cap_just_reached": False,
            }

        cap = get_setting_int(
            "application_cap_count",
            get_setting_int("close_early_threshold", 10),
        )

        current_count = get_declarations_count_for_project(code)

        if current_count >= cap:
            try:
                close_project(project["id"], close_reason="cap_reached")
            except Exception:
                pass

            return {
                "success": False,
                "reason": "project_capacity_full",
                "remaining_credit": _safe_int(contractor.get("credit"), 0),
                "applications_count": current_count,
                "cap_just_reached": False,
            }

        current_credit = _safe_int(contractor.get("credit"), 0)

        if current_credit <= 0:
            return {
                "success": False,
                "reason": "insufficient_credit",
                "remaining_credit": 0,
                "applications_count": current_count,
                "cap_just_reached": False,
            }

        remaining_credit = current_credit - 1

        try:
            supabase.table("contractors").update({
                "credit": remaining_credit,
            }).eq("id", contractor["id"]).execute()
        except Exception as e:
            logger.error(f"fallback apply: خطا در کم کردن credit: {e}")
            return {
                "success": False,
                "reason": "credit_update_failed",
                "remaining_credit": current_credit,
                "applications_count": current_count,
                "cap_just_reached": False,
            }

        try:
            supabase.table("declarations").insert({
                "contractor_id": contractor["id"],
                "project_code": code,
                "created_at": _now_iso(),
            }).execute()
        except Exception as e:
            logger.error(f"fallback apply: خطا در ثبت declarations: {e}")

            try:
                supabase.table("contractors").update({
                    "credit": current_credit,
                }).eq("id", contractor["id"]).execute()
            except Exception as rollback_error:
                logger.error(f"fallback apply: rollback credit خطا: {rollback_error}")

            return {
                "success": False,
                "reason": "declaration_insert_failed",
                "remaining_credit": current_credit,
                "applications_count": current_count,
                "cap_just_reached": False,
            }

        try:
            app_payload = {
                "project_code": code,
                "contractor_telegram_id": contractor.get("telegram_id"),
                "contractor_name": contractor.get("full_name"),
                "contractor_phone": contractor.get("phone"),
                "contractor_resume": contractor.get("resume"),
                "created_at": _now_iso(),
            }

            if project.get("id") is not None:
                app_payload["project_id"] = project.get("id")

            supabase.table("applications").insert(app_payload).execute()

        except Exception as e:
            logger.warning(f"fallback apply: ثبت applications ناموفق بود ولی ادامه می‌دهیم: {e}")

        new_count = get_declarations_count_for_project(code)
        cap_just_reached = new_count >= cap

        if cap_just_reached:
            try:
                close_project(project["id"], close_reason="cap_reached")
            except Exception as e:
                logger.warning(f"fallback apply: بستن پروژه بعد از تکمیل ظرفیت ناموفق بود: {e}")

        _insert_event("contractor_applied_to_project", {
            "project_code": code,
            "project_id": project.get("id"),
            "contractor_id": contractor.get("id"),
            "contractor_telegram_id": contractor.get("telegram_id"),
            "remaining_credit": remaining_credit,
            "applications_count": new_count,
            "cap_just_reached": cap_just_reached,
            "source": "python_fallback",
            "created_at": _now_iso(),
        })

        return {
            "success": True,
            "reason": "ok",
            "remaining_credit": remaining_credit,
            "applications_count": new_count,
            "cap_just_reached": cap_just_reached,
        }

    except Exception as e:
        logger.error(f"_apply_to_project_python_fallback خطای کلی: {e}", exc_info=True)
        return {
            "success": False,
            "reason": "fallback_error",
            "remaining_credit": 0,
            "applications_count": None,
            "cap_just_reached": False,
        }


def apply_to_project_atomic(contractor_id, project_code: str, is_vip: bool = False) -> Dict[str, Any]:
    """
    اعلام آمادگی پیمانکار برای پروژه.

    اول تلاش می‌کند RPC دیتابیس را اجرا کند.
    اگر RPC به هر دلیلی خطا داد، fallback مستقیم Python اجرا می‌شود.
    """
    code = str(project_code).strip().upper()

    try:
        resp = supabase.rpc("apply_to_project_atomic", {
            "p_contractor_id": contractor_id,
            "p_project_code": code,
            "p_is_vip": bool(is_vip),
        }).execute()

        row = None

        if isinstance(resp.data, list) and resp.data:
            row = resp.data[0]
        elif isinstance(resp.data, dict):
            row = resp.data

        if row:
            result = {
                "success": bool(row.get("success", False)),
                "reason": row.get("reason", "unknown"),
                "remaining_credit": _safe_int(row.get("remaining_credit"), 0),
                "applications_count": row.get("applications_count"),
                "cap_just_reached": bool(row.get("cap_just_reached", False)),
            }

            if result["reason"] not in ("unknown", "rpc_error", "no_response"):
                return result

            logger.warning(f"apply_to_project_atomic RPC پاسخ مبهم داد: {result}")

        else:
            logger.warning("apply_to_project_atomic RPC هیچ row معتبری برنگرداند.")

    except Exception as e:
        logger.error(f"apply_to_project_atomic RPC خطا: {e}", exc_info=True)

    return _apply_to_project_python_fallback(contractor_id, code, is_vip)
    
def deduct_credit_atomic(contractor_id, project_code: str = None, is_vip: bool = False):
    if project_code:
        return apply_to_project_atomic(contractor_id, project_code, is_vip)

    contractor = get_contractor_by_id(contractor_id)
    if not contractor:
        return {"success": False, "reason": "contractor_not_found", "remaining_credit": 0}

    current = _safe_int(contractor.get("credit"), 0)
    if current <= 0:
        return {"success": False, "reason": "insufficient_credit", "remaining_credit": 0}

    remaining = current - 1
    ok = update_contractor_profile(contractor_id, {"credit": remaining})
    return {
        "success": bool(ok),
        "reason": "ok" if ok else "update_failed",
        "remaining_credit": remaining if ok else current,
    }


def apply_to_project(project_id, contractor_id, is_vip: bool = False):
    project = get_project_by_id(project_id)
    if not project:
        return {
            "result": "project_not_found",
            "remaining_credits": None,
            "applications_count": None,
            "cap_just_reached": False,
        }

    result = apply_to_project_atomic(contractor_id, project["code"], is_vip)
    return {
        "result": "success" if result.get("success") else result.get("reason"),
        "remaining_credits": result.get("remaining_credit"),
        "applications_count": result.get("applications_count"),
        "cap_just_reached": result.get("cap_just_reached", False),
    }


# ============================================================
# Ratings
# ============================================================

def _contractor_telegram_from_identifier(identifier):
    if identifier is None:
        return None

    if _is_uuid_like(str(identifier)):
        contractor = get_contractor_by_id(identifier)
        return contractor.get("telegram_id") if contractor else None

    if _is_int_like(identifier):
        return int(identifier)

    contractor = get_contractor_by_id(identifier)
    return contractor.get("telegram_id") if contractor else None


def _customer_telegram_from_identifier(identifier):
    if identifier is None:
        return None

    if _is_uuid_like(str(identifier)):
        customer = _select_first("customers", "id", identifier)
        return customer.get("telegram_id") if customer else None

    if _is_int_like(identifier):
        return int(identifier)

    return None


def create_rating(*args, **kwargs):
    try:
        project_id = None
        project_code = None
        contractor_identifier = None
        customer_identifier = None
        score = None
        comment = None

        if len(args) >= 6:
            project_id, project_code, contractor_identifier, customer_identifier, score, comment = args[:6]
        elif len(args) >= 4:
            project_code, customer_identifier, contractor_identifier, score = args[:4]
            comment = args[4] if len(args) > 4 else kwargs.get("comment")
        else:
            project_id = kwargs.get("project_id")
            project_code = kwargs.get("project_code")
            contractor_identifier = kwargs.get("contractor_telegram_id") or kwargs.get("contractor_id")
            customer_identifier = kwargs.get("customer_telegram_id") or kwargs.get("customer_id")
            score = kwargs.get("score")
            comment = kwargs.get("comment")

        contractor_tg = _contractor_telegram_from_identifier(contractor_identifier)
        customer_tg = _customer_telegram_from_identifier(customer_identifier)

        payload = {
            "project_code": str(project_code).strip().upper() if project_code else None,
            "contractor_telegram_id": contractor_tg,
            "customer_telegram_id": customer_tg,
            "score": int(score),
            "comment": comment,
            "customer_comment": comment,
            "is_approved": False,
            "created_at": _now_iso(),
        }

        if project_id is not None and _is_int_like(project_id):
            payload["project_id"] = int(project_id)

        payload = {k: v for k, v in payload.items() if v is not None}

        resp = supabase.table("ratings").insert(payload).execute()
        return _first(resp)

    except Exception as e:
        logger.error(f"create_rating خطا: {e}")
        return None


def get_pending_ratings(limit: int = 50):
    try:
        try:
            resp = (
                supabase.table("ratings")
                .select("*")
                .or_("is_approved.is.false,is_approved.is.null")
                .order("created_at", desc=False)
                .limit(limit)
                .execute()
            )
        except Exception:
            resp = (
                supabase.table("ratings")
                .select("*")
                .eq("is_approved", False)
                .order("created_at", desc=False)
                .limit(limit)
                .execute()
            )

        rows = _data(resp) or []
        result = []

        for row in rows:
            item = dict(row)
            item["comment"] = item.get("comment") or item.get("customer_comment")
            item["project_code"] = item.get("project_code")
            result.append(item)

        return result
    except Exception as e:
        logger.error(f"get_pending_ratings خطا: {e}")
        return []


def approve_rating(rating_id) -> bool:
    try:
        supabase.table("ratings").update({
            "is_approved": True,
        }).eq("id", rating_id).execute()

        _insert_event("rating_approved", {
            "rating_id": rating_id,
            "approved_at": _now_iso(),
        })

        return True
    except Exception as e:
        logger.error(f"approve_rating خطا: {e}")
        return False


def reject_rating(rating_id) -> bool:
    try:
        supabase.table("ratings").update({
            "is_approved": False,
        }).eq("id", rating_id).execute()

        _insert_event("rating_rejected", {
            "rating_id": rating_id,
            "rejected_at": _now_iso(),
        })

        return True
    except Exception as e:
        logger.error(f"reject_rating خطا: {e}")
        return False


def get_contractor_approved_ratings(contractor_identifier):
    contractor_tg = _contractor_telegram_from_identifier(contractor_identifier)
    if not contractor_tg:
        return []

    try:
        resp = (
            supabase.table("ratings")
            .select("*")
            .eq("contractor_telegram_id", contractor_tg)
            .eq("is_approved", True)
            .order("created_at", desc=True)
            .execute()
        )
        rows = _data(resp) or []

        if not rows:
            resp = (
                supabase.table("ratings")
                .select("*")
                .eq("contractor_telegram_id", contractor_tg)
                .order("created_at", desc=True)
                .execute()
            )
            rows = _data(resp) or []

        return rows
    except Exception as e:
        logger.error(f"get_contractor_approved_ratings خطا: {e}")
        return []


def get_contractor_avg_rating(contractor_identifier):
    rows = get_contractor_approved_ratings(contractor_identifier)

    scores = []
    for row in rows:
        try:
            if row.get("score") is not None:
                scores.append(float(row.get("score")))
        except Exception:
            pass

    if not scores:
        return None

    return round(sum(scores) / len(scores), 1)


def has_rating_for_project(project_code: str) -> bool:
    try:
        resp = (
            supabase.table("ratings")
            .select("id", count="exact")
            .eq("project_code", str(project_code).strip().upper())
            .execute()
        )
        return (_count(resp) or 0) > 0
    except Exception as e:
        logger.error(f"has_rating_for_project خطا: {e}")
        return False


# ============================================================
# Flow Events / Logs
# ============================================================

def log_flow_event(telegram_id: int, role: str, event: str = None, details=None) -> bool:
    event_name = event or role
    flow_type = role if event else "system"

    ok = _insert_event("flow_event", {
        "telegram_id": telegram_id,
        "role": role,
        "event": event_name,
        "details": details if details is not None else {},
        "created_at": _now_iso(),
    })

    try:
        supabase.table("flow_events").insert({
            "telegram_id": telegram_id,
            "flow_type": flow_type,
            "event_name": event_name,
            "created_at": _now_iso(),
        }).execute()
    except Exception as e:
        logger.warning(f"log_flow_event در flow_events خطا: {e}")

    return ok


def get_flow_completion_rate(role: str, start_event: str, end_event: str, days: int = 30):
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        started = (
            supabase.table("flow_events")
            .select("telegram_id", count="exact")
            .eq("flow_type", role)
            .eq("event_name", start_event)
            .gte("created_at", cutoff)
            .execute()
        )

        finished = (
            supabase.table("flow_events")
            .select("telegram_id", count="exact")
            .eq("flow_type", role)
            .eq("event_name", end_event)
            .gte("created_at", cutoff)
            .execute()
        )

        s = _count(started) or 0
        f = _count(finished) or 0

        if s <= 0:
            return 0.0

        return round((f / s) * 100, 1)
    except Exception as e:
        logger.error(f"get_flow_completion_rate خطا: {e}")
        return 0.0


# ============================================================
# Scheduled Tasks
# ============================================================

def create_scheduled_task(task_type: str, payload: Dict[str, Any], run_at: datetime):
    try:
        run_at_value = run_at.isoformat() if isinstance(run_at, datetime) else str(run_at)

        resp = supabase.table("scheduled_tasks").insert({
            "task_type": task_type,
            "payload": payload or {},
            "run_at": run_at_value,
            "status": "pending",
            "created_at": _now_iso(),
        }).execute()

        return _first(resp)
    except Exception as e:
        logger.error(f"create_scheduled_task خطا: {e}")
        return None


def get_pending_scheduled_tasks(limit: int = 100):
    try:
        resp = (
            supabase.table("scheduled_tasks")
            .select("*")
            .eq("status", "pending")
            .lte("run_at", _now_iso())
            .order("run_at", desc=False)
            .limit(limit)
            .execute()
        )

        result = []
        for row in (_data(resp) or []):
            item = dict(row)
            item["type"] = item.get("task_type")
            item["payload"] = _safe_json(item.get("payload"))
            result.append(item)

        return result
    except Exception as e:
        logger.error(f"get_pending_scheduled_tasks خطا: {e}")
        return []


def get_due_tasks():
    return get_pending_scheduled_tasks()


def mark_scheduled_task_done(task_id) -> bool:
    try:
        supabase.table("scheduled_tasks").update({
            "status": "done",
        }).eq("id", task_id).execute()
        return True
    except Exception as e:
        logger.error(f"mark_scheduled_task_done خطا: {e}")
        return False


def mark_task_done(task_id):
    return mark_scheduled_task_done(task_id)


def mark_task_failed(task_id):
    try:
        supabase.table("scheduled_tasks").update({
            "status": "failed",
        }).eq("id", task_id).execute()
        return True
    except Exception as e:
        logger.error(f"mark_task_failed خطا: {e}")
        return False


# ============================================================
# Rating Reminder
# ============================================================

def get_projects_needing_rating_reminder(days: int = 1):
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(days or 1))).isoformat()

        resp = (
            supabase.table("projects")
            .select("*")
            .eq("status", "closed")
            .eq("rating_reminder_sent", False)
            .lte("closed_at", cutoff)
            .execute()
        )

        projects = [_normalize_project(x) for x in (_data(resp) or [])]

        result = []
        for p in projects:
            if p.get("hired_contractor_id") and not has_rating_for_project(p.get("code")):
                result.append(p)

        return result
    except Exception as e:
        logger.error(f"get_projects_needing_rating_reminder خطا: {e}")
        return []


def mark_rating_reminder_sent(project_id_or_code) -> bool:
    try:
        q = supabase.table("projects").update({
            "rating_reminder_sent": True,
        })

        if _is_uuid_like(project_id_or_code):
            q = q.eq("id", project_id_or_code)
        else:
            q = q.eq("code", str(project_id_or_code).strip().upper())

        q.execute()
        return True
    except Exception as e:
        logger.error(f"mark_rating_reminder_sent خطا: {e}")
        return False


# ============================================================
# Payments
# ============================================================

def create_payment(telegram_id: int, payment_type: str, amount, discount_code: str = None, receipt_file_id: str = None):
    try:
        resp = supabase.table("payments").insert({
            "telegram_id": telegram_id,
            "payment_type": payment_type,
            "amount": str(amount),
            "discount_code": discount_code,
            "receipt_file_id": receipt_file_id,
            "status": "pending",
            "created_at": _now_iso(),
        }).execute()
        return _first(resp)
    except Exception as e:
        logger.error(f"create_payment خطا: {e}")
        return None


def get_payment_by_id(payment_id):
    try:
        resp = supabase.table("payments").select("*").eq("id", payment_id).limit(1).execute()
        return _first(resp)
    except Exception as e:
        logger.error(f"get_payment_by_id خطا: {e}")
        return None


def approve_payment(payment_id) -> bool:
    try:
        supabase.table("payments").update({
            "status": "approved",
        }).eq("id", payment_id).execute()
        return True
    except Exception as e:
        logger.error(f"approve_payment خطا: {e}")
        return False


def reject_payment(payment_id) -> bool:
    try:
        supabase.table("payments").update({
            "status": "rejected",
        }).eq("id", payment_id).execute()
        return True
    except Exception as e:
        logger.error(f"reject_payment خطا: {e}")
        return False


# ============================================================
# Reports / Dashboard
# ============================================================

def _count_table(table: str, filters: Optional[List[tuple]] = None) -> int:
    try:
        q = supabase.table(table).select("id", count="exact")

        for op, col, val in filters or []:
            if op == "eq":
                q = q.eq(col, val)
            elif op == "neq":
                q = q.neq(col, val)
            elif op == "gte":
                q = q.gte(col, val)
            elif op == "lte":
                q = q.lte(col, val)
            elif op == "in":
                q = q.in_(col, val)

        resp = q.execute()
        c = _count(resp)
        return int(c) if c is not None else len(_data(resp) or [])
    except Exception as e:
        logger.warning(f"_count_table خطا در {table}: {e}")
        return 0


def get_projects_without_applications(hours: int = 24):
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        resp = (
            supabase.table("projects")
            .select("*")
            .in_("status", ["open", "active"])
            .lte("created_at", cutoff)
            .execute()
        )

        result = []
        for p in (_data(resp) or []):
            project = _normalize_project(p)
            if get_declarations_count_for_project(project.get("code")) == 0:
                result.append(project)

        return result
    except Exception as e:
        logger.error(f"get_projects_without_applications خطا: {e}")
        return []


def get_dashboard_stats(days: int = 30) -> Dict[str, Any]:
    stats = {
        "total_projects": 0,
        "open_projects": 0,
        "closed_projects": 0,
        "vip_projects": 0,
        "total_contractors": 0,
        "vip_contractors": 0,
        "total_declarations": 0,
        "total_payments": 0,
        "total_payments_amount": 0,
        "total_payments_count": 0,
        "projects_no_response": 0,
        "connected_both_sides": 0,
    }

    stats["total_projects"] = _count_table("projects")
    stats["open_projects"] = (
        _count_table("projects", [("eq", "status", "open")])
        + _count_table("projects", [("eq", "status", "active")])
    )
    stats["closed_projects"] = _count_table("projects", [("eq", "status", "closed")])
    stats["total_contractors"] = _count_table("contractors")
    stats["vip_contractors"] = _count_table("contractors", [("eq", "is_vip", True)])
    stats["total_declarations"] = _count_table("declarations") + _count_table("applications")
    stats["projects_no_response"] = len(get_projects_without_applications(hours=24))

    try:
        resp = (
            supabase.table("projects")
            .select("id", count="exact")
            .not_.is_("hired_contractor_id", "null")
            .execute()
        )
        stats["connected_both_sides"] = _count(resp) or 0
    except Exception:
        stats["connected_both_sides"] = 0

    try:
        resp = supabase.table("payments").select("amount,status").execute()
        rows = _data(resp) or []
        total = 0.0
        count = 0

        for row in rows:
            if row.get("status") in ("paid", "approved", "success"):
                total += _safe_float(row.get("amount"), 0)
                count += 1

        stats["total_payments"] = total
        stats["total_payments_amount"] = total
        stats["total_payments_count"] = count
    except Exception as e:
        logger.warning(f"payments stats خطا: {e}")

    return stats


def get_close_reason_stats() -> Dict[str, float]:
    try:
        resp = (
            supabase.table("projects")
            .select("closed_reason")
            .eq("status", "closed")
            .execute()
        )
        rows = _data(resp) or []

        counts = {}
        total = 0

        for row in rows:
            reason = row.get("closed_reason")
            if not reason:
                continue
            counts[reason] = counts.get(reason, 0) + 1
            total += 1

        if total <= 0:
            return {}

        return {
            reason: round((count / total) * 100, 1)
            for reason, count in sorted(counts.items(), key=lambda x: -x[1])
        }
    except Exception as e:
        logger.error(f"get_close_reason_stats خطا: {e}")
        return {}


def get_early_close_stats() -> Dict[str, Any]:
    result = {
        "total_closed": 0,
        "early_closed": 0,
        "early_close_pct": 0,
        "total_early_closes": 0,
        "breakdown": {},
    }

    try:
        closed_resp = (
            supabase.table("projects")
            .select("id,code,early_cancel_reason")
            .eq("status", "closed")
            .execute()
        )
        rows = _data(closed_resp) or []

        result["total_closed"] = len(rows)

        early_rows = [r for r in rows if r.get("early_cancel_reason")]
        result["early_closed"] = len(early_rows)
        result["total_early_closes"] = len(early_rows)

        if rows:
            result["early_close_pct"] = round((len(early_rows) / len(rows)) * 100, 1)

        counts = {}
        for row in early_rows:
            reason = row.get("early_cancel_reason") or "نامشخص"
            counts[reason] = counts.get(reason, 0) + 1

        if early_rows:
            result["breakdown"] = {
                reason: round((count / len(early_rows)) * 100, 1)
                for reason, count in sorted(counts.items(), key=lambda x: -x[1])
            }

        return result
    except Exception as e:
        logger.error(f"get_early_close_stats خطا: {e}")
        return result


def get_weekly_leads_report():
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

        resp = (
            supabase.table("declarations")
            .select("contractor_id")
            .gte("created_at", cutoff)
            .execute()
        )

        counts = {}
        for row in (_data(resp) or []):
            cid = row.get("contractor_id")
            if cid:
                counts[cid] = counts.get(cid, 0) + 1

        report = []
        for contractor_id, count in counts.items():
            c = get_contractor_by_id(contractor_id)
            if c:
                report.append({
                    "full_name": c.get("full_name"),
                    "city": c.get("city"),
                    "declarations_count": count,
                })

        report.sort(key=lambda x: -x["declarations_count"])
        return report
    except Exception as e:
        logger.error(f"get_weekly_leads_report خطا: {e}")
        return []


def get_full_dashboard_metrics() -> Dict[str, Any]:
    """
    نسخه‌ی کامل و غنی متریک‌های داشبورد ادمین.
    داده‌های is_test=True از همه‌ی محاسبات کنار گذاشته می‌شن تا آمار واقعی خراب نشه.
    """
    m: Dict[str, Any] = {}

    try:
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = now - timedelta(days=7)
        month_start = now - timedelta(days=30)

        # ---------- پروژه‌ها ----------
        proj_resp = (
            supabase.table("projects")
            .select(
                "id,code,city,category,categories,status,created_at,"
                "closed_reason,early_cancel_reason,is_test,posted_vip_at"
            )
            .execute()
        )
        all_projects = [p for p in (_data(proj_resp) or []) if not p.get("is_test")]

        m["projects_total"] = len(all_projects)
        m["projects_today"] = sum(
            1 for p in all_projects
            if (dt := _parse_dt(p.get("created_at"))) and dt >= today_start
        )
        m["projects_week"] = sum(
            1 for p in all_projects
            if (dt := _parse_dt(p.get("created_at"))) and dt >= week_start
        )
        m["projects_month"] = sum(
            1 for p in all_projects
            if (dt := _parse_dt(p.get("created_at"))) and dt >= month_start
        )

        by_city: Dict[str, int] = {}
        by_category: Dict[str, int] = {}
        code_to_categories: Dict[str, list] = {}

        for p in all_projects:
            city = p.get("city") or "نامشخص"
            by_city[city] = by_city.get(city, 0) + 1

            cats = p.get("categories") or ([p["category"]] if p.get("category") else [])
            code_to_categories[p.get("code")] = cats
            for c in cats:
                by_category[c] = by_category.get(c, 0) + 1

        m["projects_by_city"] = by_city
        m["projects_by_category"] = by_category

        # ---------- نرخ تکمیل فلو مشتری ----------
        started_resp = (
            supabase.table("flow_events")
            .select("telegram_id", count="exact")
            .eq("flow_type", "customer_new_project")
            .eq("event_name", "start")
            .execute()
        )
        completed_resp = (
            supabase.table("flow_events")
            .select("telegram_id", count="exact")
            .eq("flow_type", "customer_new_project")
            .eq("event_name", "completed")
            .execute()
        )
        started_n = _count(started_resp) or 0
        completed_n = _count(completed_resp) or 0
        m["customer_funnel_started"] = started_n
        m["customer_funnel_completed"] = completed_n
        m["customer_funnel_rate"] = round((completed_n / started_n) * 100, 1) if started_n else 0.0

        # ---------- پیمانکارها ----------
        contractors_resp = (
            supabase.table("contractors")
            .select("id,credits,categories,is_test")
            .execute()
        )
        all_contractors = [c for c in (_data(contractors_resp) or []) if not c.get("is_test")]

        m["contractors_total"] = len(all_contractors)
        m["contractors_active"] = len(all_contractors)
        m["contractors_zero_credit"] = sum(1 for c in all_contractors if (c.get("credits") or 0) <= 0)
        m["contractors_avg_credit"] = (
            round(sum((c.get("credits") or 0) for c in all_contractors) / len(all_contractors), 1)
            if all_contractors else 0
        )

        contractor_by_cat: Dict[str, int] = {}
        for c in all_contractors:
            for cat in (c.get("categories") or []):
                contractor_by_cat[cat] = contractor_by_cat.get(cat, 0) + 1
        m["contractors_by_category"] = contractor_by_cat

        # ---------- اتصال دو طرف (اعلام آمادگی) ----------
        # هم جدول جدید (declarations) و هم جدول قدیمی (applications) شمرده میشن
        decl_resp = supabase.table("declarations").select("project_code").execute()
        declarations = _data(decl_resp) or []

        legacy_resp = supabase.table("applications").select("project_code").execute()
        legacy_apps = _data(legacy_resp) or []

        apps_by_code: Dict[str, int] = {}
        for row in declarations + legacy_apps:
            code = row.get("project_code")
            if code:
                apps_by_code[code] = apps_by_code.get(code, 0) + 1

        total_applications = len(declarations) + len(legacy_apps)
        m["applications_total"] = total_applications
        m["avg_applications_per_project"] = (
            round(total_applications / len(all_projects), 1) if all_projects else 0
        )

        cat_totals: Dict[str, int] = {}
        cat_counts: Dict[str, int] = {}
        for p in all_projects:
            code = p.get("code")
            count_for_project = apps_by_code.get(code, 0)
            for cat in code_to_categories.get(code, []):
                cat_totals[cat] = cat_totals.get(cat, 0) + count_for_project
                cat_counts[cat] = cat_counts.get(cat, 0) + 1

        m["avg_applications_by_category"] = {
            cat: round(cat_totals[cat] / cat_counts[cat], 1) for cat in cat_totals
        }

        m["projects_no_response"] = sum(
            1 for p in all_projects if apps_by_code.get(p.get("code"), 0) == 0
        )

        m["project_to_contractor_ratio"] = (
            round(len(all_projects) / m["contractors_active"], 2)
            if m["contractors_active"] else 0
        )

        # ---------- دلایل بستن پروژه بدون اعلام آمادگی ----------
        reason_counts: Dict[str, int] = {}
        for p in all_projects:
            if p.get("status") == "closed" and apps_by_code.get(p.get("code"), 0) == 0:
                reason = p.get("early_cancel_reason") or p.get("closed_reason") or "نامشخص"
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
        m["closed_no_response_reasons"] = reason_counts

        # ---------- وی‌آی‌پی ----------
        m["vip_delayed_projects"] = sum(1 for p in all_projects if p.get("posted_vip_at"))

        # ---------- پرداخت‌ها ----------
        pay_resp = supabase.table("payments").select("status").execute()
        payments = _data(pay_resp) or []
        m["payments_pending"] = sum(1 for x in payments if x.get("status") == "pending")
        m["payments_approved"] = sum(1 for x in payments if x.get("status") == "approved")
        rejected_n = sum(1 for x in payments if x.get("status") == "rejected")
        m["payments_abandoned"] = rejected_n
        m["payments_abandon_rate"] = (
            round((rejected_n / len(payments)) * 100, 1) if payments else 0
        )

    except Exception as e:
        logger.error(f"get_full_dashboard_metrics خطا: {e}")

    return m




def get_customer(telegram_id: int):
    return get_customer_by_telegram_id(telegram_id)


def mark_project_posted(project_identifier):
    return mark_project_posted_public(project_identifier)


def mark_task_completed(task_id):
    return mark_scheduled_task_done(task_id)


if __name__ == "__main__":
    print("DB health:", db_health_check())
    print("initial_credit:", get_setting_int("initial_credit", 10))
