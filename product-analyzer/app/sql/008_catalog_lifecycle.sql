-- Catalog lifecycle: ingest every 2 hours and expire products after 48 hours.
ALTER TABLE products ADD COLUMN IF NOT EXISTS catalog_batch_id TIMESTAMP DEFAULT NOW();
ALTER TABLE products ADD COLUMN IF NOT EXISTS catalog_expires_at TIMESTAMP;
UPDATE products SET catalog_batch_id = COALESCE(catalog_batch_id, created_at), catalog_expires_at = COALESCE(catalog_expires_at, created_at + INTERVAL '48 hours');
CREATE INDEX IF NOT EXISTS idx_products_catalog_expiry ON products(is_active, catalog_expires_at);
