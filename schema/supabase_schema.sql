-- ============================================================
-- LinkedIn Automation Engine — Supabase Schema
-- Run this once in the Supabase SQL editor
-- ============================================================

-- Posts: every LinkedIn post that moves through the pipeline
CREATE TABLE IF NOT EXISTS posts (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content           TEXT NOT NULL,
    image_prompt      TEXT,
    image_url         TEXT,
    audience_type     TEXT NOT NULL CHECK (audience_type IN ('technical', 'business', 'story')),
    status            TEXT NOT NULL DEFAULT 'draft'
                          CHECK (status IN ('draft', 'image_pending', 'queued', 'published', 'failed')),
    scheduled_at      TIMESTAMPTZ,
    published_at      TIMESTAMPTZ,
    linkedin_post_id  TEXT,
    error_message     TEXT,
    created_at        TIMESTAMPTZ DEFAULT NOW(),
    updated_at        TIMESTAMPTZ DEFAULT NOW()
);

-- Stories: raw submissions from Discord #stories channel
CREATE TABLE IF NOT EXISTS stories (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    discord_message_id  TEXT UNIQUE NOT NULL,
    discord_author      TEXT NOT NULL,
    raw_content         TEXT NOT NULL,
    processed_post_id   UUID REFERENCES posts(id),
    status              TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'processed', 'rejected')),
    submitted_at        TIMESTAMPTZ DEFAULT NOW()
);

-- Auto-update updated_at on posts
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS posts_updated_at ON posts;
CREATE TRIGGER posts_updated_at
    BEFORE UPDATE ON posts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Useful indexes
CREATE INDEX IF NOT EXISTS idx_posts_status       ON posts(status);
CREATE INDEX IF NOT EXISTS idx_posts_scheduled_at ON posts(scheduled_at);
CREATE INDEX IF NOT EXISTS idx_stories_status     ON stories(status);
