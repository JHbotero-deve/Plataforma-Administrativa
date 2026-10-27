-- Central deja de usar Mercado Libre. Se conserva el historial, pero sus productos
-- quedan inactivos y no pueden aparecer en tarjetas publicadas.
INSERT INTO platforms (name, base_url)
VALUES ('aliexpress', 'https://www.aliexpress.com')
ON CONFLICT (name) DO NOTHING;

UPDATE published_cards pc
SET is_published = FALSE, updated_at = NOW()
FROM products p
JOIN platforms pl ON pl.id = p.platform_id
WHERE pc.product_id = p.id
  AND pl.name = 'mercadolibre'
  AND pc.is_published = TRUE;

UPDATE products p
SET is_active = FALSE, updated_at = NOW()
FROM platforms pl
WHERE p.platform_id = pl.id
  AND pl.name = 'mercadolibre'
  AND p.is_active = TRUE;

-- Conservar las claves históricas, pero sacar el nombre de la fuente del catálogo operativo.
UPDATE platforms
SET name = 'archived_mercadolibre', base_url = NULL
WHERE name = 'mercadolibre'
  AND NOT EXISTS (SELECT 1 FROM platforms WHERE name = 'archived_mercadolibre');

-- Las colas de enlaces son temporales: descartar únicamente enlaces pendientes de la fuente retirada.
DO $$
BEGIN
    IF to_regclass('public.link_queue') IS NOT NULL THEN
        DELETE FROM link_queue WHERE platform = 'mercadolibre';
        ALTER TABLE link_queue DROP CONSTRAINT IF EXISTS link_queue_platform_check;
        ALTER TABLE link_queue
            ADD CONSTRAINT link_queue_platform_check
            CHECK (platform IN ('amazon', 'aliexpress'));
    END IF;
END $$;
