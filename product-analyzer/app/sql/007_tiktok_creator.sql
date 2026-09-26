-- =========================================================
-- TikTok Shop Creator / Affiliate
-- =========================================================

CREATE TABLE IF NOT EXISTS tiktok_creator_products (
    id SERIAL PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    tiktok_product_id VARCHAR(150) NOT NULL,
    showcase_status VARCHAR(30) NOT NULL DEFAULT 'showcase',
    affiliate_url TEXT,
    commission_rate NUMERIC(7,4),
    estimated_commission NUMERIC(12,2),
    video_url TEXT,
    content_type VARCHAR(20),
    raw_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    synced_at TIMESTAMP DEFAULT NOW(),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(product_id),
    UNIQUE(tiktok_product_id)
);

CREATE INDEX IF NOT EXISTS idx_tiktok_creator_products_status
    ON tiktok_creator_products(showcase_status);
CREATE INDEX IF NOT EXISTS idx_tiktok_creator_products_synced
    ON tiktok_creator_products(synced_at DESC);

CREATE TABLE IF NOT EXISTS tiktok_creator_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    open_id VARCHAR(255),
    granted_scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
    user_type INTEGER,
    last_sync_at TIMESTAMP,
    last_error TEXT,
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tiktok_creator_clicks (
    id BIGSERIAL PRIMARY KEY,
    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    affiliate_url TEXT NOT NULL,
    clicked_at TIMESTAMP DEFAULT NOW(),
    source VARCHAR(50) DEFAULT 'central'
);

CREATE INDEX IF NOT EXISTS idx_tiktok_creator_clicks_product
    ON tiktok_creator_clicks(product_id, clicked_at DESC);
