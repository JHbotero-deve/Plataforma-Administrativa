CREATE TABLE IF NOT EXISTS promotion_alerts (
    product_id INTEGER PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    fingerprint VARCHAR(64) NOT NULL,
    last_sent_at TIMESTAMP NOT NULL DEFAULT NOW()
);
