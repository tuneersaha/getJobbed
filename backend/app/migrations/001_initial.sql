-- GetJobbed: Initial Schema
-- All IDs: UUID gen_random_uuid() — no integer PKs that leak volume
-- No soft deletes — hard delete + user_excluded_jobs for multi-user safety
-- google_sub added per auth design (Auth0 → Google OAuth2 migration)

-- ─── Enums ───────────────────────────────────────────────────────────────────

CREATE TYPE work_arrangement AS ENUM ('remote', 'hybrid', 'onsite', 'unknown');
CREATE TYPE citizenship_req  AS ENUM ('not_required', 'required', 'unknown');
CREATE TYPE company_priority AS ENUM ('hot', 'warm', 'cold', 'dead');
CREATE TYPE ats_type         AS ENUM ('greenhouse', 'lever', 'ashby', 'workday', 'bamboohr', 'icims', 'paylocity', 'other');
CREATE TYPE job_source       AS ENUM ('greenhouse', 'lever', 'ashby', 'adzuna', 'remotive', 'themuse', 'bamboohr', 'icims', 'paylocity', 'workday', 'hn');
CREATE TYPE task_type        AS ENUM ('fetch_jobs', 'score_job', 'tailor_resume', 'sync_company_lists');
CREATE TYPE task_status      AS ENUM ('pending', 'processing', 'done', 'failed', 'dead');
CREATE TYPE fetch_run_status AS ENUM ('running', 'completed', 'failed', 'partial', 'skipped');
CREATE TYPE experience_level AS ENUM ('entry', 'mid', 'senior');

CREATE TYPE match_status AS ENUM (
    'pending', 'tailoring', 'ready', 'skipped',
    'applied', 'interviewing', 'offer', 'accepted', 'rejected',
    'deleted'
);

-- ─── Core user tables ─────────────────────────────────────────────────────────

