ALTER TABLE products
ADD COLUMN IF NOT EXISTS storefront_excluded BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_products_storefront_excluded
ON products(storefront_excluded)
WHERE storefront_excluded = TRUE;
