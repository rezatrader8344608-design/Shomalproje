-- ============================================================
-- رفع خطاهای 404 در لاگ Railway
-- این دو تابع در db.py صدا زده می‌شوند ولی در دیتابیس وجود نداشتند.
-- (بدون این‌ها هم ربات با fallback کار می‌کند، ولی با این‌ها
-- سریع‌تر و اتمیک می‌شود و هشدارهای لاگ حذف می‌شوند.)
--
-- طرز اجرا: در Supabase → SQL Editor → این فایل را paste و Run کن.
-- ============================================================

-- تعداد پروژه‌های ماه جاری مشتری
CREATE OR REPLACE FUNCTION get_customer_monthly_count(p_customer_id BIGINT)
RETURNS INT AS $$
DECLARE
    v_count INT;
BEGIN
    SELECT COUNT(*) INTO v_count
    FROM projects
    WHERE customer_id = p_customer_id
      AND created_at >= date_trunc('month', now());
    RETURN v_count;
END;
$$ LANGUAGE plpgsql;


-- ساخت اتمیک پروژه (کد پروژه + درج رکورد در یک تراکنش)
CREATE OR REPLACE FUNCTION create_project_atomic(
    p_customer_id BIGINT,
    p_customer_telegram_id BIGINT,
    p_city TEXT,
    p_category TEXT,
    p_description TEXT,
    p_budget TEXT,
    p_urgency TEXT
)
RETURNS SETOF projects AS $$
DECLARE
    v_code TEXT;
BEGIN
    v_code := generate_project_code_atomic();

    RETURN QUERY
    INSERT INTO projects (
        project_code,
        code,
        customer_id,
        customer_telegram_id,
        city,
        category,
        categories,
        description,
        budget,
        urgency,
        status
    )
    VALUES (
        v_code,
        v_code,
        p_customer_id,
        p_customer_telegram_id,
        p_city,
        p_category,
        CASE WHEN p_category IS NULL THEN '{}'::TEXT[] ELSE ARRAY[p_category] END,
        p_description,
        p_budget,
        p_urgency,
        'open'
    )
    RETURNING *;
END;
$$ LANGUAGE plpgsql;


-- همسان‌سازی رکوردهای قدیمی که ستون code آن‌ها خالی مانده
UPDATE projects SET code = project_code WHERE code IS NULL;
