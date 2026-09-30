-- Central: productos propios y publicación multicanal.
INSERT INTO platforms (name, base_url) VALUES ('personal', NULL) ON CONFLICT (name) DO NOTHING;
INSERT INTO categories (name) VALUES ('otros') ON CONFLICT (name) DO NOTHING;
ALTER TABLE products ADD COLUMN IF NOT EXISTS stock INTEGER;
ALTER TABLE products ADD COLUMN IF NOT EXISTS sku VARCHAR(150);
ALTER TABLE products ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS previous_price NUMERIC(12,2);
CREATE INDEX IF NOT EXISTS idx_products_sku ON products(sku);