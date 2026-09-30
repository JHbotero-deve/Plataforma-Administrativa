-- Published cards: admin-created product cards shown by the Central storefront.
CREATE TABLE IF NOT EXISTS published_cards (
    id                  SERIAL PRIMARY KEY,
    product_id          INTEGER NOT NULL UNIQUE REFERENCES products(id) ON DELETE CASCADE,
    title               TEXT NOT NULL,
    subtitle            TEXT,
    price_display       VARCHAR(100),
    image_url           TEXT,
    product_url         TEXT,
    opportunity_score   NUMERIC(5,2),
    footer              VARCHAR(200) DEFAULT 'Disponible en Central',
    accent              VARCHAR(20) DEFAULT '#b6f23a',
    is_published        BOOLEAN DEFAULT TRUE,
    sort_order          INTEGER DEFAULT 0,
    published_at        TIMESTAMP DEFAULT NOW(),
    updated_at          TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_published_cards_live ON published_cards(is_published, sort_order, published_at DESC);
