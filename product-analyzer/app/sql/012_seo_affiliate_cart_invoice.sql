ALTER TABLE products ADD COLUMN IF NOT EXISTS affiliate_url TEXT;
CREATE TABLE IF NOT EXISTS affiliate_clicks (
 id BIGSERIAL PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
 platform VARCHAR(50) NOT NULL, target_url TEXT NOT NULL, clicked_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS store_order_items (
 id BIGSERIAL PRIMARY KEY, order_id BIGINT NOT NULL REFERENCES store_orders(id) ON DELETE CASCADE,
 product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT, publication_id INTEGER REFERENCES published_cards(id) ON DELETE SET NULL,
 product_title TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity>0), unit_price NUMERIC(12,2) NOT NULL,
 cost_unit_price NUMERIC(12,2) NOT NULL DEFAULT 0, line_total NUMERIC(12,2) NOT NULL, estimated_profit NUMERIC(12,2) NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS store_invoices (
 id BIGSERIAL PRIMARY KEY, order_id BIGINT UNIQUE NOT NULL REFERENCES store_orders(id) ON DELETE RESTRICT,
 invoice_number VARCHAR(100) UNIQUE NOT NULL, total_amount NUMERIC(12,2) NOT NULL, currency VARCHAR(10) NOT NULL,
 status VARCHAR(30) NOT NULL DEFAULT 'ISSUED', issued_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_affiliate_clicks_product ON affiliate_clicks(product_id,clicked_at DESC);
CREATE INDEX IF NOT EXISTS idx_order_items_order ON store_order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_invoices_issued ON store_invoices(issued_at DESC);