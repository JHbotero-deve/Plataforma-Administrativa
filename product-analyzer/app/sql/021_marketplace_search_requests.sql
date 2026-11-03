CREATE TABLE IF NOT EXISTS marketplace_search_requests (
    id BIGSERIAL PRIMARY KEY,
    query VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    result_count INTEGER NOT NULL DEFAULT 0,
    published_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_marketplace_search_requests_pending
ON marketplace_search_requests(created_at)
WHERE status = 'pending';
