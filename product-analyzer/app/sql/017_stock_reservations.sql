-- Reservas transaccionales de stock para evitar sobreventa durante el checkout.
CREATE TABLE IF NOT EXISTS store_stock_reservations (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES store_orders(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    expires_at TIMESTAMP NOT NULL DEFAULT (NOW() + INTERVAL '30 minutes'),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    released_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_stock_reservations_active
    ON store_stock_reservations(product_id, status, expires_at);

CREATE INDEX IF NOT EXISTS idx_stock_reservations_order
    ON store_stock_reservations(order_id, status);

UPDATE store_stock_reservations
SET status='EXPIRED', released_at=NOW()
WHERE status='ACTIVE' AND expires_at <= NOW();
