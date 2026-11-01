CREATE TABLE IF NOT EXISTS link_queue (
    id SERIAL PRIMARY KEY,
    url TEXT NOT NULL,
    platform TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    processing_at TIMESTAMPTZ,
    chat_id TEXT,
    product_id INTEGER,
    title TEXT,
    price NUMERIC,
    currency TEXT,
    image_url TEXT,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    notified_at TIMESTAMPTZ
);
ALTER TABLE link_queue ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE link_queue ADD COLUMN IF NOT EXISTS processing_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS link_queue_status_idx ON link_queue (status);
INSERT INTO platforms (name, base_url) VALUES ('aliexpress', NULL) ON CONFLICT (name) DO NOTHING;