CREATE TABLE users (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    google_sub TEXT        NOT NULL UNIQUE,
    email      TEXT        NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX idx_users_google_sub ON users(google_sub);

CREATE TABLE user_profiles (
    id                   UUID             PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id              UUID             NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    desired_roles        TEXT[]           NOT NULL DEFAULT '{}',
    experience_years     SMALLINT         NOT NULL DEFAULT 1 CHECK (experience_years >= 0),
    experience_level     experience_level NOT NULL DEFAULT 'entry',
    max_experience_years SMALLINT         NOT NULL DEFAULT 2 CHECK (max_experience_years >= 0),
    locations            TEXT[]           NOT NULL DEFAULT '{}',
    remote_ok            BOOLEAN          NOT NULL DEFAULT TRUE,
    excluded_keywords    TEXT[]           NOT NULL DEFAULT '{}',
    tailor_threshold     NUMERIC(3,2)     NOT NULL DEFAULT 0.40
        CHECK (tailor_threshold BETWEEN 0.0 AND 1.0),
    updated_at           TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    UNIQUE (user_id)
);
CREATE INDEX idx_user_profiles_user_id ON user_profiles(user_id);

CREATE TABLE user_resumes (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    latex_source   TEXT        NOT NULL,
    parsed_skills  TEXT[]      NOT NULL DEFAULT '{}',
    parsed_summary TEXT,
    embedding      FLOAT[],
    version        INTEGER     NOT NULL DEFAULT 1 CHECK (version > 0),
    is_active      BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_user_resumes_user_id ON user_resumes(user_id);
-- Enforces only one active resume per user at DB level
CREATE UNIQUE INDEX idx_one_active_resume ON user_resumes(user_id) WHERE is_active = TRUE;

-- ─── Company & job tables ─────────────────────────────────────────────────────

CREATE TABLE companies (
    id               UUID             PRIMARY KEY DEFAULT gen_random_uuid(),
    name             TEXT,
    website          TEXT,
    ats_type         ats_type,
    ats_slug         TEXT,
    priority         company_priority NOT NULL DEFAULT 'cold',
    last_job_found_at TIMESTAMPTZ,
    dead_since       TIMESTAMPTZ,
    dead_retry_at    TIMESTAMPTZ,
    dead_probe_count SMALLINT         NOT NULL DEFAULT 0,
    UNIQUE (ats_type, ats_slug)
);
CREATE INDEX idx_companies_priority  ON companies(priority);
CREATE INDEX idx_companies_dead_retry ON companies(dead_retry_at) WHERE priority = 'dead';

CREATE TABLE jobs (
    id                          UUID             PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id                 TEXT             NOT NULL,
    source                      job_source       NOT NULL,
    company_id                  UUID             REFERENCES companies(id) ON DELETE SET NULL,
    company_name                TEXT             NOT NULL,
    title                       TEXT             NOT NULL,
    description                 TEXT             NOT NULL,
    location                    TEXT,
    work_type                   work_arrangement NOT NULL DEFAULT 'unknown',
    apply_url                   TEXT             NOT NULL,
    experience_min              SMALLINT,
    experience_max              SMALLINT,
    requires_foreign_citizenship citizenship_req  NOT NULL DEFAULT 'unknown',
    embedding                   FLOAT[],
    posted_at                   TIMESTAMPTZ,
    fetched_at                  TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    UNIQUE (source, external_id)
);
CREATE INDEX idx_jobs_source     ON jobs(source);
CREATE INDEX idx_jobs_fetched_at ON jobs(fetched_at DESC);
CREATE INDEX idx_jobs_work_type  ON jobs(work_type) WHERE work_type = 'remote';

-- ─── Matching & exclusion tables ─────────────────────────────────────────────

-- Per-user job exclusions — jobs row stays so other users can still match it
CREATE TABLE user_excluded_jobs (
    user_id    UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id     UUID        NOT NULL REFERENCES jobs(id)  ON DELETE CASCADE,
    reason     TEXT        NOT NULL DEFAULT 'deleted'
        CHECK (reason IN ('deleted', 'not_interested')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, job_id)
);

CREATE TABLE user_job_matches (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id              UUID         NOT NULL REFERENCES jobs(id)  ON DELETE CASCADE,
    match_score         NUMERIC(5,4) NOT NULL CHECK (match_score BETWEEN 0 AND 1),
    gap_analysis        JSONB        NOT NULL DEFAULT '{}',
    -- gap_analysis shape: {matching_skills: [], missing_skills: [], experience_gap: null|str, skills_coverage: float}
    status              match_status NOT NULL DEFAULT 'pending',
    applied_at          TIMESTAMPTZ,
    tailoring_failed_at TIMESTAMPTZ,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, job_id)
);
CREATE INDEX idx_matches_user_id ON user_job_matches(user_id);
CREATE INDEX idx_matches_job_id  ON user_job_matches(job_id);
CREATE INDEX idx_matches_status  ON user_job_matches(user_id, status);
CREATE INDEX idx_matches_score   ON user_job_matches(user_id, match_score DESC);

CREATE TABLE tailored_resumes (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    match_id          UUID        NOT NULL REFERENCES user_job_matches(id) ON DELETE CASCADE UNIQUE,
    latex_source      TEXT        NOT NULL,
    model_used        TEXT        NOT NULL,
    prompt_tokens     INTEGER     NOT NULL CHECK (prompt_tokens >= 0),
    completion_tokens INTEGER     NOT NULL CHECK (completion_tokens >= 0),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_tailored_match_id ON tailored_resumes(match_id);

-- ─── Queue & audit tables ─────────────────────────────────────────────────────

-- Postgres SKIP LOCKED queue — no Redis, no Celery
CREATE TABLE job_queue (
    id           BIGSERIAL   PRIMARY KEY,
    task_type    task_type   NOT NULL,
    payload      JSONB       NOT NULL DEFAULT '{}',
    status       task_status NOT NULL DEFAULT 'pending',
    attempts     SMALLINT    NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts SMALLINT    NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
    run_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    locked_until TIMESTAMPTZ,
    last_error   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- Covering index for the SKIP LOCKED poll query
CREATE INDEX idx_queue_poll ON job_queue(task_type, run_at) WHERE status = 'pending';

CREATE TABLE fetch_runs (
    id           UUID             PRIMARY KEY DEFAULT gen_random_uuid(),
    source       TEXT             NOT NULL,
    started_at   TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    jobs_found   INTEGER,
    jobs_new     INTEGER          CHECK (jobs_new >= 0),
    status       fetch_run_status NOT NULL DEFAULT 'running',
    error        TEXT
);
CREATE INDEX idx_fetch_runs_source ON fetch_runs(source, started_at DESC);

-- ─── Row Level Security ───────────────────────────────────────────────────────
-- FastAPI connects via Supabase Transaction Pooler as the postgres superuser.
-- Superusers bypass RLS automatically — no bypass policy needed.
-- RLS is enabled as defense-in-depth: direct DB access (console, psql) never
-- leaks cross-user data even without app-layer filters.

ALTER TABLE users               ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_profiles       ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_resumes        ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_job_matches    ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_excluded_jobs  ENABLE ROW LEVEL SECURITY;
ALTER TABLE tailored_resumes    ENABLE ROW LEVEL SECURITY;
ALTER TABLE companies           ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobs                ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_queue           ENABLE ROW LEVEL SECURITY;
ALTER TABLE fetch_runs          ENABLE ROW LEVEL SECURITY;
