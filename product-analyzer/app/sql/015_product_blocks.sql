-- Baja manual de productos: evita que el worker los reactive automaticamente.
ALTER TABLE products ADD COLUMN IF NOT EXISTS is_blocked BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS idx_products_active_blocked ON products(is_active, is_blocked);