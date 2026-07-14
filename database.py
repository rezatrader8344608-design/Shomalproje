import logging
from datetime import datetime, timezone
from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger(__name__)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# =========================================================
# ==================== APP SETTINGS ======================
# =========================================================

_settings_cache: dict = {}
_settings_cache_time: datetime | None = None
_CACHE_TTL_SECONDS = 5


def get_all_settings(force_refresh: bool = False) -> dict:
    global _settings_cache, _settings_cache_time
    now = datetime.now(timezone.utc)
    if (
        not force_refresh
        and _settings_cache_time
        and (now - _settings_cache_time).total_seconds() < _CACHE_TTL_SECONDS
    ):
        return _settings_cache

    resp = supabase.table("app_settings").select("*").execute()
    _settings_cache = {row["key"]: row["value"] for row in resp.data}
    _settings_cache_time = now
    return _settings_cache


def get_setting(key: str, default=None):
    settings = get_all_settings()
    return settings.get(key, default)


def get_setting_int(key: str, default: int = 0) -> int:
    val = get_setting(key)
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def get_setting_bool(key: str, default: bool = False) -> bool:
    val = get_setting(key)
    if val is None:
        return default
    return str(val).strip().lower() in ("true", "1", "yes", "on")


def set_setting(key: str, value: str) -> tuple[bool, str | None]:
    existing = supabase.table("app_settings").select("*").eq("key", key).execute()
    if not existing.data:
        return False, None

    old_value = existing.data[0]["value"]
    supabase.table("app_settings").update(
        {"value": value, "updated_at": datetime.now(timezone.utc).isoformat()}
    ).eq("key", key).execute()

    get_all_settings(force_refresh=True)
    return True, old_value


# =========================================================
# ==================== CUSTOMERS ==========================
# =========================================================

def get_customer_by_telegram_id(telegram_id: int) -> dict | None:
    resp = supabase.table("customers").select("*").eq("telegram_id", telegram_id).execute()
    return resp.data[0] if resp.data else None


def create_customer(telegram_id: int, name: str, phone: str) -> dict:
    resp = supabase.table("customers").insert({
        "telegram_id": telegram_id,
        "name": name,
        "phone": phone,
    }).execute()
    return resp.data[0]


def update_customer_city(customer_id: int, city: str):
    supabase.table("customers").update({"city": city}).eq("id", customer_id).execute()


def flag_customer(customer_id: int, reason: str):
    supabase.table("customer_flags").insert({
        "customer_id": customer_id,
        "reason": reason,
    }).execute()
    supabase.table("customers").update({"is_flagged": True}).eq("id", customer_id).execute()


def count_customer_monthly_projects(customer_id: int) -> int:
    resp = supabase.rpc("count_customer_monthly_projects", {"p_customer_id": customer_id}).execute()
    return resp.data if isinstance(resp.data, int) else 0


