-- Central solo admite las fuentes externas Amazon y AliExpress.
ALTER TABLE link_queue DROP CONSTRAINT IF EXISTS link_queue_platform_check;
DELETE FROM link_queue WHERE lower(platform) IN ('mercadolibre', 'mercado libre');
ALTER TABLE link_queue ADD CONSTRAINT link_queue_platform_check CHECK (platform IN ('amazon', 'aliexpress'));

UPDATE published_cards pc
SET is_published = FALSE, updated_at = NOW()
FROM products p
JOIN platforms pl ON pl.id = p.platform_id
WHERE pc.product_id = p.id
  AND lower(pl.name) IN ('mercadolibre', 'mercado libre');

UPDATE products p
SET is_active = FALSE, updated_at = NOW()
FROM platforms pl
WHERE p.platform_id = pl.id
  AND lower(pl.name) IN ('mercadolibre', 'mercado libre');

DROP TABLE IF EXISTS meli_oauth_tokens;
DROP TABLE IF EXISTS meli_oauth_state;
