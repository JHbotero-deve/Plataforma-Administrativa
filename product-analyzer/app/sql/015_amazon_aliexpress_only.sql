-- Retire Mercado Libre from active use without deleting catalog or order history.
INSERT INTO platforms (name, base_url)
VALUES ('aliexpress', 'https://www.aliexpress.com')
ON CONFLICT (name) DO NOTHING;

UPDATE published_cards pc
SET is_published = FALSE, updated_at = NOW()
FROM products p
JOIN platforms pl ON pl.id = p.platform_id
WHERE pc.product_id = p.id
  AND lower(pl.name) IN ('mercadolibre', 'mercado libre', 'archived_mercadolibre')
  AND pc.is_published = TRUE;

UPDATE products p
SET is_active = FALSE, updated_at = NOW()
FROM platforms pl
WHERE p.platform_id = pl.id
  AND lower(pl.name) IN ('mercadolibre', 'mercado libre', 'archived_mercadolibre')
  AND p.is_active = TRUE;

-- Keep old queue rows for audit while preventing the URL worker from retrying them.
DO $$
BEGIN
    IF to_regclass('public.link_queue') IS NOT NULL THEN
        UPDATE link_queue
        SET status = 'failed',
            error = COALESCE(error, 'Marketplace retirado; registro preservado'),
            processing_at = NULL,
            processed_at = COALESCE(processed_at, NOW())
        WHERE lower(platform) IN ('mercadolibre', 'mercado libre', 'archived_mercadolibre')
          AND status IN ('pending', 'processing');

        ALTER TABLE link_queue DROP CONSTRAINT IF EXISTS link_queue_platform_check;
        ALTER TABLE link_queue
            ADD CONSTRAINT link_queue_platform_check
            CHECK (platform IN (
                'amazon', 'aliexpress', 'mercadolibre',
                'mercado libre', 'archived_mercadolibre'
            ));
    END IF;
END $$;
