"""
ai_classifier.py
ماژول تمیزسازی و دسته‌بندی متن پروژه برای ربات خدمات ساختمانی شمال

این فایل طوری نوشته شده که:
- اگر API هوش مصنوعی تنظیم نبود، ربات کرش نکند.
- تابع clean_text همیشه وجود داشته باشد.
- تابع classify_project همیشه خروجی امن و قابل استفاده بدهد.
- هیچ وابستگی خارجی اجباری نداشته باشد.
"""

import os
import re
import json
import logging
import urllib.request
import urllib.error
from typing import Any, Dict, Optional


logger = logging.getLogger(__name__)


# ============================================================
# تنظیمات اختیاری هوش مصنوعی
# ============================================================

NVIDIA_API_KEY = (
    os.getenv("NVIDIA_API_KEY")
    or os.getenv("NVIDIA_NIM_API_KEY")
    or os.getenv("NIM_API_KEY")
    or ""
).strip()

NVIDIA_MODEL = (
    os.getenv("NVIDIA_MODEL")
    or os.getenv("NVIDIA_NIM_MODEL")
    or "meta/llama-3.1-70b-instruct"
).strip()

NVIDIA_API_BASE_URL = (
    os.getenv("NVIDIA_API_BASE_URL")
    or os.getenv("NVIDIA_NIM_BASE_URL")
    or "https://integrate.api.nvidia.com/v1/chat/completions"
).strip()

GROQ_API_KEY = (
    os.getenv("GROQ_API_KEY")
    or ""
).strip()

GROQ_MODEL = (
    os.getenv("GROQ_MODEL")
    or "llama-3.3-70b-versatile"
).strip()

GROQ_API_BASE_URL = (
    os.getenv("GROQ_API_BASE_URL")
    or "https://api.groq.com/openai/v1/chat/completions"
).strip()

AI_TIMEOUT_SECONDS = int(os.getenv("AI_TIMEOUT_SECONDS", "20"))


# ============================================================
# داده‌های پایه پروژه
# ============================================================

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

URGENCY_LEVELS = [
    "فوری (۱-۲ روز)",
    "این هفته",
    "این ماه",
    "زمان مشخصی ندارم",
]


# ============================================================
# ابزارهای عمومی
# ============================================================

_PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
_EN_DIGITS = "0123456789"


def _translate_digits_to_en(text: str) -> str:
    if not text:
        return text

    table = str.maketrans(
        _PERSIAN_DIGITS + _ARABIC_DIGITS,
        _EN_DIGITS + _EN_DIGITS,
    )
    return text.translate(table)


