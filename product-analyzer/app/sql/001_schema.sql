-- =========================================================
-- Esquema base para el analizador de productos (moda)
-- Plataformas: Mercado Libre, Amazon, TikTok Shop
-- =========================================================

-- Evitar errores de dependencias con vistas
DROP VIEW IF EXISTS product_price_comparison;

CREATE TABLE IF NOT EXISTS platforms (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(50) UNIQUE NOT NULL,   -- 'mercadolibre', 'amazon', 'tiktok'
    base_url    VARCHAR(255)
);

INSERT INTO platforms (name, base_url) VALUES
    ('mercadolibre', 'https://api.mercadolibre.com'),
    ('amazon',       'https://webservices.amazon.com/paapi5'),
    ('tiktok',       'https://open-api.tiktokglobalshop.com')
ON CONFLICT (name) DO NOTHING;

CREATE TABLE IF NOT EXISTS categories (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) UNIQUE NOT NULL   -- 'ropa', 'calzado', 'accesorios'
);

INSERT INTO categories (name) VALUES
    ('ropa'), ('calzado'), ('accesorios')
ON CONFLICT (name) DO NOTHING;

CREATE TABLE IF NOT EXISTS sellers (
    id           SERIAL PRIMARY KEY,
    platform_id  INTEGER REFERENCES platforms(id),
    external_id  VARCHAR(100),           -- id del vendedor en la plataforma origen
    name         VARCHAR(255),
    reputation   NUMERIC(4,2),           -- 0.00 - 5.00
    UNIQUE (platform_id, external_id)
);

CREATE TABLE IF NOT EXISTS products (
    id              SERIAL PRIMARY KEY,
    platform_id     INTEGER REFERENCES platforms(id) NOT NULL,
    category_id     INTEGER REFERENCES categories(id),
    seller_id       INTEGER REFERENCES sellers(id),
    external_id     VARCHAR(150) NOT NULL,   -- SKU/ASIN/MLA-id original
    title           TEXT NOT NULL,
    image_url       TEXT,
    product_url     TEXT,
    current_price   NUMERIC(12,2),
    currency        VARCHAR(10) DEFAULT 'COP',
    rating          NUMERIC(3,2),
    reviews_count   INTEGER DEFAULT 0,
    sales_estimate  INTEGER,                 -- ventas estimadas si la plataforma lo expone
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE (platform_id, external_id)
);

CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id);
CREATE INDEX IF NOT EXISTS idx_products_platform ON products(platform_id);

-- Histórico de precios: permite ver tendencias y detectar bajas/subas
CREATE TABLE IF NOT EXISTS price_history (
    id          SERIAL PRIMARY KEY,
    product_id  INTEGER REFERENCES products(id) ON DELETE CASCADE,
    price       NUMERIC(12,2) NOT NULL,
    recorded_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_price_history_product ON price_history(product_id, recorded_at);

-- Resultado del análisis/scoring inteligente por producto
CREATE TABLE IF NOT EXISTS product_scores (
    id              SERIAL PRIMARY KEY,
    product_id      INTEGER REFERENCES products(id) ON DELETE CASCADE UNIQUE,
    price_score     NUMERIC(5,2),   -- qué tan competitivo es el precio vs. otras plataformas
    demand_score    NUMERIC(5,2),   -- basado en reviews/ventas estimadas
    trend_score     NUMERIC(5,2),   -- variación de precio/ventas en el tiempo
    opportunity_score NUMERIC(5,2), -- score final combinado
    calculated_at   TIMESTAMP DEFAULT NOW()
);

-- Vista de conveniencia: mismo producto (por título similar) comparado entre plataformas
CREATE OR REPLACE VIEW product_price_comparison AS
SELECT
    p.title,
    pl.name AS platform,
    p.current_price,
    p.rating,
    p.product_url
FROM products p
JOIN platforms pl ON pl.id = p.platform_id
WHERE p.is_active = TRUE
ORDER BY p.title, p.current_price ASC;
