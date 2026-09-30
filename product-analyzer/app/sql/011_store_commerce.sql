ALTER TABLE published_cards ADD COLUMN IF NOT EXISTS sale_price NUMERIC(12,2);
ALTER TABLE published_cards ADD COLUMN IF NOT EXISTS cost_price NUMERIC(12,2);
ALTER TABLE published_cards ADD COLUMN IF NOT EXISTS profit_amount NUMERIC(12,2) DEFAULT 0;
ALTER TABLE published_cards ADD COLUMN IF NOT EXISTS profit_margin_pct NUMERIC(7,2) DEFAULT 0;
ALTER TABLE products ADD COLUMN IF NOT EXISTS image_gallery JSONB NOT NULL DEFAULT '[]'::jsonb;
CREATE TABLE IF NOT EXISTS store_orders (
 id BIGSERIAL PRIMARY KEY, reference VARCHAR(255) UNIQUE NOT NULL,
 product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
 publication_id INTEGER REFERENCES published_cards(id) ON DELETE SET NULL,
 customer_name VARCHAR(200) NOT NULL, customer_email VARCHAR(255) NOT NULL, customer_phone VARCHAR(40),
 quantity INTEGER NOT NULL CHECK(quantity>0), unit_price NUMERIC(12,2) NOT NULL CHECK(unit_price>0),
 cost_unit_price NUMERIC(12,2) NOT NULL DEFAULT 0 CHECK(cost_unit_price>=0), total_amount NUMERIC(12,2) NOT NULL CHECK(total_amount>0),
 estimated_profit NUMERIC(12,2) NOT NULL DEFAULT 0, currency VARCHAR(10) NOT NULL DEFAULT 'COP',
 status VARCHAR(30) NOT NULL DEFAULT 'PENDING', payment_status VARCHAR(30) NOT NULL DEFAULT 'PENDING',
 source VARCHAR(30) NOT NULL DEFAULT 'TIENDA', product_title_snapshot TEXT NOT NULL, product_image_snapshot TEXT,
 product_url_snapshot TEXT, platform_snapshot VARCHAR(100), notes TEXT, created_at TIMESTAMP DEFAULT NOW(),
 updated_at TIMESTAMP DEFAULT NOW(), paid_at TIMESTAMP
);
ALTER TABLE payment_transactions ADD COLUMN IF NOT EXISTS order_id BIGINT REFERENCES store_orders(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_store_orders_status ON store_orders(status,payment_status,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_store_orders_product ON store_orders(product_id,created_at DESC);
