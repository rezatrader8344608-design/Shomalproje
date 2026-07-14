CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now()
);

INSERT INTO app_settings (key, value) VALUES
    ('application_cap_count', '10'),
    ('vip_application_cap_count', '50'),
    ('vip_delay_enabled', 'true'),
    ('vip_delay_minutes', '10'),
    ('rating_delay_days', '1'),
    ('customer_monthly_project_cap', '10'),
    ('customer_flag_threshold', '10'),
    ('contractor_initial_credits', '10'),
    ('price_monthly_subscription', '2000000'),
    ('price_3_credits', '750000'),
    ('price_10_credits', '2000000'),
    ('price_30_credits', '5000000'),
    ('discount_code', 'SHOMAL100'),
    ('discount_credits_bonus', '0')
ON CONFLICT (key) DO NOTHING;

CREATE TABLE IF NOT EXISTS customers (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    name TEXT,
    phone TEXT,
    city TEXT,
    is_flagged BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS contractors (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    name TEXT,
    phone TEXT,
    city TEXT,
    categories TEXT[] DEFAULT '{}',
    resume TEXT,
    credits INT DEFAULT 10,
    is_vip BOOLEAN DEFAULT false,
    referred_by BIGINT REFERENCES contractors(telegram_id),
    total_applications INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE SEQUENCE IF NOT EXISTS project_code_seq START 1;

CREATE TABLE IF NOT EXISTS projects (
    id BIGSERIAL PRIMARY KEY,
    project_code TEXT UNIQUE NOT NULL,
    customer_id BIGINT REFERENCES customers(id) NOT NULL,
    city TEXT,
    categories TEXT[] DEFAULT '{}',
    description TEXT,
    photo_file_id TEXT,
    budget TEXT,
    status TEXT DEFAULT 'active',
    closed_reason TEXT,
    channel_message_id BIGINT,
    vip_channel_message_id BIGINT,
    created_at TIMESTAMPTZ DEFAULT now(),
    closed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_projects_customer ON projects(customer_id);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);

CREATE TABLE IF NOT EXISTS applications (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT REFERENCES projects(id) NOT NULL,
    contractor_id BIGINT REFERENCES contractors(id) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(project_id, contractor_id)
);

CREATE INDEX IF NOT EXISTS idx_applications_project ON applications(project_id);
CREATE INDEX IF NOT EXISTS idx_applications_contractor ON applications(contractor_id);

CREATE TABLE IF NOT EXISTS payments (
    id BIGSERIAL PRIMARY KEY,
    contractor_id BIGINT REFERENCES contractors(id) NOT NULL,
    package_type TEXT NOT NULL,
    amount NUMERIC,
    discount_code TEXT,
    receipt_file_id TEXT,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT now(),
    approved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id BIGSERIAL PRIMARY KEY,
    task_type TEXT NOT NULL,
    payload JSONB,
    run_at TIMESTAMPTZ NOT NULL,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_pending ON scheduled_tasks(status, run_at);

CREATE TABLE IF NOT EXISTS customer_flags (
    id BIGSERIAL PRIMARY KEY,
    customer_id BIGINT REFERENCES customers(id) NOT NULL,
    reason TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ratings (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT REFERENCES projects(id) NOT NULL,
    contractor_id BIGINT REFERENCES contractors(id),
    score INT CHECK (score BETWEEN 1 AND 5),
    comment TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS events (
    id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    payload JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE OR REPLACE FUNCTION generate_project_code_atomic()
RETURNS TEXT AS $$
DECLARE
    seq_val BIGINT;
BEGIN
    seq_val := nextval('project_code_seq');
    RETURN 'SP-' || LPAD(seq_val::TEXT, 4, '0');
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION deduct_credit_atomic(p_contractor_id BIGINT)
RETURNS INT AS $$
DECLARE
    remaining INT;
BEGIN
    UPDATE contractors
    SET credits = credits - 1
    WHERE id = p_contractor_id AND credits > 0
    RETURNING credits INTO remaining;

    IF remaining IS NULL THEN
        RETURN -1;
    END IF;

    RETURN remaining;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION apply_to_project_atomic(
    p_project_id BIGINT,
    p_contractor_id BIGINT,
    p_cap INT
)
RETURNS TABLE(
    result TEXT,
    remaining_credits INT,
    applications_count INT,
    cap_just_reached BOOLEAN
) AS $$
DECLARE
    v_status TEXT;
    v_count INT;
    v_credits INT;
    v_cap_reached BOOLEAN := false;
BEGIN
    SELECT status INTO v_status FROM projects WHERE id = p_project_id FOR UPDATE;

    IF v_status IS NULL THEN
        RETURN QUERY SELECT 'project_not_found'::TEXT, NULL::INT, NULL::INT, false;
        RETURN;
    END IF;

    IF v_status <> 'active' THEN
        RETURN QUERY SELECT 'project_closed'::TEXT, NULL::INT, NULL::INT, false;
        RETURN;
    END IF;

    IF EXISTS (
        SELECT 1 FROM applications
        WHERE project_id = p_project_id AND contractor_id = p_contractor_id
    ) THEN
        RETURN QUERY SELECT 'already_applied'::TEXT, NULL::INT, NULL::INT, false;
        RETURN;
    END IF;

    SELECT COUNT(*) INTO v_count FROM applications WHERE project_id = p_project_id;

    IF v_count >= p_cap THEN
        RETURN QUERY SELECT 'cap_reached'::TEXT, NULL::INT, v_count, false;
        RETURN;
    END IF;

    UPDATE contractors
    SET credits = credits - 1
    WHERE id = p_contractor_id AND credits > 0
    RETURNING credits INTO v_credits;

    IF v_credits IS NULL THEN
        RETURN QUERY SELECT 'no_credit'::TEXT, NULL::INT, v_count, false;
        RETURN;
    END IF;

    INSERT INTO applications (project_id, contractor_id) VALUES (p_project_id, p_contractor_id);
    UPDATE contractors SET total_applications = total_applications + 1 WHERE id = p_contractor_id;

    v_count := v_count + 1;

    IF v_count >= p_cap THEN
        UPDATE projects
        SET status = 'closed', closed_reason = 'cap_reached', closed_at = now()
        WHERE id = p_project_id;
        v_cap_reached := true;
    END IF;

    RETURN QUERY SELECT 'success'::TEXT, v_credits, v_count, v_cap_reached;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION close_project_by_customer_atomic(
    p_project_id BIGINT,
    p_customer_id BIGINT
)
RETURNS BOOLEAN AS $$
DECLARE
    v_status TEXT;
BEGIN
    SELECT status INTO v_status
    FROM projects
    WHERE id = p_project_id AND customer_id = p_customer_id
    FOR UPDATE;

    IF v_status IS NULL OR v_status <> 'active' THEN
        RETURN false;
    END IF;

    UPDATE projects
    SET status = 'closed', closed_reason = 'closed_by_customer', closed_at = now()
    WHERE id = p_project_id;

    RETURN true;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION count_customer_monthly_projects(p_customer_id BIGINT)
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
