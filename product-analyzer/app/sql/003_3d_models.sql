-- =========================================================
-- Soporte de modelado 3D por producto
-- =========================================================

-- Agregar columnas necesarias a la tabla products
ALTER TABLE products ADD COLUMN IF NOT EXISTS model_url TEXT;
ALTER TABLE products ADD COLUMN IF NOT EXISTS model_shape VARCHAR(20) DEFAULT 'garment';

-- Borrar la vista antigua para evitar conflictos de nombres de columnas
DROP VIEW IF EXISTS product_price_comparison;

-- Crear la vista actualizada con soporte 3D
CREATE VIEW product_price_comparison AS
SELECT
    p.id,
    p.title,
    pl.name AS platform,
    p.current_price,
    p.currency,
    p.rating,
    p.product_url,
    p.image_url,
    p.model_url,
    p.model_shape
FROM products p
JOIN platforms pl ON pl.id = p.platform_id
WHERE p.is_active = TRUE;
