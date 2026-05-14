-- ============================================================
-- LinkedIn Automation Engine — Supabase Schema
-- Run this in the Supabase SQL editor (Project → SQL Editor → New Query)
-- Safe to re-run: uses IF NOT EXISTS / OR REPLACE throughout
-- ============================================================


-- ── posts ─────────────────────────────────────────────────────────────────────
-- Every LinkedIn post that moves through the pipeline.
-- Populated by generate_posts.py, updated by publish_post.py and analytics sync.

CREATE TABLE IF NOT EXISTS posts (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    linkedin_urn        TEXT,                           -- LinkedIn UGC post URN (set after publish)
    topic               TEXT,                           -- Source topic from Google Sheets
    category            TEXT        CHECK (category IN (
                            'build-log',
                            'transformation',
                            'hot-take',
                            'behind-scenes',
                            'story'
                        )),
    audience            TEXT        CHECK (audience IN ('engineer', 'founder', 'story')),
    post_text           TEXT        NOT NULL,
    published_at        TIMESTAMPTZ,
    -- Analytics (populated by a future analytics-sync job)
    impressions         INTEGER     DEFAULT 0,
    likes               INTEGER     DEFAULT 0,
    comments_count      INTEGER     DEFAULT 0,
    shares              INTEGER     DEFAULT 0,
    clicks              INTEGER     DEFAULT 0,
    engagement_score    FLOAT       DEFAULT 0,          -- (likes + comments*2 + shares*3) / impressions * 100
    analytics_captured  BOOLEAN     DEFAULT FALSE,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_posts_linkedin_urn    ON posts(linkedin_urn);
CREATE INDEX IF NOT EXISTS idx_posts_published_at   ON posts(published_at);
CREATE INDEX IF NOT EXISTS idx_posts_category       ON posts(category);
CREATE INDEX IF NOT EXISTS idx_posts_audience       ON posts(audience);
ALTER TABLE posts ENABLE ROW LEVEL SECURITY;


-- ── processed_comments ────────────────────────────────────────────────────────
-- Deduplication log for comment_triage.py.
-- Every comment seen is recorded here — whether actioned or not.

CREATE TABLE IF NOT EXISTS processed_comments (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    comment_id          TEXT        UNIQUE NOT NULL,    -- LinkedIn comment ID
    post_linkedin_urn   TEXT        NOT NULL,
    category            TEXT        CHECK (category IN ('A', 'B', 'C')),
    processed_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_processed_comments_post ON processed_comments(post_linkedin_urn);
ALTER TABLE processed_comments ENABLE ROW LEVEL SECURITY;


-- ── config ────────────────────────────────────────────────────────────────────
-- Key-value store for runtime configuration.
-- Primary use: storing top_performers JSON that generate_posts.py reads
-- to bias prompt selection toward historically high-engagement formats.

CREATE TABLE IF NOT EXISTS config (
    key         TEXT        PRIMARY KEY,
    value       TEXT        NOT NULL,                   -- JSON string or plain value
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Seed default config rows
INSERT INTO config (key, value) VALUES
    ('top_performers',   '[]'),                         -- Array of {category, audience, engagement_score}
    ('post_count_week',  '0'),                          -- Reset weekly by digest job
    ('last_digest_sent', 'null')
ON CONFLICT (key) DO NOTHING;

-- Auto-update config.updated_at
CREATE OR REPLACE FUNCTION update_config_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS config_updated_at ON config;
CREATE TRIGGER config_updated_at
    BEFORE UPDATE ON config
    FOR EACH ROW EXECUTE FUNCTION update_config_updated_at();
ALTER TABLE config ENABLE ROW LEVEL SECURITY;


-- ── content_backlog ───────────────────────────────────────────────────────────
-- Category B comments from triage — potential future post ideas.
-- Mirrors the "Content Backlog" tab in Google Sheets (Sheets is the working view;
-- this table is the persistent source of truth).

CREATE TABLE IF NOT EXISTS content_backlog (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    comment_text        TEXT        NOT NULL,
    commenter_name      TEXT,
    original_post_topic TEXT,
    suggested_angle     TEXT,                           -- Groq's suggested post angle from this comment
    added_at            TIMESTAMPTZ DEFAULT NOW(),
    used                BOOLEAN     DEFAULT FALSE       -- TRUE once converted into a Topic Bank entry
);

CREATE INDEX IF NOT EXISTS idx_content_backlog_used ON content_backlog(used);
ALTER TABLE content_backlog ENABLE ROW LEVEL SECURITY;


-- ── stories ───────────────────────────────────────────────────────────────────
-- Raw Discord story submissions. Linked to posts once processed.

CREATE TABLE IF NOT EXISTS stories (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    discord_message_id  TEXT        UNIQUE NOT NULL,
    discord_author      TEXT        NOT NULL,
    raw_content         TEXT        NOT NULL,
    processed_post_id   UUID        REFERENCES posts(id),
    status              TEXT        NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'processed', 'rejected')),
    submitted_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_stories_status ON stories(status);
ALTER TABLE stories ENABLE ROW LEVEL SECURITY;


-- ── leads_seen ────────────────────────────────────────────────────────────────
-- Deduplication table for search_leads.py
-- Every LinkedIn post URL found is recorded here to prevent duplicate alerts

CREATE TABLE IF NOT EXISTS leads_seen (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    post_url        TEXT        UNIQUE NOT NULL,
    poster_name     TEXT,
    post_snippet    TEXT,
    search_query    TEXT,
    category        TEXT        CHECK (category IN ('asking_for_help', 'describing_problem')),
    discord_alerted BOOLEAN     DEFAULT FALSE,
    found_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_leads_seen_url ON leads_seen(post_url);
CREATE INDEX IF NOT EXISTS idx_leads_seen_found_at ON leads_seen(found_at);
ALTER TABLE leads_seen ENABLE ROW LEVEL SECURITY;

-- This automation runs through SUPABASE_SERVICE_ROLE_KEY only.
-- Keep direct browser/API roles locked out unless explicit policies are added.
REVOKE ALL ON TABLE posts FROM anon, authenticated;
REVOKE ALL ON TABLE processed_comments FROM anon, authenticated;
REVOKE ALL ON TABLE config FROM anon, authenticated;
REVOKE ALL ON TABLE content_backlog FROM anon, authenticated;
REVOKE ALL ON TABLE stories FROM anon, authenticated;
REVOKE ALL ON TABLE leads_seen FROM anon, authenticated;
