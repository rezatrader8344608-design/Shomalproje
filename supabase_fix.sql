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


-- ============================================================
-- بخش ۲: اعلام آمادگی اتمیک
-- db.py این تابع را با این امضا صدا می‌زند:
--   apply_to_project_atomic(p_contractor_id, p_project_code, p_is_vip)
-- ولی نسخه‌ی قدیمی دیتابیس امضای متفاوتی داشت و 404 می‌داد.
-- این نسخه: چک تکراری‌نبودن + چک ظرفیت + کسر اعتبار + ثبت
-- declarations و applications — همه در یک تراکنش.
-- ============================================================

-- حذف نسخه‌ی قدیمی با امضای متفاوت تا تداخل پیش نیاید
DROP FUNCTION IF EXISTS apply_to_project_atomic(BIGINT, BIGINT, INT);

CREATE OR REPLACE FUNCTION apply_to_project_atomic(
    p_contractor_id BIGINT,
    p_project_code TEXT,
    p_is_vip BOOLEAN DEFAULT false
)
RETURNS TABLE(
    success BOOLEAN,
    reason TEXT,
    remaining_credit INT,
    applications_count INT,
    cap_just_reached BOOLEAN
) AS $$
DECLARE
    v_project projects%ROWTYPE;
    v_cap INT;
    v_count INT;
    v_credits INT;
    v_cap_reached BOOLEAN := false;
    v_code TEXT;
BEGIN
    v_code := upper(trim(p_project_code));

    SELECT * INTO v_project
    FROM projects
    WHERE code = v_code OR project_code = v_code
    LIMIT 1
    FOR UPDATE;

    IF v_project.id IS NULL THEN
        RETURN QUERY SELECT false, 'project_not_found'::TEXT, NULL::INT, NULL::INT, false;
        RETURN;
    END IF;

    IF v_project.status = 'closed' THEN
        RETURN QUERY SELECT false, 'project_closed'::TEXT, NULL::INT, NULL::INT, false;
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1 FROM declarations d
        WHERE d.project_code = v_code AND d.contractor_id = p_contractor_id
    ) THEN
        RETURN QUERY SELECT false, 'already_applied'::TEXT, NULL::INT, NULL::INT, false;
        RETURN;
    END IF;

    SELECT COALESCE(
        (
            SELECT s.value::INT FROM app_settings s
            WHERE s.key = CASE WHEN p_is_vip
                THEN 'vip_application_cap_count'
                ELSE 'application_cap_count' END
        ),
        10
    ) INTO v_cap;

    SELECT COUNT(*) INTO v_count FROM declarations d WHERE d.project_code = v_code;

    IF v_count >= v_cap THEN
        UPDATE projects
        SET status = 'closed', closed_reason = 'cap_reached', closed_at = now()
        WHERE id = v_project.id;

        RETURN QUERY SELECT false, 'project_capacity_full'::TEXT, NULL::INT, v_count, false;
        RETURN;
    END IF;

    UPDATE contractors
    SET credits = credits - 1
    WHERE id = p_contractor_id AND credits > 0
    RETURNING credits INTO v_credits;

    IF v_credits IS NULL THEN
        RETURN QUERY SELECT false, 'insufficient_credit'::TEXT, 0, v_count, false;
        RETURN;
    END IF;

    INSERT INTO declarations (contractor_id, project_code)
    VALUES (p_contractor_id, v_code);

    INSERT INTO applications (
        project_id, contractor_id, project_code,
        contractor_telegram_id, contractor_name,
        contractor_phone, contractor_resume
    )
    SELECT
        v_project.id, p_contractor_id, v_code,
        c.telegram_id, COALESCE(c.full_name, c.name),
        c.phone, c.resume
    FROM contractors c
    WHERE c.id = p_contractor_id;

    UPDATE contractors
    SET total_applications = COALESCE(total_applications, 0) + 1
    WHERE id = p_contractor_id;

    v_count := v_count + 1;

    IF v_count >= v_cap THEN
        UPDATE projects
        SET status = 'closed', closed_reason = 'cap_reached', closed_at = now()
        WHERE id = v_project.id;
        v_cap_reached := true;
    END IF;

    RETURN QUERY SELECT true, 'ok'::TEXT, v_credits, v_count, v_cap_reached;
END;
$$ LANGUAGE plpgsql;