def get_customer_active_project(customer_id: int) -> dict | None:
    resp = (
        supabase.table("projects")
        .select("*")
        .eq("customer_id", customer_id)
        .eq("status", "active")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def get_customer_projects(customer_id: int, limit: int = 20) -> list[dict]:
    resp = (
        supabase.table("projects")
        .select("*")
        .eq("customer_id", customer_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return resp.data


# =========================================================
# ==================== CONTRACTORS ========================
# =========================================================

def get_contractor_by_telegram_id(telegram_id: int) -> dict | None:
    resp = supabase.table("contractors").select("*").eq("telegram_id", telegram_id).execute()
    return resp.data[0] if resp.data else None


def get_contractor_by_phone(phone: str) -> dict | None:
    resp = supabase.table("contractors").select("*").eq("phone", phone).execute()
    return resp.data[0] if resp.data else None


def create_contractor(telegram_id: int, name: str, phone: str, categories: list[str],
                       resume: str | None, referred_by: int | None = None) -> dict:
    initial_credits = get_setting_int("contractor_initial_credits", 10)
    resp = supabase.table("contractors").insert({
        "telegram_id": telegram_id,
        "name": name,
        "phone": phone,
        "categories": categories,
        "resume": resume,
        "credits": initial_credits,
        "referred_by": referred_by,
    }).execute()
    return resp.data[0]


def set_contractor_vip(phone: str, is_vip: bool) -> dict | None:
    resp = supabase.table("contractors").select("*").eq("phone", phone).execute()
    if not resp.data:
        return None
    contractor = resp.data[0]
    supabase.table("contractors").update({"is_vip": is_vip}).eq("id", contractor["id"]).execute()
    contractor["is_vip"] = is_vip
    return contractor


def add_contractor_credits(contractor_id: int, amount: int):
    contractor = supabase.table("contractors").select("credits").eq("id", contractor_id).execute().data[0]
    new_credits = contractor["credits"] + amount
    supabase.table("contractors").update({"credits": new_credits}).eq("id", contractor_id).execute()
    return new_credits


# =========================================================
# ==================== PROJECTS ===========================
# =========================================================

def generate_project_code() -> str:
    resp = supabase.rpc("generate_project_code_atomic", {}).execute()
    return resp.data


def create_project(customer_id: int, city: str, categories: list[str],
                    description: str, photo_file_id: str | None, budget: str | None) -> dict:
    code = generate_project_code()
    resp = supabase.table("projects").insert({
        "project_code": code,
        "customer_id": customer_id,
        "city": city,
        "categories": categories,
        "description": description,
        "photo_file_id": photo_file_id,
        "budget": budget,
    }).execute()
    return resp.data[0]


def set_project_channel_message(project_id: int, message_id: int, is_vip_channel: bool = False):
    field = "vip_channel_message_id" if is_vip_channel else "channel_message_id"
    supabase.table("projects").update({field: message_id}).eq("id", project_id).execute()


def get_project_by_id(project_id: int) -> dict | None:
    resp = supabase.table("projects").select("*").eq("id", project_id).execute()
    return resp.data[0] if resp.data else None


def get_project_by_code(code: str) -> dict | None:
    resp = supabase.table("projects").select("*").eq("project_code", code).execute()
    return resp.data[0] if resp.data else None


def close_project_by_customer(project_id: int, customer_id: int) -> bool:
    resp = supabase.rpc("close_project_by_customer_atomic", {
        "p_project_id": project_id,
        "p_customer_id": customer_id,
    }).execute()
    return bool(resp.data)


def get_project_applications(project_id: int) -> list[dict]:
    resp = (
        supabase.table("applications")
        .select("*, contractors(name, phone, is_vip)")
        .eq("project_id", project_id)
        .order("created_at")
        .execute()
    )
    return resp.data


# =========================================================
# ==================== APPLICATIONS =======================
# =========================================================

def apply_to_project(project_id: int, contractor_id: int, is_vip: bool) -> dict:
    cap_key = "vip_application_cap_count" if is_vip else "application_cap_count"
    cap = get_setting_int(cap_key, 10)

    resp = supabase.rpc("apply_to_project_atomic", {
        "p_project_id": project_id,
        "p_contractor_id": contractor_id,
        "p_cap": cap,
    }).execute()

    row = resp.data[0] if resp.data else {}
    return {
        "result": row.get("result"),
        "remaining_credits": row.get("remaining_credits"),
        "applications_count": row.get("applications_count"),
        "cap_just_reached": row.get("cap_just_reached", False),
    }


# =========================================================
# ==================== SCHEDULED TASKS ====================
# =========================================================

def create_scheduled_task(task_type: str, payload: dict, run_at: datetime):
    supabase.table("scheduled_tasks").insert({
        "task_type": task_type,
        "payload": payload,
        "run_at": run_at.isoformat(),
    }).execute()


def get_due_tasks() -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    resp = (
        supabase.table("scheduled_tasks")
        .select("*")
        .eq("status", "pending")
        .lte("run_at", now)
        .execute()
    )
    return resp.data


def mark_task_done(task_id: int):
    supabase.table("scheduled_tasks").update({"status": "done"}).eq("id", task_id).execute()


def mark_task_failed(task_id: int):
    supabase.table("scheduled_tasks").update({"status": "failed"}).eq("id", task_id).execute()


# =========================================================
# ==================== PAYMENTS ===========================
# =========================================================

def create_payment(contractor_id: int, package_type: str, amount: float,
                    discount_code: str | None, receipt_file_id: str) -> dict:
    resp = supabase.table("payments").insert({
        "contractor_id": contractor_id,
        "package_type": package_type,
        "amount": amount,
        "discount_code": discount_code,
        "receipt_file_id": receipt_file_id,
    }).execute()
    return resp.data[0]


def get_payment_by_id(payment_id: int) -> dict | None:
    resp = supabase.table("payments").select("*, contractors(*)").eq("id", payment_id).execute()
    return resp.data[0] if resp.data else None


def approve_payment(payment_id: int):
    supabase.table("payments").update({
        "status": "approved",
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", payment_id).execute()


def reject_payment(payment_id: int):
    supabase.table("payments").update({"status": "rejected"}).eq("id", payment_id).execute()


# =========================================================
# ==================== RAT
