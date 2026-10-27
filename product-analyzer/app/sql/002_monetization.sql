-- =========================================================
-- Monetización: afiliados + dropshipping + suscripción SaaS
-- =========================================================

-- IMPORTANTE: Borramos la vista antes de alterar la tabla para evitar el error "cannot drop columns from view"
DROP VIEW IF EXISTS product_price_comparison;

-- Cómo se monetiza cada producto: por afiliado (comisión) o
-- por dropshipping (vos comprás y revendés con margen)
ALTER TABLE products ADD COLUMN IF NOT EXISTS monetization_type VARCHAR(20)
    DEFAULT 'affiliate' CHECK (monetization_type IN ('affiliate', 'dropship'));

-- Para dropshipping: costo real al proveedor, para calcular margen
ALTER TABLE products ADD COLUMN IF NOT EXISTS supplier_cost NUMERIC(12,2);

-- --------------------------------------------------------
-- Usuarios de la plataforma (compradores finales y vendedores
-- que se suscriben para usar el buscador/dashboard)
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    email         VARCHAR(255) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          VARCHAR(20) DEFAULT 'customer' CHECK (role IN ('customer', 'reseller', 'admin')),
    created_at    TIMESTAMP DEFAULT NOW()
);

-- --------------------------------------------------------
-- Suscripción SaaS: vendedores que pagan para usar el análisis
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS subscription_plans (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(50) UNIQUE NOT NULL,   -- 'free', 'pro', 'business'
    price       NUMERIC(10,2) NOT NULL,
    billing_period VARCHAR(20) DEFAULT 'monthly' CHECK (billing_period IN ('monthly', 'yearly')),
    max_searches_per_day INTEGER,
    features    TEXT
);

INSERT INTO subscription_plans (name, price, max_searches_per_day, features) VALUES
    ('free', 0, 10, 'Búsqueda básica, sin exportar datos'),
    ('pro', 9990, 200, 'Búsqueda ilimitada por categoría, alertas de precio, exportar CSV'),
    ('business', 29990, NULL, 'Todo lo de Pro + acceso a la API + soporte prioritario')
ON CONFLICT (name) DO NOTHING;

CREATE TABLE IF NOT EXISTS subscriptions (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
    plan_id     INTEGER REFERENCES subscription_plans(id),
    status      VARCHAR(20) DEFAULT 'active' CHECK (status IN ('active', 'cancelled', 'past_due')),
    started_at  TIMESTAMP DEFAULT NOW(),
    renews_at   TIMESTAMP,
    UNIQUE (user_id)  -- un usuario tiene una suscripción activa a la vez
);

-- --------------------------------------------------------
-- Afiliados: clics y conversiones de Amazon, AliExpress y TikTok.
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS affiliate_clicks (
    id          SERIAL PRIMARY KEY,
    product_id  INTEGER REFERENCES products(id) ON DELETE CASCADE,
    user_id     INTEGER REFERENCES users(id),   -- null si el visitante no está logueado
    clicked_at  TIMESTAMP DEFAULT NOW(),
    converted   BOOLEAN DEFAULT FALSE,           -- se marca true si se confirma la venta
    commission_earned NUMERIC(12,2)
);

CREATE INDEX IF NOT EXISTS idx_affiliate_clicks_product ON affiliate_clicks(product_id);

-- --------------------------------------------------------
-- Dropshipping: órdenes donde la plataforma compra al
-- proveedor y revende directo, con margen propio
-- --------------------------------------------------------
CREATE TABLE IF NOT EXISTS orders (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER REFERENCES users(id),
    product_id      INTEGER REFERENCES products(id),
    quantity        INTEGER DEFAULT 1,
    sale_price      NUMERIC(12,2) NOT NULL,   -- precio al que se le vendió al cliente
    supplier_cost   NUMERIC(12,2) NOT NULL,   -- lo que costó comprarlo al proveedor
    margin          NUMERIC(12,2) GENERATED ALWAYS AS
                        ((sale_price - supplier_cost)) STORED,
    status          VARCHAR(20) DEFAULT 'pending'
                        CHECK (status IN ('pending', 'paid', 'shipped', 'delivered', 'cancelled')),
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_product ON orders(product_id);

-- Vista resumen: ingresos totales por canal, para ver de un
-- vistazo cuál modelo está funcionando mejor
CREATE OR REPLACE VIEW revenue_summary AS
SELECT
    'afiliados' AS channel,
    COALESCE(SUM(commission_earned), 0) AS total_revenue,
    COUNT(*) FILTER (WHERE converted) AS conversions
FROM affiliate_clicks
UNION ALL
SELECT
    'dropshipping' AS channel,
    COALESCE(SUM(margin), 0) AS total_revenue,
    COUNT(*) AS conversions
FROM orders WHERE status IN ('paid', 'shipped', 'delivered')
UNION ALL
SELECT
    'suscripciones' AS channel,
    COALESCE(SUM(sp.price), 0) AS total_revenue,
    COUNT(*) AS conversions
FROM subscriptions s
JOIN subscription_plans sp ON sp.id = s.plan_id
WHERE s.status = 'active';