def _normalize_persian_chars(text: str) -> str:
    if not text:
        return text

    replacements = {
        "ي": "ی",
        "ى": "ی",
        "ئ": "ی",
        "ك": "ک",
        "ۀ": "ه",
        "ة": "ه",
        "ؤ": "و",
        "إ": "ا",
        "أ": "ا",
        "آ": "آ",
        "ٱ": "ا",
        "‌": " ",
        "\u200c": " ",
        "\u200f": " ",
        "\u200e": " ",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    return text


def clean_text(text: str) -> str:
    """
    تمیزسازی امن متن.

    این تابع توسط bot.py ایمپورت می‌شود:
        from ai_classifier import clean_text

    پس حتماً باید وجود داشته باشد و هیچ‌وقت کرش نکند.
    """
    if text is None:
        return ""

    try:
        text = str(text)
        text = _normalize_persian_chars(text)

        # حذف کاراکترهای کنترلی نامرئی
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", " ", text)

        # یکسان‌سازی فاصله‌ها
        text = re.sub(r"[ \t\r\n]+", " ", text)

        # حذف فاصله‌های اضافه اطراف نشانه‌ها
        text = re.sub(r"\s+([،,.!?؟:؛])", r"\1", text)
        text = re.sub(r"([،,.!?؟:؛])\s*", r"\1 ", text)

        # دوباره مرتب‌سازی فاصله‌ها
        text = re.sub(r"\s+", " ", text)

        return text.strip()

    except Exception as e:
        logger.error(f"clean_text error: {e}")
        try:
            return str(text).strip()
        except Exception:
            return ""


def _safe_lower(text: str) -> str:
    return clean_text(text).lower()


def _contains_any(text: str, keywords) -> bool:
    text = _safe_lower(text)
    return any(k.lower() in text for k in keywords)


def _extract_json_from_text(text: str) -> Optional[Dict[str, Any]]:
    """
    تلاش برای استخراج JSON از پاسخ مدل.
    """
    if not text:
        return None

    text = text.strip()

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    # اگر مدل اطراف JSON توضیح داده باشد
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None

    try:
        data = json.loads(match.group(0))
        if isinstance(data, dict):
            return data
    except Exception:
        return None

    return None


# ============================================================
# دسته‌بندی داخلی بدون AI
# ============================================================

def detect_city(text: str) -> Optional[str]:
    text = _safe_lower(text)

    city_keywords = {
        "تنکابن": ["تنکابن", "شهسوار", "خرم آباد تنکابن", "کریم آباد"],
        "شیرود": ["شیرود", "شیرود تنکابن"],
        "رامسر": ["رامسر", "کتالم", "سادات شهر"],
    }

    for city, keywords in city_keywords.items():
        if any(k.lower() in text for k in keywords):
            return city

    return None


def detect_category(text: str) -> str:
    text = _safe_lower(text)

    category_keywords = {
        "بازسازی و ساخت": [
            "بازسازی",
            "ساخت",
            "ساختمان",
            "دیوار",
            "سقف",
            "کف",
            "بنایی",
            "سیمان",
            "گچ",
            "کاشی",
            "سرامیک",
            "نما",
            "ویلا",
            "خانه",
            "آپارتمان",
            "تعمیرات کلی",
        ],
        "کابینت و MDF": [
            "کابینت",
            "ام دی اف",
            "mdf",
            "هایگلاس",
            "کمد",
            "کمد دیواری",
            "صفحه کابینت",
            "جزیره",
            "کورین",
            "کوارتز",
        ],
        "نقاشی": [
            "نقاشی",
            "رنگ",
            "رنگ کاری",
            "رنگکاری",
            "بتونه",
            "کاغذ دیواری",
            "مولتی کالر",
            "کنیتکس",
        ],
        "برق کاری": [
            "برق",
            "برقکاری",
            "برق کاری",
            "سیم کشی",
            "کلید",
            "پریز",
            "فیوز",
            "تابلو برق",
            "لوستر",
            "چراغ",
            "روشنایی",
            "آیفون",
            "دوربین",
            "دوربین مداربسته",
        ],
        "لوله کشی و تاسیسات": [
            "لوله",
            "لوله کشی",
            "تاسیسات",
            "آب",
            "فاضلاب",
            "نشتی",
            "شیرآلات",
            "پمپ",
            "مخزن",
            "رادیاتور",
            "شوفاژ",
            "توالت",
            "سرویس",
            "حمام",
        ],
        "کولر و پکیج": [
            "کولر",
            "اسپلیت",
            "پکیج",
            "بخاری",
            "گرمایش",
            "سرمایش",
            "سرویس کولر",
            "نصب کولر",
            "تعمیر کولر",
            "تعمیر پکیج",
            "داکت اسپلیت",
        ],
        "درب و پنجره": [
            "درب",
            "در",
            "پنجره",
            "پنجره دوجداره",
            "upvc",
            "یو پی وی سی",
            "آلومینیوم",
            "توری",
            "شیشه",
            "کرکره",
            "ریل",
        ],
        "دکوراسیون و طراحی داخلی": [
            "دکور",
            "دکوراسیون",
            "طراحی داخلی",
            "کناف",
            "نور مخفی",
            "پارتیشن",
            "دیوارپوش",
            "سقف کاذب",
            "لمینت",
            "پارکت",
            "طراحی",
        ],
    }

    scores = {}

    for category, keywords in category_keywords.items():
        score = 0
        for keyword in keywords:
            if keyword.lower() in text:
                score += 1
        scores[category] = score

    best_category = max(scores, key=scores.get)

    if scores.get(best_category, 0) <= 0:
        return "بازسازی و ساخت"

    return best_category


def detect_urgency(text: str) -> str:
    text_norm = _safe_lower(text)
    text_digits = _translate_digits_to_en(text_norm)

    urgent_keywords = [
        "فوری",
        "اورژانسی",
        "همین امروز",
        "امروز",
        "فردا",
        "سریع",
        "عجله",
        "ضروری",
        "۱ روز",
        "1 روز",
        "۲ روز",
        "2 روز",
    ]

    week_keywords = [
        "این هفته",
        "هفته",
        "چند روز",
        "تا آخر هفته",
        "سه چهار روز",
        "3 روز",
        "4 روز",
        "5 روز",
        "6 روز",
        "7 روز",
    ]

    month_keywords = [
        "این ماه",
        "ماه",
        "چند هفته",
        "دو هفته",
        "2 هفته",
        "سه هفته",
        "3 هفته",
    ]

    no_time_keywords = [
        "زمان مشخصی ندارم",
        "عجله ندارم",
        "مهم نیست",
        "هر وقت",
        "فرقی نداره",
        "فعلا مشخص نیست",
    ]

    if any(k in text_norm for k in urgent_keywords) or any(k in text_digits for k in urgent_keywords):
        return "فوری (۱-۲ روز)"

    if any(k in text_norm for k in week_keywords) or any(k in text_digits for k in week_keywords):
        return "این هفته"

    if any(k in text_norm for k in month_keywords) or any(k in text_digits for k in month_keywords):
        return "این ماه"

    if any(k in text_norm for k in no_time_keywords):
        return "زمان مشخصی ندارم"

    return "زمان مشخصی ندارم"


def estimate_budget_range(text: str) -> Optional[str]:
    """
    استخراج ساده بودجه از متن.
    خروجی صرفاً کمکی است.
    """
    if not text:
        return None

    text_clean = clean_text(text)
    text_en = _translate_digits_to_en(text_clean)

    money_patterns = [
        r"(\d+(?:[.,]\d+)?)\s*(?:میلیون|ملیون)",
        r"(\d+(?:[.,]\d+)?)\s*(?:تومن|تومان)",
    ]

    found = []

    for pattern in money_patterns:
        for m in re.finditer(pattern, text_en, flags=re.IGNORECASE):
            found.append(m.group(0))

    if found:
        return "، ".join(found[:3])

    return None


def detect_is_relevant(text: str) -> bool:
    """
    تشخیص ساده اینکه متن به خدمات ساختمانی مربوط هست یا نه.
    """
    text = _safe_lower(text)

    relevant_keywords = [
        "ساختمان",
        "بازسازی",
        "کابینت",
        "برق",
        "لوله",
        "نقاشی",
        "رنگ",
        "پکیج",
        "کولر",
        "درب",
        "پنجره",
        "دکور",
        "کناف",
        "کاشی",
        "سرامیک",
        "ویلا",
        "خانه",
        "آپارتمان",
        "تاسیسات",
        "تعمیر",
        "نصب",
        "ساخت",
        "سقف",
        "دیوار",
        "کف",
    ]

    return any(k in text for k in relevant_keywords)


def fallback_classify_project(text: str) -> Dict[str, Any]:
    """
    دسته‌بندی داخلی بدون نیاز به API.
    """
    cleaned = clean_text(text)
    city = detect_city(cleaned)
    category = detect_category(cleaned)
    urgency = detect_urgency(cleaned)
    budget_hint = estimate_budget_range(cleaned)
    is_relevant = detect_is_relevant(cleaned)

    confidence = 0.65
    if city:
        confidence += 0.1
    if is_relevant:
        confidence += 0.15
    if budget_hint:
        confidence += 0.05

    confidence = min(round(confidence, 2), 0.95)

    return {
        "success": True,
        "source": "fallback",
        "cleaned_text": cleaned,
        "city": city,
        "category": category,
        "urgency": urgency,
        "budget_hint": budget_hint,
        "is_relevant": is_relevant,
        "confidence": confidence,
        "reason": "classified_by_internal_rules",
    }


# ============================================================
# ارتباط اختیاری با سرویس‌های هوش مصنوعی (Groq / NVIDIA)
# ============================================================

def _call_openai_compatible_chat(base_url: str, api_key: str, model: str, prompt: str) -> Optional[str]:
    """
    ارتباط عمومی با هر endpoint سازگار با OpenAI (Groq، NVIDIA NIM و مشابه).
    اگر کلید API تنظیم نشده باشد یا خطا بدهد، None برمی‌گرداند.
    """
    if not api_key:
        return None

    try:
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a Persian text cleaner and classifier for construction service requests. "
                        "Always respond with valid JSON only. No markdown. No explanation."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            "temperature": 0.1,
            "max_tokens": 700,
        }

        data = json.dumps(payload).encode("utf-8")

        request = urllib.request.Request(
            base_url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )

        with urllib.request.urlopen(request, timeout=AI_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8", errors="replace")
            result = json.loads(raw)

        choices = result.get("choices") or []
        if not choices:
            return None

        message = choices[0].get("message") or {}
        content = message.get("content")

        if not content:
            return None

        return content.strip()

    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        logger.warning(f"AI API HTTP error ({base_url}): {e.code} - {body}")
        return None

    except Exception as e:
        logger.warning(f"AI API unavailable ({base_url}): {e}")
        return None


def _call_groq_chat(prompt: str) -> Optional[str]:
    return _call_openai_compatible_chat(GROQ_API_BASE_URL, GROQ_API_KEY, GROQ_MODEL, prompt)


def _call_nvidia_chat(prompt: str) -> Optional[str]:
    return _call_openai_compatible_chat(NVIDIA_API_BASE_URL, NVIDIA_API_KEY, NVIDIA_MODEL, prompt)


def ai_classify_project(text: str) -> Optional[Dict[str, Any]]:
    """
    دسته‌بندی با AI.
    ابتدا Groq (رایگان) امتحان می‌شود؛ اگر جواب نداد یا خطا داد، NVIDIA امتحان می‌شود.
    اگر هیچ‌کدام جواب ندهند، None برمی‌گرداند (و caller باید به fallback داخلی برود).
    """
    cleaned = clean_text(text)

    if not cleaned:
        return None

    prompt = f"""
متن زیر مربوط به درخواست یک مشتری برای خدمات ساختمانی در شمال ایران است.

شهرهای مجاز:
{json.dumps(CITIES, ensure_ascii=False)}

دسته‌بندی‌های مجاز:
{json.dumps(CATEGORIES, ensure_ascii=False)}

سطح فوریت‌های مجاز:
{json.dumps(URGENCY_LEVELS, ensure_ascii=False)}

وظایف:
1. متن را تمیز و روان کن.
2. اگر شهر قابل تشخیص است، یکی از شهرهای مجاز را انتخاب کن؛ اگر قابل تشخیص نیست null بده.
3. دسته‌بندی مناسب را فقط از لیست مجاز انتخاب کن.
4. فوریت را فقط از لیست مجاز انتخاب کن.
5. اگر بودجه‌ای در متن هست، در budget_hint بنویس.
6. تشخیص بده آیا متن به خدمات ساختمانی مربوط است یا نه.
7. فقط JSON معتبر بده.

فرمت دقیق خروجی:
{{
  "cleaned_text": "...",
  "city": null,
  "category": "بازسازی و ساخت",
  "urgency": "زمان مشخصی ندارم",
  "budget_hint": null,
  "is_relevant": true,
  "confidence": 0.8,
  "reason": "..."
}}

متن مشتری:
{cleaned}
""".strip()

    content = _call_groq_chat(prompt)
    source = "groq"

    if not content:
        content = _call_nvidia_chat(prompt)
        source = "nvidia"

    if not content:
        return None

    data = _extract_json_from_text(content)
    if not data:
        return None

    # اعتبارسنجی خروجی AI
    ai_cleaned_text = clean_text(data.get("cleaned_text") or cleaned)

    city = data.get("city")
    if city not in CITIES:
        city = detect_city(ai_cleaned_text)

    category = data.get("category")
    if category not in CATEGORIES:
        category = detect_category(ai_cleaned_text)

    urgency = data.get("urgency")
    if urgency not in URGENCY_LEVELS:
        urgency = detect_urgency(ai_cleaned_text)

    budget_hint = data.get("budget_hint") or estimate_budget_range(ai_cleaned_text)

    is_relevant = data.get("is_relevant")
    if not isinstance(is_relevant, bool):
        is_relevant = detect_is_relevant(ai_cleaned_text)

    try:
        confidence = float(data.get("confidence", 0.75))
    except Exception:
        confidence = 0.75

    confidence = max(0.0, min(confidence, 1.0))

    return {
        "success": True,
        "source": source,
        "cleaned_text": ai_cleaned_text,
        "city": city,
        "category": category,
        "urgency": urgency,
        "budget_hint": budget_hint,
        "is_relevant": is_relevant,
        "confidence": round(confidence, 2),
        "reason": data.get("reason") or "classified_by_ai",
    }


# ============================================================
# تابع اصلی قابل استفاده توسط bot.py یا db.py
# ============================================================

def classify_project(text: str) -> Dict[str, Any]:
    """
    دسته‌بندی پروژه.

    خروجی همیشه dict است و هیچ‌وقت exception بیرون نمی‌دهد.
    """
    try:
        cleaned = clean_text(text)

        if not cleaned:
            return {
                "success": False,
                "source": "none",
                "cleaned_text": "",
                "city": None,
                "category": "بازسازی و ساخت",
                "urgency": "زمان مشخصی ندارم",
                "budget_hint": None,
                "is_relevant": False,
                "confidence": 0.0,
                "reason": "empty_text",
            }

        ai_result = ai_classify_project(cleaned)
        if ai_result:
            return ai_result

        return fallback_classify_project(cleaned)

    except Exception as e:
        logger.error(f"classify_project error: {e}")

        cleaned = clean_text(text)

        return {
            "success": False,
            "source": "error_fallback",
            "cleaned_text": cleaned,
            "city": detect_city(cleaned),
            "category": detect_category(cleaned),
            "urgency": detect_urgency(cleaned),
            "budget_hint": estimate_budget_range(cleaned),
            "is_relevant": detect_is_relevant(cleaned),
            "confidence": 0.4,
            "reason": "exception_fallback",
        }


def classify_text(text: str) -> Dict[str, Any]:
    """
    Alias برای سازگاری با اسم‌های احتمالی دیگر.
    """
    return classify_project(text)


def clean_and_classify(text: str) -> Dict[str, Any]:
    """
    Alias دیگر برای سازگاری.
    """
    return classify_project(text)


# ============================================================
# تست سریع محلی
# ============================================================

if __name__ == "__main__":
    sample = "سلام برای بازسازی کابینت و رنگ کاری خونه در تنکابن فوری نیاز به استادکار دارم بودجه حدود ۲۰ میلیون"
    print(json.dumps(classify_project(sample), ensure_ascii=False, indent=2))
