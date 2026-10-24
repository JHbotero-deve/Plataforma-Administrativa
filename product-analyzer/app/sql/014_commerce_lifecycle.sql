-- Ciclo comercial completo: entrega, leads, seguimiento y devoluciones.
ALTER TABLE store_orders ADD COLUMN IF NOT EXISTS address_line VARCHAR(255);
ALTER TABLE store_orders ADD COLUMN IF NOT EXISTS city VARCHAR(120);
ALTER TABLE store_orders ADD COLUMN IF NOT EXISTS department VARCHAR(120);
ALTER TABLE store_orders ADD COLUMN IF NOT EXISTS delivery_status VARCHAR(30) NOT NULL DEFAULT 'PENDING';
ALTER TABLE store_orders ADD COLUMN IF NOT EXISTS carrier VARCHAR(100);
ALTER TABLE store_orders ADD COLUMN IF NOT EXISTS tracking_number VARCHAR(150);
ALTER TABLE store_orders ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMP;

CREATE TABLE IF NOT EXISTS store_leads (
 id BIGSERIAL PRIMARY KEY,
 order_id BIGINT UNIQUE NOT NULL REFERENCES store_orders(id) ON DELETE CASCADE,
 name VARCHAR(200) NOT NULL,
 email VARCHAR(255) NOT NULL,
 phone VARCHAR(40),
 status VARCHAR(30) NOT NULL DEFAULT 'QUEUED',
 destination VARCHAR(100) NOT NULL DEFAULT 'TELEGRAM',
 delivered_at TIMESTAMP,
 last_error TEXT,
 created_at TIMESTAMP NOT NULL DEFAULT NOW(),
 updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS store_returns (
 id BIGSERIAL PRIMARY KEY,
 order_id BIGINT UNIQUE NOT NULL REFERENCES store_orders(id) ON DELETE RESTRICT,
 reason VARCHAR(1000) NOT NULL,
 status VARCHAR(30) NOT NULL DEFAULT 'REQUESTED',
 requested_amount_in_cents BIGINT NOT NULL CHECK(requested_amount_in_cents > 0),
 refunded_amount_in_cents BIGINT NOT NULL DEFAULT 0 CHECK(refunded_amount_in_cents >= 0),
 wompi_refund_id VARCHAR(255),
 resolution_note VARCHAR(1000),
 requested_at TIMESTAMP NOT NULL DEFAULT NOW(),
 resolved_at TIMESTAMP,
 updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_store_leads_status ON store_leads(status,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_store_returns_status ON store_returns(status,requested_at DESC);
