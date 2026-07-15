"""
bot.py — ربات خدمات ساختمانی شمال | نسخه نهایی یکپارچه
فلو اصلی:
- مشتری پروژه ثبت می‌کند
- پروژه در کانال خصوصی پیمانکارها منتشر می‌شود
- پیمانکار از روی دکمه داخل کانال اعلام آمادگی می‌کند
- اطلاعات پیمانکار برای مشتری ارسال می‌شود
- هر اعلام آمادگی ۱ اعتبار کم می‌کند
- بعد از تکمیل ظرفیت، پروژه بسته می‌شود و در کانال روی همان پیام reply می‌شود
"""

import os
import re
import html
import logging
import asyncio

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    filters,
)
from telegram.error import TelegramError

import db


# ============================================================
# تنظیمات پایه
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN تنظیم نشده است!")


ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip()
}


CONTRACTOR_CHANNEL_ID = int(
    os.getenv("CONTRACTOR_CHANNEL_ID")
    or os.getenv("MAIN_CHANNEL_ID")
    or "0"
)

CONTRACTOR_PRIVATE_LINK = "https://t.me/+o0h74mgG-HgyYjU0"

# VIP فعلاً غیرفعال است.
VIP_ENABLED = False
VIP_CHANNEL_ID = None


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
    "طراحی نما و محوطه",
    "نظافت منزل",
    "باغبانی",
]

URGENCY_LEVELS = [
    "فوری (۱-۲ روز)",
    "این هفته",
    "این ماه",
    "زمان مشخصی ندارم",
]


# ============================================================
# AI Classifier — دفاعی
# ============================================================

_ai_clean_text = None

try:
    import ai_classifier as _ai_classifier_module

    _ai_clean_text = getattr(_ai_classifier_module, "clean_text", None)
    if _ai_clean_text is None:
        logger.info("تابع clean_text داخل ai_classifier وجود ندارد؛ پاکسازی ساده داخلی استفاده می‌شود.")
except Exception as e:
    logger.warning(f"ai_classifier در دسترس نیست: {e}")
    _ai_clean_text = None


def _fallback_clean_text(text: str) -> str:
    if text is None:
        return text

    text = str(text).strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", text)

    replacements = {
        "ي": "ی",
        "ك": "ک",
        "ۀ": "ه",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    return text


async def clean_customer_text(text: str) -> str:
    if not text:
        return text

    if not _ai_clean_text:
        return _fallback_clean_text(text)

    try:
        if asyncio.iscoroutinefunction(_ai_clean_text):
            result = await _ai_clean_text(text)
        else:
            result = await asyncio.to_thread(_ai_clean_text, text)

        return result or _fallback_clean_text(text)

    except Exception as e:
        logger.error(f"خطای AI clean: {e}")
        return _fallback_clean_text(text)


# ============================================================
# اجرای دیتابیس sync در thread جدا
# ============================================================

async def db_call(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)


# ============================================================
# States
# ============================================================

(
    C_CITY,
    C_CATEGORY,
    C_DESCRIPTION,
    C_PHOTO,
    C_BUDGET,
    C_URGENCY,
    C_CONFIRM,
) = range(7)

(TRACK_CODE,) = range(100, 101)

(
    CLOSE_CODE,
    CLOSE_CONFIRM,
    CLOSE_REASON,
    CLOSE_PICK_CONTRACTOR,
    CLOSE_RATING_SCORE,
    CLOSE_RATING_COMMENT,
) = range(200, 206)

(
    R_FULLNAME,
    R_PHONE,
    R_CITY,
    R_CATEGORIES,
    R_EXTRA_CHOICE,
    R_EXTRA_PORTFOLIO,
    R_EXTRA_RESUME,
    R_EXTRA_SOCIAL,
    R_EXTRA_BIO,
) = range(300, 309)

(
    P_MENU,
    P_FULLNAME,
    P_PHONE2,
    P_PORTFOLIO,
    P_RESUME,
    P_SOCIAL,
    P_BIO,
    P_CATEGORIES,
) = range(400, 408)

(
    ADMIN_MENU,
    ADMIN_SETTINGS,
    ADMIN_SET_VALUE,
    ADMIN_MODERATION,
) = range(500, 504)


# ============================================================
# دکمه‌ها
# ============================================================

BTN_CANCEL = "❌ لغو"
BTN_RESTART = "🔄 شروع دوباره"
BTN_SKIP = "➖ رد کردن"
BTN_BACK_MAIN = "🏠 بازگشت به منوی اصلی"
BTN_DONE = "✅ اتمام"

BTN_NEW_PROJECT = "🆕 ثبت پروژه جدید"
BTN_TRACK_PROJECT = "🔍 پیگیری پروژه"
BTN_CLOSE_PROJECT = "✅ پایان پروژه"

BTN_BUY_CREDIT = "💳 خرید اعتبار"

BTN_MY_PROFILE = "👤 پروفایل من"
BTN_EDIT_PROFILE = "✏️ ویرایش پروفایل"

BTN_ADMIN_DASHBOARD = "📊 داشبورد"
BTN_ADMIN_SETTINGS = "⚙️ تنظیمات"
BTN_ADMIN_MODERATION = "🗂 نظارت (تایید نظرات)"
BTN_ADMIN_TEST = "🧪 تست کامل فلو"

CUSTOMER_ROLE = "customer"
CONTRACTOR_ROLE = "contractor"
ADMIN_ROLE = "admin"


# ============================================================
# فیلترهای مشترک ورودی مراحل
# ============================================================
# نکته مهم: قبلاً هر مرحله از فلوها با TEXT_INPUT_FILTER ساده
# ثبت می‌شد. این فیلتر متن دکمه‌های لغو/شروع دوباره رو هم به‌عنوان «ورودی همون
# مرحله» قورت می‌داد و اجازه نمی‌داد پیام به fallbacks برسه - در نتیجه کاربر
# توی هر خطای ورودی (مثلاً شماره نامعتبر) توی لوپ گیر می‌کرد و دکمه لغو/شروع
# دوباره عملاً هیچ اثری نداشت. این سه فیلتر زیر، دکمه‌های کنترلی رو صریحاً
# استثنا می‌کنن تا همیشه به fallbacks برسن.

_CANCEL_RESTART_EXCLUDE = ~filters.Regex(
    f"^({re.escape(BTN_CANCEL)}|{re.escape(BTN_RESTART)})$"
)

TEXT_INPUT_FILTER = filters.TEXT & ~filters.COMMAND & _CANCEL_RESTART_EXCLUDE
PHOTO_INPUT_FILTER = (filters.PHOTO | filters.TEXT) & ~filters.COMMAND & _CANCEL_RESTART_EXCLUDE
CONTACT_INPUT_FILTER = (filters.CONTACT | filters.TEXT) & ~filters.COMMAND & _CANCEL_RESTART_EXCLUDE


# ============================================================
# کیبوردها
# ============================================================

def kb_cancel_restart(extra_rows=None):
    rows = extra_rows[:] if extra_rows else []
    rows.append([BTN_CANCEL, BTN_RESTART])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def kb_list(options, per_row=2, extra_rows=None, with_cancel=True):
    rows = []
    row = []

    for i, opt in enumerate(options, 1):
        row.append(KeyboardButton(opt))
        if i % per_row == 0:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    if extra_rows:
        rows.extend(extra_rows)

    if with_cancel:
        rows.append([BTN_CANCEL, BTN_RESTART])

    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def kb_customer_main():
    return ReplyKeyboardMarkup(
        [
            [BTN_NEW_PROJECT],
            [BTN_TRACK_PROJECT, BTN_CLOSE_PROJECT],
        ],
        resize_keyboard=True,
    )


def kb_contractor_main():
    """
    اعلام آمادگی از منوی ربات حذف شده است.
    پیمانکار فقط از طریق دکمه داخل کانال پروژه‌ها اعلام آمادگی می‌کند.
    """
    return ReplyKeyboardMarkup(
        [
            [BTN_MY_PROFILE, BTN_EDIT_PROFILE],
            [BTN_BUY_CREDIT],
        ],
        resize_keyboard=True,
    )


def kb_admin_main():
    return ReplyKeyboardMarkup(
        [
            [BTN_ADMIN_DASHBOARD, BTN_ADMIN_SETTINGS],
            [BTN_ADMIN_MODERATION, BTN_ADMIN_TEST],
        ],
        resize_keyboard=True,
    )


def kb_share_phone():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📱 اشتراک شماره تماس", request_contact=True)],
            [BTN_CANCEL, BTN_RESTART],
        ],
        resize_keyboard=True,
    )


def kb_skip_cancel():
    return ReplyKeyboardMarkup(
        [
            [BTN_SKIP],
            [BTN_CANCEL, BTN_RESTART],
        ],
        resize_keyboard=True,
    )


def kb_confirm():
    return ReplyKeyboardMarkup(
        [
            ["✅ تایید و ارسال"],
            [BTN_CANCEL, BTN_RESTART],
        ],
        resize_keyboard=True,
    )


def kb_categories(selected: list):
    rows = []

    for cat in CATEGORIES:
        mark = "✅ " if cat in selected else ""
        rows.append([KeyboardButton(f"{mark}{cat}")])

    rows.append([KeyboardButton("🏁 پایان انتخاب دسته‌بندی‌ها")])
    rows.append([BTN_CANCEL, BTN_RESTART])

    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def kb_extra_choice():
    return ReplyKeyboardMarkup(
        [
            ["🖼 نمونه کار (عکس)"],
            ["📄 رزومه (متن)"],
            ["🔗 شبکه اجتماعی"],
            ["📝 توضیحات بیشتر"],
            [BTN_DONE + " و ثبت‌نام"],
            [BTN_CANCEL, BTN_RESTART],
        ],
        resize_keyboard=True,
    )


def kb_edit_profile_menu():
    return ReplyKeyboardMarkup(
        [
            ["✏️ نام و نام خانوادگی"],
            ["📂 ویرایش دسته‌بندی"],
            ["📱 افزودن/ویرایش شماره دوم"],
            ["🖼 افزودن نمونه کار"],
            ["📄 ویرایش رزومه"],
            ["🔗 ویرایش شبکه اجتماعی"],
            [BTN_DONE],
            [BTN_CANCEL, BTN_RESTART],
        ],
        resize_keyboard=True,
    )


# ============================================================
# ابزارها
# ============================================================

def esc(value) -> str:
    if value is None:
        return ""
    return html.escape(str(value))


def normalize_phone(raw: str):
    if not raw:
        return None

    p = re.sub(r"[^\d+]", "", raw.strip())

    if p.startswith("+98"):
        p = "0" + p[3:]
    elif p.startswith("98") and len(p) > 10:
        p = "0" + p[2:]

    if re.fullmatch(r"09\d{9}", p):
        return p

    return None


