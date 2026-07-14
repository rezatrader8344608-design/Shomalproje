import os

# ==================== ENV VARIABLES ====================
BOT_TOKEN = os.environ["BOT_TOKEN"]
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()]

MAIN_CHANNEL_ID = int(os.environ["MAIN_CHANNEL_ID"])

VIP_CHANNEL_ID = os.environ.get("VIP_CHANNEL_ID")
VIP_CHANNEL_ID = int(VIP_CHANNEL_ID) if VIP_CHANNEL_ID else None

CITIES = ["تنکابن", "شیرود", "رامسر"]

CATEGORIES = [
    "بازسازی و ساخت",
    "کابینت و MDF",
    "نقاشی",
    "برق کاری",
    "لوله کشی و تاسیسات",
    "کولر و پکیج",
    "درب و پنجره",
    "دکوراسیون و طراحی داخلی",
]

PACKAGE_LABELS = {
    "monthly": "اشتراک ماهانه",
    "credits_3": "بسته ۳ اعتباری",
    "credits_10": "بسته ۱۰ اعتباری",
    "credits_30": "بسته ۳۰ اعتباری",
}

PACKAGE_PRICE_KEYS = {
    "monthly": "price_monthly_subscription",
    "credits_3": "price_3_credits",
    "credits_10": "price_10_credits",
    "credits_30": "price_30_credits",
}

PACKAGE_CREDITS = {
    "credits_3": 3,
    "credits_10": 10,
    "credits_30": 30,
}
