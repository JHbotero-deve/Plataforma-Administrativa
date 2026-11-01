-- Conserva los snapshots financieros, pero elimina el catálogo e historial de la
-- fuente retirada. Amazon y AliExpress siguen siendo las únicas fuentes externas.
INSERT INTO platforms (name, base_url)
VALUES ('aliexpress', 'https://www.aliexpress.com')
ON CONFLICT (name) DO NOTHING;

-- Desvincula pedidos antes de borrar productos. Sus importes y snapshots se conservan.
ALTER TABLE orders ADD COLUMN IF NOT EXISTS product_title_snapshot TEXT;
ALTER TABLE orders ALTER COLUMN product_id DROP NOT NULL;
UPDATE orders o
SET product_title_snapshot = p.title
FROM products p
JOIN platforms pl ON pl.id = p.platform_id
WHERE o.product_id = p.id
  AND pl.name IN ('mercadolibre', 'archived_mercadolibre')
  AND COALESCE(o.product_title_snapshot, '') = '';

ALTER TABLE store_orders ALTER COLUMN product_id DROP NOT NULL;
ALTER TABLE store_order_items ALTER COLUMN product_id DROP NOT NULL;

DO $$
DECLARE
    fk RECORD;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'orders'::regclass
          AND confrelid = 'products'::regclass
          AND contype = 'f'
          AND confdeltype = 'n'
    ) THEN
        FOR fk IN
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'orders'::regclass
              AND confrelid = 'products'::regclass
              AND contype = 'f'
        LOOP
            EXECUTE format('ALTER TABLE orders DROP CONSTRAINT %I', fk.conname);
        END LOOP;
        ALTER TABLE orders
            ADD CONSTRAINT orders_product_id_fkey
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'store_orders'::regclass
          AND confrelid = 'products'::regclass
          AND contype = 'f'
          AND confdeltype = 'n'
    ) THEN
        FOR fk IN
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'store_orders'::regclass
              AND confrelid = 'products'::regclass
              AND contype = 'f'
        LOOP
            EXECUTE format('ALTER TABLE store_orders DROP CONSTRAINT %I', fk.conname);
        END LOOP;
        ALTER TABLE store_orders
            ADD CONSTRAINT store_orders_product_id_fkey
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'store_order_items'::regclass
          AND confrelid = 'products'::regclass
          AND contype = 'f'
          AND confdeltype = 'n'
    ) THEN
        FOR fk IN
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'store_order_items'::regclass
              AND confrelid = 'products'::regclass
              AND contype = 'f'
        LOOP
            EXECUTE format('ALTER TABLE store_order_items DROP CONSTRAINT %I', fk.conname);
        END LOOP;
        ALTER TABLE store_order_items
            ADD CONSTRAINT store_order_items_product_id_fkey
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE SET NULL;
    END IF;
END $$;

-- Quitar solo datos temporales de cola de la fuente retirada.
DO $$
BEGIN
    IF to_regclass('public.link_queue') IS NOT NULL THEN
        DELETE FROM link_queue
        WHERE platform IN ('mercadolibre', 'archived_mercadolibre');
        ALTER TABLE link_queue DROP CONSTRAINT IF EXISTS link_queue_platform_check;
        ALTER TABLE link_queue
            ADD CONSTRAINT link_queue_platform_check
            CHECK (platform IN ('amazon', 'aliexpress'));
    END IF;
END $$;

DELETE FROM products
WHERE platform_id IN (
    SELECT id FROM platforms
    WHERE name IN ('mercadolibre', 'archived_mercadolibre')
);

DELETE FROM sellers
WHERE platform_id IN (
    SELECT id FROM platforms
    WHERE name IN ('mercadolibre', 'archived_mercadolibre')
);

DELETE FROM platforms
WHERE name IN ('mercadolibre', 'archived_mercadolibre');