async def safe_get_declarations_count(code: str) -> int:
    try:
        if hasattr(db, "get_declarations_count"):
            return await db_call(db.get_declarations_count, code)

        if hasattr(db, "get_declarations_count_for_project"):
            return await db_call(db.get_declarations_count_for_project, code)

    except Exception as e:
        logger.error(f"خطا در گرفتن تعداد اعلام آمادگی‌ها: {e}")

    return 0


async def safe_set_project_photo(project_id, file_id):
    try:
        if hasattr(db, "set_project_photo"):
            return await db_call(db.set_project_photo, project_id, file_id)
    except Exception as e:
        logger.error(f"خطا در ذخیره عکس پروژه: {e}")

    return None


async def safe_mark_project_posted_public(project_id):
    try:
        if hasattr(db, "mark_project_posted_public"):
            return await db_call(db.mark_project_posted_public, project_id)
    except Exception as e:
        logger.error(f"خطا در mark_project_posted_public: {e}")

    return None


async def safe_answer_callback(query, text: str, show_alert: bool = True):
    try:
        await query.answer(text=text, show_alert=show_alert)
    except Exception:
        pass


async def safe_send_private_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, **kwargs) -> bool:
    try:
        await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)
        return True
    except TelegramError as e:
        logger.warning(f"ارسال پیام خصوصی ناموفق بود: {e}")
        return False


# ============================================================
# فلو کانال پیمانکارها
# ============================================================

def build_project_channel_text(project_code: str, p: dict) -> str:
    city = p.get("city") or "نامشخص"
    category = p.get("category") or "نامشخص"
    description = p.get("description") or "-"
    budget = p.get("budget") or "ثبت نشده"
    urgency = p.get("urgency") or "نامشخص"

    return (
        f"🆕 <b>پروژه جدید</b>\n\n"
        f"🆔 <b>کد پروژه:</b> <code>{esc(project_code)}</code>\n"
        f"🏙 <b>شهر:</b> {esc(city)}\n"
        f"🛠 <b>دسته‌بندی:</b> {esc(category)}\n"
        f"💰 <b>بودجه:</b> {esc(budget)}\n"
        f"⏱ <b>فوریت:</b> {esc(urgency)}\n\n"
        f"📝 <b>شرح پروژه:</b>\n"
        f"{esc(description)}\n\n"
        f"برای اعلام آمادگی، روی دکمه زیر بزنید 👇"
    )


