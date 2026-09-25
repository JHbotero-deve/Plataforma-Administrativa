ALTER TABLE products ADD COLUMN IF NOT EXISTS source_metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX IF NOT EXISTS idx_products_source_metadata ON products USING GIN (source_metadata);
