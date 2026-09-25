CREATE TABLE IF NOT EXISTS curated_links (
    id SERIAL PRIMARY KEY,
    product_id INTEGER REFERENCES products(id) ON DELETE CASCADE,
    original_url TEXT NOT NULL UNIQUE,
    platform VARCHAR(50) NOT NULL,
    title TEXT NOT NULL,
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_curated_links_platform ON curated_links(platform);
CREATE INDEX IF NOT EXISTS idx_curated_links_created_at ON curated_links(created_at DESC);