async def publish_project_to_contractor_channel(
    context: ContextTypes.DEFAULT_TYPE,
    project_id,
    project_code,
    p,
    photo_file_id=None,
):
    """
    ارسال پروژه فقط به کانال اصلی پیمانکارها.
    VIP فعلاً غیرفعال است.
    """
    if not CONTRACTOR_CHANNEL_ID:
        logger.error("CONTRACTOR_CHANNEL_ID تنظیم نشده است.")
        return False

    if not project_code:
        logger.error("project_code خالی است؛ ارسال به کانال انجام نشد.")
        return False

    caption = build_project_channel_text(project_code, p)

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "👷 اعلام آمادگی",
                    callback_data=f"apply_project:{project_code}",
                )
            ]
        ]
    )

    try:
        if photo_file_id:
            msg = await context.bot.send_photo(
                chat_id=CONTRACTOR_CHANNEL_ID,
                photo=photo_file_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
        else:
            msg = await context.bot.send_message(
                chat_id=CONTRACTOR_CHANNEL_ID,
                text=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )

        if project_id and hasattr(db, "set_project_channel_message"):
            await db_call(db.set_project_channel_message, project_id, msg.message_id, False)

        await safe_mark_project_posted_public(project_id)

        logger.info(f"پروژه {project_code} در کانال پیمانکارها منتشر شد.")
        return True

    except TelegramError as e:
        logger.error(f"خطا در ارسال پروژه به کانال پیمانکارها: {e}")
        return False


def build_contractor_profile_for_customer(contractor: dict, project: dict, avg_rating=None) -> str:
    project_code = project.get("code") or project.get("project_code") or "-"
    full_name = contractor.get("full_name") or contractor.get("name") or "نامشخص"
    phone = contractor.get("phone") or "ثبت نشده"
    city = contractor.get("city") or "نامشخص"

    categories = contractor.get("categories") or []
    if isinstance(categories, list):
        categories_text = "، ".join(categories) if categories else "ثبت نشده"
    else:
        categories_text = str(categories)

    resume = contractor.get("resume") or "ثبت نشده"
    social_media = contractor.get("social_media") or "ثبت نشده"
    bio = contractor.get("bio") or "ثبت نشده"

    rating_text = f"{avg_rating}/۵ ⭐" if avg_rating else "هنوز امتیازی ثبت نشده"

    return (
        f"👷 <b>یک پیمانکار برای پروژه شما اعلام آمادگی کرد</b>\n\n"
        f"🆔 <b>کد پروژه:</b> <code>{esc(project_code)}</code>\n\n"
        f"👤 <b>نام:</b> {esc(full_name)}\n"
        f"📞 <b>شماره تماس:</b> {esc(phone)}\n"
        f"🏙 <b>شهر:</b> {esc(city)}\n"
        f"🛠 <b>تخصص‌ها:</b> {esc(categories_text)}\n"
        f"⭐ <b>امتیاز:</b> {esc(rating_text)}\n\n"
        f"📄 <b>رزومه:</b>\n{esc(resume)}\n\n"
        f"🔗 <b>شبکه اجتماعی:</b>\n{esc(social_media)}\n\n"
        f"📝 <b>توضیحات بیشتر:</b>\n{esc(bio)}"
    )


async def send_project_closed_reply_to_channel(
    context: ContextTypes.DEFAULT_TYPE,
    project: dict,
    reason_text: str,
) -> bool:
    """
    پیام بسته شدن پروژه را روی همان پیام پروژه در کانال reply می‌کند.
    """
    if not CONTRACTOR_CHANNEL_ID:
        return False

    if not project:
        return False

    channel_message_id = project.get("channel_message_id")

    if not channel_message_id:
        logger.warning("channel_message_id برای پروژه پیدا نشد؛ پیام بسته شدن بدون reply ارسال می‌شود.")
        try:
            await context.bot.send_message(
                chat_id=CONTRACTOR_CHANNEL_ID,
                text=reason_text,
            )
            return True
        except TelegramError as e:
            logger.error(f"خطا در ارسال پیام بسته شدن بدون reply: {e}")
            return False

    try:
        await context.bot.send_message(
            chat_id=CONTRACTOR_CHANNEL_ID,
            text=reason_text,
            reply_to_message_id=channel_message_id,
        )
        return True

    except TelegramError as e:
        logger.error(f"خطا در reply بسته شدن پروژه در کانال: {e}")
        return False


async def handle_apply_project_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    اعلام آمادگی پیمانکار از دکمه داخل کانال.
    این تنها مسیر اعلام آمادگی است.
    """
    query = update.callback_query

    try:
        data = query.data or ""

        if not data.startswith("apply_project:"):
            await safe_answer_callback(query, "درخواست نامعتبر است.", show_alert=True)
            return

        project_code = data.split(":", 1)[1].strip().upper()
        telegram_user = query.from_user
        telegram_id = telegram_user.id

        contractor = await db_call(db.get_contractor_by_telegram_id, telegram_id)

        if not contractor:
            await safe_answer_callback(
                query,
                "برای اعلام آمادگی، ابتدا باید ثبت‌نام پیمانکار را در ربات کامل کنید.",
                show_alert=True,
            )

            bot_username = context.bot.username
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🤖 ورود به ربات و ثبت‌نام",
                            url=f"https://t.me/{bot_username}",
                        )
                    ]
                ]
            )

            await safe_send_private_message(
                context,
                telegram_id,
                "برای اعلام آمادگی، ابتدا باید ثبت‌نام پیمانکار را در ربات کامل کنید.\n"
                "بعد از تکمیل ثبت‌نام می‌توانید از طریق همین دکمه داخل کانال اعلام آمادگی کنید.",
                reply_markup=keyboard,
            )
            return

        project = await db_call(db.get_project_by_code, project_code)

        if not project:
            await safe_answer_callback(query, "❌ این پروژه پیدا نشد.", show_alert=True)
            return

        if project.get("status") == "closed":
            close_reason = project.get("close_reason") or project.get("closed_reason")

            if close_reason == "cap_reached":
                msg = "ظرفیت این پروژه تکمیل شد! پروژه‌های دیگر را اعلام آمادگی کن."
            elif close_reason == "closed_by_customer":
                msg = "این پروژه توسط مشتری بسته شده است."
            else:
                msg = "این پروژه بسته شده و دیگر امکان اعلام آمادگی ندارد."

            await safe_answer_callback(query, msg, show_alert=True)

            await safe_send_private_message(
                context,
                telegram_id,
                f"❌ {msg}",
                reply_markup=kb_contractor_main(),
            )
            return

        result = await db_call(
            db.apply_to_project_atomic,
            contractor["id"],
            project_code,
            False,
        )

        logger.info(
            f"نتیجه apply_to_project_atomic برای پروژه {project_code} "
            f"و پیمانکار تلگرام {telegram_id}: {result}"
        )

        if not result or not result.get("success"):
            reason = result.get("reason", "unknown") if isinstance(result, dict) else "unknown"

            if reason == "already_applied":
                msg = "شما قبلاً برای این پروژه اعلام آمادگی کرده‌اید."
            elif reason in ("insufficient_credit", "no_credit"):
                msg = "اعتبار شما کافی نیست."
            elif reason in ("project_closed", "project_capacity_full"):
                msg = "ظرفیت این پروژه تکمیل شد! پروژه‌های دیگر را اعلام آمادگی کن."
            elif reason == "project_not_found":
                msg = "این پروژه پیدا نشد."
            elif reason == "contractor_not_found":
                msg = "اطلاعات پیمانکار شما پیدا نشد."
            else:
                msg = "ثبت اعلام آمادگی با خطا مواجه شد. لطفاً دوباره تلاش کنید."

            await safe_answer_callback(query, f"❌ {msg}", show_alert=True)

            await safe_send_private_message(
                context,
                telegram_id,
                f"❌ {msg}",
                reply_markup=kb_contractor_main(),
            )
            return

        remaining_credit = result.get("remaining_credit", contractor.get("credit", 0))

        await db_call(
            db.log_flow_event,
            telegram_id,
            "contractor_apply_from_channel",
            f"project:{project_code}",
        )

        avg_rating = await db_call(db.get_contractor_avg_rating, contractor["telegram_id"])

        customer_telegram_id = project.get("customer_telegram_id")

        if customer_telegram_id:
            customer_text = build_contractor_profile_for_customer(
                contractor,
                project,
                avg_rating=avg_rating,
            )

            try:
                await context.bot.send_message(
                    chat_id=customer_telegram_id,
                    text=customer_text,
                    parse_mode=ParseMode.HTML,
                )
            except TelegramError as e:
                logger.error(f"ارسال اطلاعات پیمانکار به مشتری ناموفق بود: {e}")

        success_msg = (
            f"✅ اعلام آمادگی شما برای پروژه {project_code} ثبت شد.\n"
            f"اطلاعات پروفایل شما برای مشتری ارسال شد.\n\n"
            f"💳 اعتبار باقی‌مانده: {remaining_credit}"
        )

        await safe_answer_callback(
            query,
            "✅ اعلام آمادگی ثبت شد و اطلاعات شما برای مشتری ارسال شد.",
            show_alert=True,
        )

        await safe_send_private_message(
            context,
            telegram_id,
            success_msg,
            reply_markup=kb_contractor_main(),
        )

        if result.get("cap_just_reached"):
            count = result.get("applications_count") or await safe_get_declarations_count(project_code)

            close_text = (
                "🔒 پروژه بسته شد.\n"
                f"{count} پیمانکار اعلام آمادگی کردند و اطلاعاتشان برای مشتری ارسال شد."
            )

            fresh_project = await db_call(db.get_project_by_code, project_code)
            await send_project_closed_reply_to_channel(context, fresh_project, close_text)

    except Exception as e:
        logger.error(f"خطا در handle_apply_project_callback: {e}", exc_info=True)

        try:
            await safe_answer_callback(
                query,
                "خطای غیرمنتظره‌ای رخ داد. لطفاً دوباره تلاش کنید.",
                show_alert=True,
            )
        except Exception:
            pass


# ============================================================
# /start
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    user = update.effective_user
    telegram_id = user.id

    if telegram_id in ADMIN_IDS:
        context.user_data["role"] = ADMIN_ROLE
        await update.message.reply_text(
            "🛠 به پنل مدیریت خوش آمدید.",
            reply_markup=kb_admin_main(),
        )
        return ConversationHandler.END

    contractor = await db_call(db.get_contractor_by_telegram_id, telegram_id)

    if contractor:
        context.user_data["role"] = CONTRACTOR_ROLE
        context.user_data["contractor"] = contractor

        await update.message.reply_text(
            f"سلام {contractor.get('full_name', '')} عزیز 👷\n"
            f"اعتبار فعلی شما: {contractor.get('credit', 0)} اعلام آمادگی\n\n"
            "برای دیدن پروژه‌ها و اعلام آمادگی، وارد کانال خصوصی پیمانکاران شوید.\n"
            "از دکمه‌های زیر هم می‌توانید پروفایل و اعتبار خود را مدیریت کنید:",
            reply_markup=kb_contractor_main(),
        )

        return ConversationHandler.END

    await update.message.reply_text(
        "به ربات خدمات ساختمانی شمال خوش آمدید! 🏗\n\n"
        "شما چه نقشی دارید؟",
        reply_markup=ReplyKeyboardMarkup(
            [
                ["👤 من مشتری هستم"],
                ["👷 من پیمانکار هستم"],
            ],
            resize_keyboard=True,
        ),
    )

    context.user_data["role"] = "pending"
    return ConversationHandler.END


cmd_start = start


async def role_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "👤 من مشتری هستم":
        context.user_data["role"] = CUSTOMER_ROLE

        await update.message.reply_text(
            "خوش آمدید! از دکمه‌های زیر استفاده کنید:",
            reply_markup=kb_customer_main(),
        )

    elif text == "👷 من پیمانکار هستم":
        return await cmd_register_contractor_entry(update, context)

    else:
        await update.message.reply_text("لطفاً /start را بزنید.")


async def cancel_generic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = context.user_data.get("role")

    context.user_data.clear()
    context.user_data["role"] = role

    if role == CONTRACTOR_ROLE:
        await update.message.reply_text(
            "لغو شد. به منوی پیمانکار بازگشتید.",
            reply_markup=kb_contractor_main(),
        )

    elif role == ADMIN_ROLE:
        await update.message.reply_text(
            "لغو شد.",
            reply_markup=kb_admin_main(),
        )

    else:
        context.user_data["role"] = CUSTOMER_ROLE
        await update.message.reply_text(
            "لغو شد. به منوی اصلی بازگشتید.",
            reply_markup=kb_customer_main(),
        )

    return ConversationHandler.END


# ============================================================
# مشتری: ثبت پروژه
# ============================================================

async def new_project_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id

    customer = await db_call(
        db.get_or_create_customer,
        telegram_id,
        update.effective_user.full_name or "مشتری",
    )

    if not customer:
        await update.message.reply_text("خطا در ثبت اطلاعات. لطفاً بعداً تلاش کنید.")
        return ConversationHandler.END

    max_monthly = await db_call(db.get_setting_int, "max_monthly_projects", 10)
    monthly_count = await db_call(db.get_customer_monthly_count, customer["id"])

    if monthly_count >= max_monthly:
        await update.message.reply_text(
            f"⚠️ شما در این ماه به سقف {max_monthly} پروژه رسیده‌اید.",
            reply_markup=kb_customer_main(),
        )
        return ConversationHandler.END

    context.user_data["role"] = CUSTOMER_ROLE
    context.user_data["customer"] = customer
    context.user_data["new_project"] = {}

    await db_call(db.log_flow_event, telegram_id, "customer_new_project", "start")

    await update.message.reply_text(
        "🏙 لطفاً شهر مورد نظر خود را انتخاب کنید:",
        reply_markup=kb_list(CITIES, per_row=1),
    )

    return C_CITY


async def np_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text not in CITIES:
        await update.message.reply_text(
            "لطفاً یکی از شهرهای موجود را انتخاب کنید.",
            reply_markup=kb_list(CITIES, per_row=1),
        )
        return C_CITY

    context.user_data["new_project"]["city"] = text

    await update.message.reply_text(
        "🛠 دسته‌بندی خدمات مورد نیاز را انتخاب کنید:",
        reply_markup=kb_list(CATEGORIES, per_row=2),
    )

    return C_CATEGORY


async def np_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text not in CATEGORIES:
        await update.message.reply_text(
            "لطفاً یکی از دسته‌بندی‌های موجود را انتخاب کنید.",
            reply_markup=kb_list(CATEGORIES, per_row=2),
        )
        return C_CATEGORY

    context.user_data["new_project"]["category"] = text

    await update.message.reply_text(
        "📝 لطفاً توضیح کاملی از پروژه خود بنویسید:",
        reply_markup=kb_cancel_restart(),
    )

    return C_DESCRIPTION


async def np_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if not text or len(text.strip()) < 5:
        await update.message.reply_text("توضیحات خیلی کوتاه است. لطفاً کمی بیشتر توضیح دهید.")
        return C_DESCRIPTION

    cleaned = await clean_customer_text(text.strip())
    context.user_data["new_project"]["description"] = cleaned

    await update.message.reply_text(
        "📷 در صورت تمایل یک عکس ارسال کنید یا رد کردن را بزنید:",
        reply_markup=kb_skip_cancel(),
    )

    return C_PHOTO


async def np_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        context.user_data["new_project"]["photo_file_id"] = update.message.photo[-1].file_id

    elif update.message.text == BTN_SKIP:
        context.user_data["new_project"]["photo_file_id"] = None

    else:
        await update.message.reply_text(
            "لطفاً یک عکس ارسال کنید یا «رد کردن» را بزنید.",
            reply_markup=kb_skip_cancel(),
        )
        return C_PHOTO

    await update.message.reply_text(
        "💰 بودجه تقریبی خود را وارد کنید:",
        reply_markup=kb_cancel_restart(),
    )

    return C_BUDGET


async def np_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if not text or len(text.strip()) < 1:
        await update.message.reply_text("لطفاً بودجه تقریبی را وارد کنید.")
        return C_BUDGET

    context.user_data["new_project"]["budget"] = text.strip()

    await update.message.reply_text(
        "⏱ فوریت انجام کار چقدر است؟",
        reply_markup=kb_list(URGENCY_LEVELS, per_row=1),
    )

    return C_URGENCY


async def np_urgency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text not in URGENCY_LEVELS:
        await update.message.reply_text(
            "لطفاً یکی از گزینه‌های موجود را انتخاب کنید.",
            reply_markup=kb_list(URGENCY_LEVELS, per_row=1),
        )
        return C_URGENCY

    context.user_data["new_project"]["urgency"] = text
    p = context.user_data["new_project"]

    summary = (
        "📋 خلاصه پروژه شما:\n\n"
        f"🏙 شهر: {p['city']}\n"
        f"🛠 دسته‌بندی: {p['category']}\n"
        f"📝 توضیحات: {p['description']}\n"
        f"💰 بودجه: {p['budget']}\n"
        f"⏱ فوریت: {p['urgency']}\n"
        f"📷 عکس: {'دارد' if p.get('photo_file_id') else 'ندارد'}\n\n"
        "آیا اطلاعات فوق را تایید و ارسال می‌کنید؟"
    )

    await update.message.reply_text(summary, reply_markup=kb_confirm())

    return C_CONFIRM


async def np_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text != "✅ تایید و ارسال":
        await update.message.reply_text(
            "لطفاً «تایید و ارسال» را بزنید یا لغو کنید.",
            reply_markup=kb_confirm(),
        )
        return C_CONFIRM

    telegram_id = update.effective_user.id
    customer = context.user_data["customer"]
    p = context.user_data["new_project"]

    result = await db_call(
        db.create_project_atomic,
        customer["id"],
        telegram_id,
        p["city"],
        p["category"],
        p["description"],
        p["budget"],
        p["urgency"],
    )

    if not result:
        await update.message.reply_text(
            "❌ خطایی رخ داد. لطفاً دوباره تلاش کنید.",
            reply_markup=kb_customer_main(),
        )
        return ConversationHandler.END

    project_row = result[0] if isinstance(result, list) and result else result

    project_code = None
    project_id = None

    if isinstance(project_row, dict):
        project_code = project_row.get("code") or project_row.get("project_code")
        project_id = project_row.get("id")

    if not project_code and isinstance(result, str):
        project_code = result

    if not project_id and project_code:
        project = await db_call(db.get_project_by_code, project_code)
        if project:
            project_id = project.get("id")

    if p.get("photo_file_id") and project_id:
        await safe_set_project_photo(project_id, p["photo_file_id"])

    await db_call(db.log_flow_event, telegram_id, "customer_new_project", "completed")

    sent_to_channel = await publish_project_to_contractor_channel(
        context,
        project_id,
        project_code,
        p,
        photo_file_id=p.get("photo_file_id"),
    )

    if sent_to_channel:
        msg = (
            f"✅ پروژه شما با موفقیت ثبت شد و برای پیمانکاران ارسال گردید.\n\n"
            f"کد پیگیری پروژه شما:\n<code>{esc(project_code)}</code>\n\n"
            "این کد را برای پیگیری یا بستن پروژه نگه دارید."
        )
    else:
        msg = (
            f"✅ پروژه شما ثبت شد.\n\n"
            f"کد پیگیری پروژه شما:\n<code>{esc(project_code)}</code>\n\n"
            "⚠️ اما ارسال پروژه به کانال پیمانکاران با مشکل مواجه شد. ادمین بررسی می‌کند."
        )

    await update.message.reply_text(
        msg,
        parse_mode=ParseMode.HTML,
        reply_markup=kb_customer_main(),
    )

    context.user_data.pop("new_project", None)

    return ConversationHandler.END


# ============================================================
# مشتری: پیگیری پروژه
# ============================================================

async def track_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔍 لطفاً کد پروژه خود را وارد کنید:",
        reply_markup=kb_cancel_restart(),
    )

    return TRACK_CODE


async def track_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip().upper()
    project = await db_call(db.get_project_by_code, code)

    if not project:
        await update.message.reply_text(
            "❌ کد وارد شده اشتباه است یا پروژه‌ای یافت نشد.",
            reply_markup=kb_cancel_restart(),
        )
        return TRACK_CODE

    telegram_id = update.effective_user.id

    if project.get("customer_telegram_id") != telegram_id:
        await update.message.reply_text(
            "❌ این پروژه متعلق به شما نیست.",
            reply_markup=kb_customer_main(),
        )
        return ConversationHandler.END

    declarations_count = await safe_get_declarations_count(code)

    status_map = {
        "open": "🟢 باز",
        "active": "🟢 باز",
        "in_progress": "🟡 در حال انجام",
        "closed": "🔴 بسته شده",
    }

    status_fa = status_map.get(project.get("status"), project.get("status"))

    await update.message.reply_text(
        f"📋 وضعیت پروژه {code}:\n\n"
        f"وضعیت: {status_fa}\n"
        f"🛠 دسته‌بندی: {project.get('category')}\n"
        f"🏙 شهر: {project.get('city')}\n"
        f"📩 تعداد اعلام آمادگی: {declarations_count}",
        reply_markup=kb_customer_main(),
    )

    return ConversationHandler.END


# ============================================================
# مشتری: پایان پروژه
# ============================================================

async def close_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id

    customer = await db_call(
        db.get_or_create_customer,
        telegram_id,
        update.effective_user.full_name or "مشتری",
    )

    if not customer:
        await update.message.reply_text(
            "خطا در بررسی اطلاعات شما. لطفاً دوباره تلاش کنید.",
            reply_markup=kb_customer_main(),
        )
        return ConversationHandler.END

    open_projects = []

    try:
        if hasattr(db, "get_customer_open_projects"):
            open_projects = await db_call(db.get_customer_open_projects, customer["id"])

        elif hasattr(db, "get_open_projects_by_customer"):
            open_projects = await db_call(db.get_open_projects_by_customer, customer["id"])

        elif hasattr(db, "get_customer_projects"):
            all_projects = await db_call(db.get_customer_projects, customer["id"])
            open_projects = [
                p for p in (all_projects or [])
                if p.get("status") != "closed"
            ]

    except Exception as e:
        logger.error(f"خطا در بررسی پروژه‌های باز مشتری: {e}")
        open_projects = []

    if not open_projects:
        await update.message.reply_text(
            "شما هنوز پروژه‌ی بازی برای بستن ندارید.\n\n"
            "اگر می‌خواهید پروژه‌ای ثبت کنید، از دکمه «ثبت پروژه جدید» استفاده کنید.",
            reply_markup=kb_customer_main(),
        )
        return ConversationHandler.END

    if len(open_projects) == 1:
        project = open_projects[0]
        code = project.get("code") or project.get("project_code")

        context.user_data["close_project"] = project

        await update.message.reply_text(
            f"شما فقط یک پروژه باز دارید:\n\n"
            f"🆔 کد پروژه: {code}\n"
            f"🏙 شهر: {project.get('city') or '—'}\n"
            f"🛠 دسته‌بندی: {project.get('category') or '—'}\n\n"
            "آیا می‌خواهید همین پروژه را ببندید؟\n"
            "برای ادامه کد پروژه را ارسال کنید یا لغو کنید.",
            reply_markup=kb_cancel_restart(),
        )

        return CLOSE_CODE

    rows = []

    for p in open_projects:
        code = p.get("code") or p.get("project_code")
        if code:
            rows.append([str(code)])

    await update.message.reply_text(
        "✅ پروژه‌های باز شما:\n\n"
        "کد پروژه‌ای که می‌خواهید ببندید را از دکمه‌های زیر انتخاب کنید:",
        reply_markup=kb_list([], extra_rows=rows),
    )

    return CLOSE_CODE


async def close_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip().upper()
    project = await db_call(db.get_project_by_code, code)
    telegram_id = update.effective_user.id

    if not project:
        await update.message.reply_text(
            "❌ کد وارد شده اشتباه است یا پروژه‌ای یافت نشد.",
            reply_markup=kb_cancel_restart(),
        )
        return CLOSE_CODE

    if project.get("customer_telegram_id") != telegram_id:
        await update.message.reply_text(
            "❌ این پروژه متعلق به شما نیست.",
            reply_markup=kb_customer_main(),
        )
        return ConversationHandler.END

    if project.get("status") == "closed":
        await update.message.reply_text(
            "این پروژه قبلاً بسته شده است.",
            reply_markup=kb_customer_main(),
        )
        return ConversationHandler.END

    context.user_data["close_project"] = project

    declarations_count = await safe_get_declarations_count(code)
    min_threshold = await db_call(db.get_setting_int, "close_early_threshold", 10)

    if declarations_count < min_threshold:
        context.user_data["close_is_early"] = True

        await update.message.reply_text(
            f"⚠️ تا الان فقط {declarations_count} پیمانکار اعلام آمادگی کرده‌اند.\n\n"
            "لطفاً دلیل بستن زودهنگام پروژه را انتخاب یا وارد کنید:",
            reply_markup=kb_list(
                [
                    "قیمت مناسب نبود",
                    "به روش دیگری حل شد",
                    "دیگر نیازی ندارم",
                    "منصرف شدم",
                ],
                per_row=1,
            ),
        )

        return CLOSE_REASON

    context.user_data["close_is_early"] = False

    declarations = await db_call(db.get_declarations_for_project, code)

    if declarations:
        names = []
        context.user_data["close_declarations"] = declarations

        for d in declarations:
            c = await db_call(db.get_contractor_by_id, d["contractor_id"])
            if c:
                names.append(c.get("full_name", "پیمانکار"))

        context.user_data["close_declarations_names"] = names

        rows = [[n] for n in names]
        rows.append(["🚫 بدون انتخاب پیمانکار خاص"])

        await update.message.reply_text(
            "کدام پیمانکار پروژه را انجام داد؟",
            reply_markup=kb_list([], extra_rows=rows),
        )

        return CLOSE_PICK_CONTRACTOR

    return await _finalize_close(update, context, hired_contractor=None)


async def close_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text.strip()
    project = context.user_data["close_project"]
    telegram_id = update.effective_user.id

    await db_call(db.close_project, project["id"], None, None, reason)

    if hasattr(db, "flag_customer"):
        await db_call(db.flag_customer, telegram_id, reason)

    await db_call(db.log_flow_event, telegram_id, "customer_close_early", reason)

    fresh_project = await db_call(db.get_project_by_code, project.get("code") or project.get("project_code"))

    await send_project_closed_reply_to_channel(
        context,
        fresh_project or project,
        "🔒 پروژه بسته شد.\nمشتری این پروژه را بست.",
    )

    await update.message.reply_text(
        "پروژه شما بسته شد. متشکریم 🙏",
        reply_markup=kb_customer_main(),
    )

    context.user_data.pop("close_project", None)

    return ConversationHandler.END


async def close_pick_contractor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    hired_contractor = None

    if text != "🚫 بدون انتخاب پیمانکار خاص":
        names = context.user_data.get("close_declarations_names", [])
        declarations = context.user_data.get("close_declarations", [])

        if text in names:
            idx = names.index(text)
            contractor_id = declarations[idx]["contractor_id"]
            hired_contractor = await db_call(db.get_contractor_by_id, contractor_id)
        else:
            await update.message.reply_text("لطفاً از بین گزینه‌های موجود انتخاب کنید.")
            return CLOSE_PICK_CONTRACTOR

    return await _finalize_close(update, context, hired_contractor=hired_contractor)


async def _finalize_close(update: Update, context: ContextTypes.DEFAULT_TYPE, hired_contractor):
    project = context.user_data["close_project"]
    telegram_id = update.effective_user.id

    await db_call(
        db.close_project,
        project["id"],
        hired_contractor["id"] if hired_contractor else None,
        None,
        "closed_by_customer",
    )

    await db_call(db.log_flow_event, telegram_id, "customer_close", "completed")

    fresh_project = await db_call(db.get_project_by_code, project.get("code") or project.get("project_code"))

    await send_project_closed_reply_to_channel(
        context,
        fresh_project or project,
        "🔒 پروژه بسته شد.\nمشتری این پروژه را بست.",
    )

    if hired_contractor:
        context.user_data["rating_contractor"] = hired_contractor
        context.user_data["rating_project"] = project

        await update.message.reply_text(
            "لطفاً به پیمانکاری که پروژه را انجام داد امتیاز دهید:",
            reply_markup=kb_list([str(i) for i in range(1, 6)], per_row=5),
        )

        return CLOSE_RATING_SCORE

    await update.message.reply_text(
        "پروژه با موفقیت بسته شد. متشکریم 🙏",
        reply_markup=kb_customer_main(),
    )

    context.user_data.pop("close_project", None)

    return ConversationHandler.END


async def close_rating_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text not in [str(i) for i in range(1, 6)]:
        await update.message.reply_text("لطفاً عددی بین ۱ تا ۵ انتخاب کنید.")
        return CLOSE_RATING_SCORE

    context.user_data["rating_score"] = int(text)

    await update.message.reply_text(
        "اگر می‌خواهید توضیحی هم بنویسید:",
        reply_markup=kb_skip_cancel(),
    )

    return CLOSE_RATING_COMMENT


async def close_rating_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    comment = None if update.message.text == BTN_SKIP else update.message.text.strip()
    comment = await clean_customer_text(comment) if comment else None

    project = context.user_data["rating_project"]
    contractor = context.user_data["rating_contractor"]
    telegram_id = update.effective_user.id
    score = context.user_data["rating_score"]

    await db_call(
        db.create_rating,
        project["id"],
        project["code"],
        contractor["telegram_id"],
        telegram_id,
        score,
        comment,
    )

    await update.message.reply_text(
        "🙏 متشکریم از ثبت نظر شما!",
        reply_markup=kb_customer_main(),
    )

    for k in [
        "close_project",
        "rating_contractor",
        "rating_project",
        "rating_score",
        "close_declarations",
        "close_declarations_names",
    ]:
        context.user_data.pop(k, None)

    return ConversationHandler.END


# ============================================================
# پیمانکار: ثبت‌نام
# ============================================================

async def cmd_register_contractor_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id

    existing = await db_call(db.get_contractor_by_telegram_id, telegram_id)

    if existing:
        context.user_data["role"] = CONTRACTOR_ROLE

        await update.message.reply_text(
            "شما قبلاً ثبت‌نام کرده‌اید ✅\n\n"
            "برای دیدن پروژه‌ها و اعلام آمادگی، وارد کانال خصوصی پیمانکاران شوید:",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "📢 عضویت در کانال پیمانکاران",
                            url=CONTRACTOR_PRIVATE_LINK,
                        )
                    ]
                ]
            ),
        )

        await update.message.reply_text(
            "از دکمه‌های زیر هم می‌توانید استفاده کنید:",
            reply_markup=kb_contractor_main(),
        )

        return ConversationHandler.END

    context.user_data["reg"] = {}
    context.user_data["role"] = "registering_contractor"

    await db_call(db.log_flow_event, telegram_id, "contractor_register", "start")

    await update.message.reply_text(
        "👷 لطفاً نام و نام خانوادگی خود را وارد کنید:",
        reply_markup=kb_cancel_restart(),
    )

    return R_FULLNAME


async def r_fullname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if len(text) < 3:
        await update.message.reply_text("نام وارد شده خیلی کوتاه است.")
        return R_FULLNAME

    context.user_data["reg"]["full_name"] = await clean_customer_text(text)

    await update.message.reply_text(
        "📱 لطفاً شماره تماس خود را وارد کنید یا به اشتراک بگذارید:",
        reply_markup=kb_share_phone(),
    )

    return R_PHONE


async def r_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.contact.phone_number if update.message.contact else update.message.text
    phone = normalize_phone(raw)

    if not phone:
        await update.message.reply_text(
            "شماره معتبر نیست. با فرمت ۰۹xxxxxxxxx وارد کنید.",
            reply_markup=kb_share_phone(),
        )
        return R_PHONE

    existing = await db_call(db.get_contractor_by_phone, phone)

    if existing:
        await update.message.reply_text(
            "این شماره قبلاً ثبت شده است. اگر حساب شماست /start را بزنید.",
            reply_markup=ReplyKeyboardRemove(),
        )
        context.user_data.clear()
        return ConversationHandler.END

    context.user_data["reg"]["phone"] = phone

    await update.message.reply_text(
        "🏙 شهر فعالیت خود را انتخاب کنید:",
        reply_markup=kb_list(CITIES, per_row=1),
    )

    return R_CITY


async def r_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text not in CITIES:
        await update.message.reply_text(
            "لطفاً یکی از شهرهای موجود را انتخاب کنید.",
            reply_markup=kb_list(CITIES, per_row=1),
        )
        return R_CITY

    context.user_data["reg"]["city"] = text
    context.user_data["reg"]["categories"] = []

    await update.message.reply_text(
        "🛠 دسته‌بندی‌های کاری خود را انتخاب کنید:",
        reply_markup=kb_categories([]),
    )

    return R_CATEGORIES


async def r_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    selected = context.user_data["reg"].setdefault("categories", [])

    if text == "🏁 پایان انتخاب دسته‌بندی‌ها":
        if not selected:
            await update.message.reply_text(
                "لطفاً حداقل یک دسته‌بندی انتخاب کنید.",
                reply_markup=kb_categories(selected),
            )
            return R_CATEGORIES

        await update.message.reply_text(
            "چه اطلاعات دیگری می‌خواهید اضافه کنید؟",
            reply_markup=kb_extra_choice(),
        )

        return R_EXTRA_CHOICE

    cat = text.replace("✅ ", "").strip()

    if cat not in CATEGORIES:
        await update.message.reply_text(
            "لطفاً از دکمه‌های موجود انتخاب کنید.",
            reply_markup=kb_categories(selected),
        )
        return R_CATEGORIES

    if cat in selected:
        selected.remove(cat)
    else:
        selected.append(cat)

    await update.message.reply_text(
        f"دسته‌بندی‌های انتخابی: {', '.join(selected) if selected else 'هیچ‌کدام'}",
        reply_markup=kb_categories(selected),
    )

    return R_CATEGORIES


async def r_extra_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text.startswith(BTN_DONE):
        return await _finalize_registration(update, context)

    if text == "🖼 نمونه کار (عکس)":
        await update.message.reply_text(
            "لطفاً عکس نمونه کار خود را ارسال کنید:",
            reply_markup=kb_skip_cancel(),
        )
        return R_EXTRA_PORTFOLIO

    if text == "📄 رزومه (متن)":
        await update.message.reply_text(
            "رزومه یا سوابق کاری خود را بنویسید:",
            reply_markup=kb_skip_cancel(),
        )
        return R_EXTRA_RESUME

    if text == "🔗 شبکه اجتماعی":
        await update.message.reply_text(
            "لینک صفحه کاری خود را ارسال کنید:",
            reply_markup=kb_skip_cancel(),
        )
        return R_EXTRA_SOCIAL

    if text == "📝 توضیحات بیشتر":
        await update.message.reply_text(
            "توضیحات بیشتری که می‌خواهید نمایش داده شود:",
            reply_markup=kb_skip_cancel(),
        )
        return R_EXTRA_BIO

    await update.message.reply_text(
        "لطفاً یکی از گزینه‌های موجود را انتخاب کنید.",
        reply_markup=kb_extra_choice(),
    )

    return R_EXTRA_CHOICE


async def r_extra_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reg = context.user_data["reg"]
    reg.setdefault("portfolio_files", [])

    if update.message.text == BTN_SKIP:
        pass

    elif update.message.photo:
        reg["portfolio_files"].append(
            {
                "type": "photo",
                "file_id": update.message.photo[-1].file_id,
            }
        )

    elif update.message.document:
        reg["portfolio_files"].append(
            {
                "type": "document",
                "file_id": update.message.document.file_id,
            }
        )

    else:
        await update.message.reply_text(
            "لطفاً عکس/فایل ارسال کنید یا رد کنید.",
            reply_markup=kb_skip_cancel(),
        )
        return R_EXTRA_PORTFOLIO

    await update.message.reply_text(
        "آیتم دیگری برای اضافه کردن دارید؟",
        reply_markup=kb_extra_choice(),
    )

    return R_EXTRA_CHOICE


async def r_extra_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reg = context.user_data["reg"]

    if update.message.text != BTN_SKIP:
        reg["resume"] = await clean_customer_text(update.message.text.strip())

    await update.message.reply_text(
        "آیتم دیگری برای اضافه کردن دارید؟",
        reply_markup=kb_extra_choice(),
    )

    return R_EXTRA_CHOICE


async def r_extra_social(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reg = context.user_data["reg"]

    if update.message.text != BTN_SKIP:
        reg["social_media"] = update.message.text.strip()

    await update.message.reply_text(
        "آیتم دیگری برای اضافه کردن دارید؟",
        reply_markup=kb_extra_choice(),
    )

    return R_EXTRA_CHOICE


async def r_extra_bio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reg = context.user_data["reg"]

    if update.message.text != BTN_SKIP:
        reg["bio"] = await clean_customer_text(update.message.text.strip())

    await update.message.reply_text(
        "آیتم دیگری برای اضافه کردن دارید؟",
        reply_markup=kb_extra_choice(),
    )

    return R_EXTRA_CHOICE


async def _finalize_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    reg = context.user_data["reg"]

    referred_by = context.user_data.get("referred_by")

    contractor = await db_call(
        db.register_contractor,
        telegram_id,
        reg["full_name"],
        reg["phone"],
        reg["city"],
        reg["categories"],
        referred_by,
        False,
    )

    if not contractor:
        await update.message.reply_text(
            "❌ خطایی در ثبت‌نام رخ داد.",
            reply_markup=ReplyKeyboardRemove(),
        )
        context.user_data.clear()
        return ConversationHandler.END

    extra_updates = {}

    for key in ("resume", "social_media", "bio"):
        if reg.get(key):
            extra_updates[key] = reg[key]

    if extra_updates:
        await db_call(db.update_contractor_profile, contractor["id"], extra_updates)

    for item in reg.get("portfolio_files", []):
        await db_call(db.add_portfolio_file, contractor["id"], item)

    await db_call(db.log_flow_event, telegram_id, "contractor_register", "completed")

    # اگر قبلاً روی لینک کانال کلیک کرده و درخواستش pending مانده، خودکار تایید شود
    await approve_pending_join_request(context, telegram_id)

    context.user_data.clear()
    context.user_data["role"] = CONTRACTOR_ROLE

    join_keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📢 عضویت در کانال پیمانکاران",
                    url=CONTRACTOR_PRIVATE_LINK,
                )
            ]
        ]
    )

    await update.message.reply_text(
        "🎉 ثبت‌نام شما با موفقیت انجام شد!\n\n"
        f"✅ اعتبار اولیه شما: {contractor.get('credit', 10)} اعلام آمادگی\n\n"
        "برای دیدن پروژه‌های جدید و اعلام آمادگی، در کانال خصوصی پیمانکاران عضو شوید:",
        reply_markup=join_keyboard,
    )

    await update.message.reply_text(
        "از این منو می‌توانید پروفایل و اعتبار خود را مدیریت کنید:",
        reply_markup=kb_contractor_main(),
    )

    return ConversationHandler.END


# ============================================================
# پیمانکار: پروفایل
# ============================================================

async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    contractor = await db_call(db.get_contractor_by_telegram_id, telegram_id)

    if not contractor:
        await update.message.reply_text("پروفایلی برای شما یافت نشد. لطفاً /start را بزنید.")
        return

    avg_rating = await db_call(db.get_contractor_avg_rating, telegram_id)
    rating_text = f"⭐ {avg_rating}/۵" if avg_rating else "هنوز امتیازی ثبت نشده"

    text = (
        f"👤 پروفایل شما:\n\n"
        f"نام: {contractor.get('full_name')}\n"
        f"شماره اصلی: {contractor.get('phone')}\n"
        f"شماره دوم: {contractor.get('second_phone') or '—'}\n"
        f"شهر: {contractor.get('city')}\n"
        f"دسته‌بندی‌ها: {', '.join(contractor.get('categories') or [])}\n"
        f"اعتبار: {contractor.get('credit', 0)}\n"
        f"وضعیت VIP: {'✅ بله' if contractor.get('is_vip') else '❌ خیر'}\n"
        f"امتیاز: {rating_text}\n"
        f"رزومه: {contractor.get('resume') or '—'}\n"
        f"شبکه اجتماعی: {contractor.get('social_media') or '—'}\n"
        f"توضیحات: {contractor.get('bio') or '—'}\n"
        f"تعداد نمونه‌کار: {len(contractor.get('portfolio_files') or [])}\n\n"
        f"📢 کانال پروژه‌ها:\n{CONTRACTOR_PRIVATE_LINK}"
    )

    await update.message.reply_text(text, reply_markup=kb_contractor_main())


async def edit_profile_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    contractor = await db_call(db.get_contractor_by_telegram_id, telegram_id)

    if not contractor:
        await update.message.reply_text(
            "پروفایلی یافت نشد.",
            reply_markup=kb_contractor_main(),
        )
        return ConversationHandler.END

    context.user_data["editing_contractor"] = contractor
    context.user_data["role"] = CONTRACTOR_ROLE

    await update.message.reply_text(
        "کدام بخش پروفایل را می‌خواهید ویرایش کنید؟",
        reply_markup=kb_edit_profile_menu(),
    )

    return P_MENU


async def p_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text == BTN_DONE:
        await update.message.reply_text(
            "✅ پروفایل شما به‌روزرسانی شد.",
            reply_markup=kb_contractor_main(),
        )
        context.user_data.pop("editing_contractor", None)
        return ConversationHandler.END

    if text == "✏️ نام و نام خانوادگی":
        await update.message.reply_text(
            "نام و نام خانوادگی جدید را وارد کنید:",
            reply_markup=kb_cancel_restart(),
        )
        return P_FULLNAME

    if text == "📂 ویرایش دسته‌بندی":
        contractor = context.user_data["editing_contractor"]
        current = list(contractor.get("categories") or [])
        context.user_data["editing_categories"] = current

        await update.message.reply_text(
            f"دسته‌بندی‌های فعلی: {', '.join(current) if current else 'هیچ‌کدام'}\n"
            "روی هرکدوم بزنید تا انتخاب/لغو بشه، در پایان دکمه پایان رو بزنید:",
            reply_markup=kb_categories(current),
        )
        return P_CATEGORIES

    if text == "📱 افزودن/ویرایش شماره دوم":
        await update.message.reply_text(
            "شماره دوم خود را وارد کنید یا به اشتراک بگذارید:",
            reply_markup=kb_share_phone(),
        )
        return P_PHONE2

    if text == "🖼 افزودن نمونه کار":
        await update.message.reply_text(
            "عکس/فایل نمونه کار خود را ارسال کنید:",
            reply_markup=kb_skip_cancel(),
        )
        return P_PORTFOLIO

    if text == "📄 ویرایش رزومه":
        await update.message.reply_text(
            "رزومه جدید را وارد کنید:",
            reply_markup=kb_skip_cancel(),
        )
        return P_RESUME

    if text == "🔗 ویرایش شبکه اجتماعی":
        await update.message.reply_text(
            "لینک شبکه اجتماعی جدید را وارد کنید:",
            reply_markup=kb_skip_cancel(),
        )
        return P_SOCIAL

    await update.message.reply_text(
        "لطفاً یکی از گزینه‌های موجود را انتخاب کنید.",
        reply_markup=kb_edit_profile_menu(),
    )

    return P_MENU


async def p_fullname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if len(text) < 3:
        await update.message.reply_text("نام خیلی کوتاه است.")
        return P_FULLNAME

    cleaned = await clean_customer_text(text)
    contractor = context.user_data["editing_contractor"]

    await db_call(db.update_contractor_profile, contractor["id"], {"full_name": cleaned})

    await update.message.reply_text(
        "✅ نام بروزرسانی شد.",
        reply_markup=kb_edit_profile_menu(),
    )

    return P_MENU


async def p_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    selected = context.user_data.setdefault("editing_categories", [])

    if text == "🏁 پایان انتخاب دسته‌بندی‌ها":
        if not selected:
            await update.message.reply_text(
                "لطفاً حداقل یک دسته‌بندی انتخاب کنید.",
                reply_markup=kb_categories(selected),
            )
            return P_CATEGORIES

        contractor = context.user_data["editing_contractor"]
        await db_call(db.update_contractor_profile, contractor["id"], {"categories": selected})
        contractor["categories"] = selected

        await update.message.reply_text(
            "✅ دسته‌بندی‌ها بروزرسانی شد.",
            reply_markup=kb_edit_profile_menu(),
        )
        context.user_data.pop("editing_categories", None)
        return P_MENU

    cat = text.replace("✅ ", "").strip()

    if cat not in CATEGORIES:
        await update.message.reply_text(
            "لطفاً از دکمه‌های موجود انتخاب کنید.",
            reply_markup=kb_categories(selected),
        )
        return P_CATEGORIES

    if cat in selected:
        selected.remove(cat)
    else:
        selected.append(cat)

    await update.message.reply_text(
        f"دسته‌بندی‌های انتخابی: {', '.join(selected) if selected else 'هیچ‌کدام'}",
        reply_markup=kb_categories(selected),
    )

    return P_CATEGORIES


async def p_phone2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.contact.phone_number if update.message.contact else update.message.text
    phone = normalize_phone(raw)

    if not phone:
        await update.message.reply_text(
            "شماره نامعتبر است. دوباره وارد کنید:",
            reply_markup=kb_share_phone(),
        )
        return P_PHONE2

    contractor = context.user_data["editing_contractor"]

    await db_call(db.update_contractor_profile, contractor["id"], {"second_phone": phone})

    await update.message.reply_text(
        "✅ شماره دوم ثبت شد.",
        reply_markup=kb_edit_profile_menu(),
    )

    return P_MENU


async def p_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contractor = context.user_data["editing_contractor"]

    if update.message.text == BTN_SKIP:
        pass

    elif update.message.photo:
        await db_call(
            db.add_portfolio_file,
            contractor["id"],
            {
                "type": "photo",
                "file_id": update.message.photo[-1].file_id,
            },
        )

    elif update.message.document:
        await db_call(
            db.add_portfolio_file,
            contractor["id"],
            {
                "type": "document",
                "file_id": update.message.document.file_id,
            },
        )

    else:
        await update.message.reply_text(
            "لطفاً عکس/فایل ارسال کنید یا رد کنید.",
            reply_markup=kb_skip_cancel(),
        )
        return P_PORTFOLIO

    await update.message.reply_text(
        "✅ ثبت شد.",
        reply_markup=kb_edit_profile_menu(),
    )

    return P_MENU


async def p_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contractor = context.user_data["editing_contractor"]

    if update.message.text != BTN_SKIP:
        cleaned = await clean_customer_text(update.message.text.strip())
        await db_call(db.update_contractor_profile, contractor["id"], {"resume": cleaned})

    await update.message.reply_text(
        "✅ ثبت شد.",
        reply_markup=kb_edit_profile_menu(),
    )

    return P_MENU


async def p_social(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contractor = context.user_data["editing_contractor"]

    if update.message.text != BTN_SKIP:
        await db_call(
            db.update_contractor_profile,
            contractor["id"],
            {"social_media": update.message.text.strip()},
        )

    await update.message.reply_text(
        "✅ ثبت شد.",
        reply_markup=kb_edit_profile_menu(),
    )

    return P_MENU


# ============================================================
# خرید اعتبار
# ============================================================

async def buy_credit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💳 برای خرید اعتبار، لطفاً با پشتیبانی/ادمین در ارتباط باشید.\n\n"
        "این بخش پرداخت هنوز به درگاه متصل نشده است.",
        reply_markup=kb_contractor_main(),
    )


# ============================================================
# ادمین
# ============================================================

async def admin_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    await update.message.reply_text("⏳ در حال محاسبه متریک‌ها...")

    m = await db_call(db.get_full_dashboard_metrics)
    if not m:
        m = {}

    city_lines = "\n".join(
        f"  • {k}: {v}" for k, v in m.get("projects_by_city", {}).items()
    ) or "  —"
    cat_lines = "\n".join(
        f"  • {k}: {v}" for k, v in m.get("projects_by_category", {}).items()
    ) or "  —"
    contractor_cat_lines = "\n".join(
        f"  • {k}: {v}" for k, v in m.get("contractors_by_category", {}).items()
    ) or "  —"
    avg_by_cat_lines = "\n".join(
        f"  • {k}: {v}" for k, v in m.get("avg_applications_by_category", {}).items()
    ) or "  —"
    closed_reason_lines = "\n".join(
        f"  • {k}: {v}" for k, v in m.get("closed_no_response_reasons", {}).items()
    ) or "  —"

    text = (
        "📊 داشبورد شمال‌پروژه\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        "🏗 پروژه‌ها\n"
        f"امروز: {m.get('projects_today', 0)} | این هفته: {m.get('projects_week', 0)} | "
        f"این ماه: {m.get('projects_month', 0)}\n"
        f"کل: {m.get('projects_total', 0)}\n"
        f"به تفکیک شهر:\n{city_lines}\n"
        f"به تفکیک دسته:\n{cat_lines}\n\n"

        "📈 نرخ تکمیل فلو مشتری\n"
        f"شروع‌کرده: {m.get('customer_funnel_started', 0)} | "
        f"تکمیل‌کرده: {m.get('customer_funnel_completed', 0)}\n"
        f"نرخ تکمیل: {m.get('customer_funnel_rate', 0)}٪\n\n"

        "👷 پیمانکارها\n"
        f"کل: {m.get('contractors_total', 0)} | فعال: {m.get('contractors_active', 0)}\n"
        f"اعتبار صفرشده: {m.get('contractors_zero_credit', 0)}\n"
        f"میانگین اعتبار: {m.get('contractors_avg_credit', 0)}\n"
        f"به تفکیک دسته:\n{contractor_cat_lines}\n\n"

        "🔗 اتصال دو طرف\n"
        f"کل اعلام آمادگی: {m.get('applications_total', 0)}\n"
        f"میانگین اعلام آمادگی به ازای هر پروژه: {m.get('avg_applications_per_project', 0)}\n"
        f"میانگین اعلام آمادگی به تفکیک دسته (برای تنظیم سقف):\n{avg_by_cat_lines}\n"
        f"⚠️ پروژه‌های بدون پاسخ: {m.get('projects_no_response', 0)}\n"
        f"نسبت پروژه به پیمانکار فعال: {m.get('project_to_contractor_ratio', 0)}\n\n"

        "🔒 دلایل بستن پروژه بدون اعلام آمادگی\n"
        f"{closed_reason_lines}\n\n"

        "🌟 وی‌آی‌پی\n"
        f"پروژه‌های با تاخیر وی‌آی‌پی ارسال‌شده: {m.get('vip_delayed_projects', 0)}\n\n"

        "💳 پرداخت‌ها\n"
        f"در انتظار تایید: {m.get('payments_pending', 0)}\n"
        f"تایید شده: {m.get('payments_approved', 0)}\n"
        f"رها شده: {m.get('payments_abandoned', 0)} ({m.get('payments_abandon_rate', 0)}٪)\n"
    )

    await update.message.reply_text(
        text,
        reply_markup=kb_admin_main(),
    )


SETTINGS_PARAMS = {
    "max_apply_credit": ("اعتبار اولیه پیمانکار", "initial_credit"),
    "rating_reminder_days": ("یادآوری امتیازدهی (روز)", "rating_reminder_days"),
    "max_monthly_projects": ("سقف پروژه ماهانه مشتری", "max_monthly_projects"),
    "close_early_threshold": ("حد نصاب اعلام آمادگی برای بستن عادی", "close_early_threshold"),
    "application_cap_count": ("ظرفیت اعلام آمادگی هر پروژه", "application_cap_count"),
}


def kb_admin_settings_inline():
    rows = [
        [
            InlineKeyboardButton(
                label,
                callback_data=f"setpar:{key}",
            )
        ]
        for key, (label, _) in SETTINGS_PARAMS.items()
    ]

    return InlineKeyboardMarkup(rows)


async def admin_settings_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return ConversationHandler.END

    current_values = {}

    for key, (label, db_key) in SETTINGS_PARAMS.items():
        val = await db_call(db.get_setting_int, db_key, 0)
        current_values[label] = val

    text = "⚙️ <b>تنظیمات فعلی سیستم:</b>\n\n"
    text += "\n".join(
        [
            f"• {label}: {val}"
            for label, val in current_values.items()
        ]
    )
    text += "\n\nبرای تغییر هر پارامتر، روی دکمه مربوطه بزنید:"

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=kb_admin_settings_inline(),
    )

    return ADMIN_SETTINGS


async def admin_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id not in ADMIN_IDS:
        return ConversationHandler.END

    param_key = query.data.split("setpar:", 1)[1]

    if param_key not in SETTINGS_PARAMS:
        await query.message.reply_text("پارامتر نامعتبر است.")
        return ConversationHandler.END

    label, db_key = SETTINGS_PARAMS[param_key]

    context.user_data["editing_setting"] = db_key
    context.user_data["editing_setting_label"] = label

    await query.message.reply_text(
        f"مقدار جدید برای «{label}» را وارد کنید:",
        reply_markup=kb_cancel_restart(),
    )

    return ADMIN_SET_VALUE


async def admin_set_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if not text.isdigit():
        await update.message.reply_text("لطفاً فقط عدد وارد کنید.")
        return ADMIN_SET_VALUE

    db_key = context.user_data["editing_setting"]
    label = context.user_data["editing_setting_label"]

    await db_call(db.set_setting, db_key, text)

    await update.message.reply_text(
        f"✅ «{label}» به مقدار {text} تغییر یافت.",
        reply_markup=kb_admin_main(),
    )

    context.user_data.pop("editing_setting", None)
    context.user_data.pop("editing_setting_label", None)

    return ConversationHandler.END


async def admin_moderation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    pending = await db_call(db.get_pending_ratings)

    if not pending:
        await update.message.reply_text(
            "✅ نظر تاییدنشده‌ای وجود ندارد.",
            reply_markup=kb_admin_main(),
        )
        return

    for r in pending:
        text = (
            f"🏷 پروژه: {r.get('project_code')}\n"
            f"⭐ امتیاز: {r.get('score')}/۵\n"
            f"📝 نظر: {r.get('comment') or '—'}"
        )

        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ تایید نمایش",
                        callback_data=f"approve_rating:{r['id']}",
                    )
                ]
            ]
        )

        await update.message.reply_text(text, reply_markup=kb)


async def admin_moderation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id not in ADMIN_IDS:
        return

    try:
        rating_id = int(query.data.split("approve_rating:", 1)[1])
    except Exception:
        await query.edit_message_text("شناسه نظر نامعتبر است.")
        return

    await db_call(db.approve_rating, rating_id)

    await query.edit_message_text(
        query.message.text + "\n\n✅ تایید و منتشر شد."
    )


async def admin_test_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    await update.message.reply_text(
        "🧪 در حال اجرای تست کامل...",
        reply_markup=ReplyKeyboardRemove(),
    )

    results = []

    try:
        db_ok = await db_call(db.db_health_check)
    except Exception:
        db_ok = False

    results.append(("اتصال دیتابیس", db_ok))

    try:
        val = await db_call(db.get_setting_int, "application_cap_count", 10)
        results.append(("خواندن تنظیمات", bool(val)))
    except Exception:
        results.append(("خواندن تنظیمات", False))

    channel_ok = True

    try:
        if CONTRACTOR_CHANNEL_ID:
            # فقط بررسی دسترسی ربات به کانال - بدون ارسال هیچ پیام قابل‌مشاهده‌ای
            await context.bot.get_chat(chat_id=CONTRACTOR_CHANNEL_ID)
        else:
            channel_ok = False

    except TelegramError as e:
        logger.error(f"بررسی دسترسی به کانال پیمانکاران ناموفق: {e}")
        channel_ok = False

    results.append(("دسترسی به کانال پیمانکاران", channel_ok))

    ai_ok = True

    try:
        test_text = await clean_customer_text("این یک متن تست است")
        ai_ok = bool(test_text)
    except Exception:
        ai_ok = False

    results.append(("پاکسازی متن", ai_ok))
    results.append(("Job Queue فعال", context.job_queue is not None))

    lines = []
    all_ok = True

    for name, status in results:
        icon = "✅" if status else "❌"

        if not status:
            all_ok = False

        lines.append(f"{icon} {name}: {'موفق' if status else 'ناموفق'}")

    summary = "🧪 <b>نتیجه تست کامل:</b>\n\n" + "\n".join(lines)
    summary += "\n\n" + (
        "✅ همه موارد سالم هستند."
        if all_ok
        else "⚠️ برخی موارد نیاز به بررسی دارند."
    )

    await update.message.reply_text(
        summary,
        parse_mode=ParseMode.HTML,
        reply_markup=kb_admin_main(),
    )


# ============================================================
# Job Queue
# ============================================================

async def job_process_scheduled_tasks(context: ContextTypes.DEFAULT_TYPE):
    """
    VIP فعلاً غیرفعال است.
    این تابع فقط برای سازگاری با scheduled_tasks قبلی نگه داشته شده.
    """
    if not hasattr(db, "get_pending_scheduled_tasks"):
        return

    tasks = await db_call(db.get_pending_scheduled_tasks)

    for task in tasks:
        try:
            task_type = task.get("type") or task.get("task_type")

            if task_type == "publish_public":
                payload = task.get("payload") or {}

                if isinstance(payload, str):
                    import json
                    payload = json.loads(payload)

                project_code = payload.get("project_code")
                project_id = payload.get("project_id")
                photo_file_id = payload.get("photo_file_id")
                caption = payload.get("caption")

                keyboard = None

                if project_code:
                    keyboard = InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "👷 اعلام آمادگی",
                                    callback_data=f"apply_project:{project_code}",
                                )
                            ]
                        ]
                    )

                if CONTRACTOR_CHANNEL_ID:
                    if photo_file_id:
                        msg = await context.bot.send_photo(
                            chat_id=CONTRACTOR_CHANNEL_ID,
                            photo=photo_file_id,
                            caption=caption or "🆕 پروژه جدید",
                            reply_markup=keyboard,
                        )
                    else:
                        msg = await context.bot.send_message(
                            chat_id=CONTRACTOR_CHANNEL_ID,
                            text=caption or "🆕 پروژه جدید",
                            reply_markup=keyboard,
                        )

                    if project_id and hasattr(db, "set_project_channel_message"):
                        await db_call(
                            db.set_project_channel_message,
                            project_id,
                            msg.message_id,
                            False,
                        )

                    await safe_mark_project_posted_public(project_id)

            if hasattr(db, "mark_scheduled_task_done"):
                await db_call(db.mark_scheduled_task_done, task["id"])

        except Exception as e:
            logger.error(f"خطا در پردازش scheduled task {task.get('id')}: {e}")

            if hasattr(db, "mark_task_failed"):
                try:
                    await db_call(db.mark_task_failed, task["id"])
                except Exception:
                    pass


async def job_rating_reminder(context: ContextTypes.DEFAULT_TYPE):
    if not hasattr(db, "get_projects_needing_rating_reminder"):
        return

    days = await db_call(db.get_setting_int, "rating_reminder_days", 1)
    projects = await db_call(db.get_projects_needing_rating_reminder, days)

    for project in projects:
        customer_telegram_id = project.get("customer_telegram_id")

        if not customer_telegram_id:
            continue

        try:
            await context.bot.send_message(
                chat_id=customer_telegram_id,
                text=(
                    f"👋 پروژه {project.get('code') or project.get('project_code')} شما به پایان رسیده است.\n"
                    "لطفاً در صورت تمایل به پیمانکار امتیاز دهید."
                ),
            )

            if hasattr(db, "mark_rating_reminder_sent"):
                await db_call(db.mark_rating_reminder_sent, project["id"])

        except TelegramError as e:
            logger.error(f"خطا در ارسال یادآوری امتیازدهی: {e}")


# ============================================================
# کانال پیمانکاران: مدیریت درخواست عضویت (Join Request)
# ============================================================
# لینک کانال باید در حالت «تایید عضویت توسط ادمین» (Approve New Members)
# باشد و ربات باید ادمین کانال با دسترسی Invite Users باشد.
#
# منطق:
# - اگر درخواست‌دهنده در جدول contractors ثبت‌نام کرده باشد → تایید خودکار.
# - اگر ثبت‌نام نکرده باشد → درخواست «رد نمی‌شود» بلکه pending می‌ماند و
#   پیام راهنما برایش ارسال می‌شود؛ به محض تکمیل ثبت‌نام در ربات،
#   درخواستش به‌صورت خودکار تایید می‌شود.
#   (دلیل: طبق رفتار تلگرام، اگر درخواست decline شود کاربر تا مدتی
#   نمی‌تواند دوباره از همان لینک درخواست بدهد.)

async def handle_chat_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    join_request = update.chat_join_request
    if not join_request:
        return

    # فقط کانال پیمانکاران را مدیریت کن
    if CONTRACTOR_CHANNEL_ID and join_request.chat.id != CONTRACTOR_CHANNEL_ID:
        return

    user = join_request.from_user
    # طبق مستندات، برای پیام دادن به درخواست‌دهنده باید از user_chat_id استفاده شود
    user_chat_id = join_request.user_chat_id

    contractor = await db_call(db.get_contractor_by_telegram_id, user.id)

    if contractor:
        try:
            await join_request.approve()
            logger.info(f"✅ درخواست عضویت پیمانکار {user.id} در کانال تایید شد.")
        except TelegramError as e:
            logger.error(f"خطا در تایید درخواست عضویت {user.id}: {e}")
            return

        try:
            await context.bot.send_message(
                chat_id=user_chat_id,
                text=(
                    "✅ درخواست عضویت شما در کانال پیمانکاران تایید شد.\n\n"
                    "از این پس پروژه‌های جدید را در کانال می‌بینید و می‌توانید "
                    "با دکمه «اعلام آمادگی» اقدام کنید."
                ),
            )
        except TelegramError:
            pass

        return

    # ثبت‌نام نکرده → درخواست pending می‌ماند + پیام راهنما
    logger.info(f"⛔ درخواست عضویت کاربر ثبت‌نام‌نشده {user.id} — در انتظار ثبت‌نام.")

    bot_username = context.bot.username
    try:
        await context.bot.send_message(
            chat_id=user_chat_id,
            text=(
                "👋 سلام!\n\n"
                "این کانال مخصوص پیمانکارانی است که در ربات «شمال‌پروژه» "
                "ثبت‌نام کرده‌اند.\n\n"
                "⛔ شما هنوز در ربات ثبت‌نام نکرده‌اید.\n\n"
                "لطفاً ابتدا وارد ربات شوید، /start را بزنید و به‌عنوان "
                "پیمانکار ثبت‌نام کنید. بلافاصله بعد از تکمیل ثبت‌نام، "
                "عضویت شما در کانال به‌صورت خودکار تایید می‌شود ✅"
            ),
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🤖 ورود به ربات و ثبت‌نام",
                            url=f"https://t.me/{bot_username}",
                        )
                    ]
                ]
            ),
        )
    except TelegramError as e:
        logger.warning(f"ارسال پیام راهنما به {user.id} ممکن نشد: {e}")


async def approve_pending_join_request(context: ContextTypes.DEFAULT_TYPE, telegram_id: int):
    """
    بعد از تکمیل ثبت‌نام پیمانکار صدا زده می‌شود.
    اگر کاربر قبلاً روی لینک کانال کلیک کرده و درخواستش pending مانده باشد،
    همین‌جا خودکار تایید می‌شود. اگر درخواستی وجود نداشته باشد، تلگرام خطا
    می‌دهد که بی‌صدا نادیده گرفته می‌شود.
    """
    if not CONTRACTOR_CHANNEL_ID:
        return

    try:
        await context.bot.approve_chat_join_request(
            chat_id=CONTRACTOR_CHANNEL_ID,
            user_id=telegram_id,
        )
        logger.info(f"✅ درخواست عضویت pending کاربر {telegram_id} پس از ثبت‌نام تایید شد.")

        try:
            await context.bot.send_message(
                chat_id=telegram_id,
                text="✅ عضویت شما در کانال پیمانکاران تایید شد!",
            )
        except TelegramError:
            pass

    except TelegramError:
        # درخواست pending وجود نداشت — طبیعی است
        pass


# ============================================================
# Error Handler
# ============================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(
        f"خطای پیش‌بینی‌نشده: {context.error}",
        exc_info=context.error,
    )

    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ خطایی رخ داد. لطفاً دوباره تلاش کنید یا /start را بزنید."
            )
        except Exception:
            pass


# ============================================================
# main
# ============================================================

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    fallbacks_cancel = [
        MessageHandler(filters.Regex(f"^{re.escape(BTN_CANCEL)}$"), cancel_generic),
        MessageHandler(filters.Regex(f"^{re.escape(BTN_RESTART)}$"), start),
        CommandHandler("cancel", cancel_generic),
    ]

    new_project_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex(f"^{re.escape(BTN_NEW_PROJECT)}$"),
                new_project_entry,
            )
        ],
        states={
            C_CITY: [
                MessageHandler(TEXT_INPUT_FILTER, np_city)
            ],
            C_CATEGORY: [
                MessageHandler(TEXT_INPUT_FILTER, np_category)
            ],
            C_DESCRIPTION: [
                MessageHandler(TEXT_INPUT_FILTER, np_description)
            ],
            C_PHOTO: [
                MessageHandler(
                    PHOTO_INPUT_FILTER,
                    np_photo,
                )
            ],
            C_BUDGET: [
                MessageHandler(TEXT_INPUT_FILTER, np_budget)
            ],
            C_URGENCY: [
                MessageHandler(TEXT_INPUT_FILTER, np_urgency)
            ],
            C_CONFIRM: [
                MessageHandler(TEXT_INPUT_FILTER, np_confirm)
            ],
        },
        fallbacks=fallbacks_cancel,
    )

    track_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex(f"^{re.escape(BTN_TRACK_PROJECT)}$"),
                track_entry,
            )
        ],
        states={
            TRACK_CODE: [
                MessageHandler(TEXT_INPUT_FILTER, track_code)
            ]
        },
        fallbacks=fallbacks_cancel,
    )

    close_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex(f"^{re.escape(BTN_CLOSE_PROJECT)}$"),
                close_entry,
            )
        ],
        states={
            CLOSE_CODE: [
                MessageHandler(TEXT_INPUT_FILTER, close_code)
            ],
            CLOSE_REASON: [
                MessageHandler(TEXT_INPUT_FILTER, close_reason)
            ],
            CLOSE_PICK_CONTRACTOR: [
                MessageHandler(TEXT_INPUT_FILTER, close_pick_contractor)
            ],
            CLOSE_RATING_SCORE: [
                MessageHandler(TEXT_INPUT_FILTER, close_rating_score)
            ],
            CLOSE_RATING_COMMENT: [
                MessageHandler(TEXT_INPUT_FILTER, close_rating_comment)
            ],
        },
        fallbacks=fallbacks_cancel,
    )

    register_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex("^👷 من پیمانکار هستم$"),
                cmd_register_contractor_entry,
            ),
            CommandHandler("register", cmd_register_contractor_entry),
        ],
        states={
            R_FULLNAME: [
                MessageHandler(TEXT_INPUT_FILTER, r_fullname)
            ],
            R_PHONE: [
                MessageHandler(
                    CONTACT_INPUT_FILTER,
                    r_phone,
                )
            ],
            R_CITY: [
                MessageHandler(TEXT_INPUT_FILTER, r_city)
            ],
            R_CATEGORIES: [
                MessageHandler(TEXT_INPUT_FILTER, r_categories)
            ],
            R_EXTRA_CHOICE: [
                MessageHandler(TEXT_INPUT_FILTER, r_extra_choice)
            ],
            R_EXTRA_PORTFOLIO: [
                MessageHandler(
                    (filters.PHOTO | filters.Document.ALL | filters.TEXT)
                    & ~filters.COMMAND
                    & _CANCEL_RESTART_EXCLUDE,
                    r_extra_portfolio,
                )
            ],
            R_EXTRA_RESUME: [
                MessageHandler(TEXT_INPUT_FILTER, r_extra_resume)
            ],
            R_EXTRA_SOCIAL: [
                MessageHandler(TEXT_INPUT_FILTER, r_extra_social)
            ],
            R_EXTRA_BIO: [
                MessageHandler(TEXT_INPUT_FILTER, r_extra_bio)
            ],
        },
        fallbacks=fallbacks_cancel,
    )

    edit_profile_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex(f"^{re.escape(BTN_EDIT_PROFILE)}$"),
                edit_profile_entry,
            )
        ],
        states={
            P_MENU: [
                MessageHandler(TEXT_INPUT_FILTER, p_menu)
            ],
            P_FULLNAME: [
                MessageHandler(TEXT_INPUT_FILTER, p_fullname)
            ],
            P_CATEGORIES: [
                MessageHandler(TEXT_INPUT_FILTER, p_categories)
            ],
            P_PHONE2: [
                MessageHandler(
                    CONTACT_INPUT_FILTER,
                    p_phone2,
                )
            ],
            P_PORTFOLIO: [
                MessageHandler(
                    (filters.PHOTO | filters.Document.ALL | filters.TEXT)
                    & ~filters.COMMAND
                    & _CANCEL_RESTART_EXCLUDE,
                    p_portfolio,
                )
            ],
            P_RESUME: [
                MessageHandler(TEXT_INPUT_FILTER, p_resume)
            ],
            P_SOCIAL: [
                MessageHandler(TEXT_INPUT_FILTER, p_social)
            ],
        },
        fallbacks=fallbacks_cancel,
    )

    admin_settings_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex(f"^{re.escape(BTN_ADMIN_SETTINGS)}$"),
                admin_settings_entry,
            )
        ],
        states={
            ADMIN_SETTINGS: [
                CallbackQueryHandler(
                    admin_settings_callback,
                    pattern="^setpar:",
                )
            ],
            ADMIN_SET_VALUE: [
                MessageHandler(
                    TEXT_INPUT_FILTER,
                    admin_set_value,
                )
            ],
        },
        fallbacks=fallbacks_cancel,
    )

    # ========================================================
    # ثبت هندلرها
    # ========================================================

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel_generic))

    # هندلر درخواست عضویت در کانال پیمانکاران (Join Request)
    app.add_handler(ChatJoinRequestHandler(handle_chat_join_request))

    # هندلر اعلام آمادگی از دکمه داخل کانال
    app.add_handler(
        CallbackQueryHandler(
            handle_apply_project_callback,
            pattern=r"^apply_project:",
        )
    )

    app.add_handler(new_project_conv)
    app.add_handler(track_conv)
    app.add_handler(close_conv)
    app.add_handler(register_conv)
    app.add_handler(edit_profile_conv)
    app.add_handler(admin_settings_conv)

    app.add_handler(
        MessageHandler(
            filters.Regex(f"^{re.escape(BTN_MY_PROFILE)}$"),
            show_profile,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.Regex(f"^{re.escape(BTN_BUY_CREDIT)}$"),
            buy_credit,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.Regex(f"^{re.escape(BTN_ADMIN_DASHBOARD)}$"),
            admin_dashboard,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.Regex(f"^{re.escape(BTN_ADMIN_MODERATION)}$"),
            admin_moderation,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.Regex(f"^{re.escape(BTN_ADMIN_TEST)}$"),
            admin_test_flow,
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            admin_moderation_callback,
            pattern="^approve_rating:",
        )
    )

    # انتخاب نقش اولیه فقط از روی دکمه‌های نقش
    app.add_handler(
        MessageHandler(
            filters.Regex("^(👤 من مشتری هستم|👷 من پیمانکار هستم)$"),
            role_router,
        )
    )

    app.add_error_handler(error_handler)

    # ========================================================
    # Job Queue
    # ========================================================

    if app.job_queue:
        app.job_queue.run_repeating(
            job_process_scheduled_tasks,
            interval=60,
            first=10,
        )

        app.job_queue.run_repeating(
            job_rating_reminder,
            interval=3600,
            first=30,
        )

        logger.info("✅ Job Queue فعال شد.")

    else:
        logger.warning(
            "⚠️ job_queue فعال نیست! برای فعال‌سازی از این پکیج استفاده کنید:\n"
            "python-telegram-bot[job-queue]"
        )

    logger.info("🚀 ربات در حال اجراست...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
