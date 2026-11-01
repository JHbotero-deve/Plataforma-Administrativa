ALTER TABLE store_invoices
    ADD COLUMN IF NOT EXISTS public_token VARCHAR(64);

CREATE UNIQUE INDEX IF NOT EXISTS idx_store_invoices_public_token
    ON store_invoices(public_token)
    WHERE public_token IS NOT NULL AND public_token<>'';

CREATE TABLE IF NOT EXISTS store_notification_outbox (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES store_orders(id) ON DELETE CASCADE,
    channel VARCHAR(20) NOT NULL,
    notification_type VARCHAR(40) NOT NULL,
    recipient VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMP NOT NULL DEFAULT NOW(),
    locked_at TIMESTAMP,
    last_error VARCHAR(120),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    sent_at TIMESTAMP,
    UNIQUE(order_id, channel, notification_type, recipient)
);

CREATE INDEX IF NOT EXISTS idx_store_notification_outbox_pending
    ON store_notification_outbox(status, available_at, id);
