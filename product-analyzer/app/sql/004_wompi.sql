-- =========================================================
-- Wompi: pagos reales, trazabilidad e idempotencia
-- =========================================================

CREATE TABLE IF NOT EXISTS payment_transactions (
    id SERIAL PRIMARY KEY,
    reference VARCHAR(255) UNIQUE NOT NULL,
    provider VARCHAR(30) NOT NULL DEFAULT 'wompi',
    transaction_id VARCHAR(255) UNIQUE,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    plan_id INTEGER REFERENCES subscription_plans(id) ON DELETE SET NULL,
    customer_email VARCHAR(255) NOT NULL,
    amount_in_cents BIGINT NOT NULL CHECK (amount_in_cents > 0),
    currency VARCHAR(10) NOT NULL DEFAULT 'COP',
    status VARCHAR(30) NOT NULL DEFAULT 'PENDING',
    payment_method_type VARCHAR(50),
    status_message TEXT,
    environment VARCHAR(10) NOT NULL DEFAULT 'prod',
    event_checksum VARCHAR(128),
    raw_event JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    paid_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_payment_transactions_user ON payment_transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_payment_transactions_status ON payment_transactions(status);
CREATE INDEX IF NOT EXISTS idx_payment_transactions_created ON payment_transactions(created_at);

CREATE TABLE IF NOT EXISTS payment_events (
    id BIGSERIAL PRIMARY KEY,
    provider VARCHAR(30) NOT NULL DEFAULT 'wompi',
    event_type VARCHAR(100) NOT NULL,
    transaction_id VARCHAR(255),
    checksum VARCHAR(128) NOT NULL,
    payload JSONB NOT NULL,
    received_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(provider, event_type, transaction_id, checksum)
);

CREATE INDEX IF NOT EXISTS idx_payment_events_transaction ON payment_events(transaction_id);
