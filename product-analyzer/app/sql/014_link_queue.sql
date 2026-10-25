CREATE TABLE IF NOT EXISTS link_queue (
    id BIGSERIAL PRIMARY KEY,
    url TEXT NOT NULL,
    platform TEXT NOT NULL CHECK (platform IN ('amazon', 'mercadolibre')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'done', 'failed')),
    chat_id TEXT,
    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    title TEXT,
    price NUMERIC(12,2),
    currency VARCHAR(10),
    image_url TEXT,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processing_at TIMESTAMPTZ,
    processed_at TIMESTAMPTZ,
    notified_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS link_queue_pending_idx
    ON link_queue (created_at, id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS link_queue_processing_idx
    ON link_queue (processing_at) WHERE status = 'processing';
