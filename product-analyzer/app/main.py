import os
import time

import schedule

from analysis import score_product
from db import get_connection
from init_db import init_database
from notifications import send_telegram_alert
from pricing import publish_catalog_product
from product_sources import enrich_pending_products
from seed_catalog import seed_catalog
from tiktok_creator import creator_configured, sync_showcase

SCORE_THRESHOLD = float(os.getenv("SCORE_THRESHOLD", "50"))
def build_url(title: str, product_url: str | None) -> str:
    if product_url and product_url != "#":
        return product_url
    store_url = os.getenv("PUBLIC_STORE_URL", "https://central-7ykr.vercel.app").rstrip("/")
    return f"{store_url}/tienda"


def expire_catalog():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE products
                SET source_metadata = COALESCE(source_metadata, '{}'::jsonb) ||
                    jsonb_build_object('catalog_status', 'stale', 'catalog_stale_at', NOW()::text)
                WHERE is_active = TRUE
                  AND catalog_expires_at IS NOT NULL
                  AND catalog_expires_at <= NOW()
                  AND source_metadata->>'catalog_status' IS DISTINCT FROM 'stale'
                RETURNING id
            """)
            expired = cur.rowcount
            cur.execute("""
                UPDATE published_cards pc
                SET subtitle = 'Precio pendiente de verificación',
                    price_display = 'Precio pendiente de verificación',
                    sale_price = NULL, cost_price = NULL, profit_amount = NULL,
                    profit_margin_pct = NULL, opportunity_score = 0,
                    footer = 'Precio observado anteriormente · verifica el precio actual en la tienda',
                    updated_at = NOW()
                FROM products p
                WHERE p.id = pc.product_id AND pc.is_published = TRUE
                  AND p.is_active = TRUE AND p.catalog_expires_at <= NOW()
            """)
        conn.commit()
        print(f"== Productos marcados para revalidación (sin borrarlos ni desactivarlos): {expired} ==")
    finally:
        conn.close()


def run_pipeline():
    print("== Iniciando catálogo URL-first: solo páginas públicas de producto ==")
    conn = get_connection()
    try:
        recovered = enrich_pending_products(conn, limit=80, max_workers=4)
        print(f"[url-enrichment] productos enriquecidos/revalidados desde sus URLs públicas: {recovered}")
        expire_catalog()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.id, p.title, p.current_price, p.currency, p.image_url, p.source_metadata,
                       p.product_url, p.affiliate_url, pl.name AS platform,
                       COALESCE(s.opportunity_score, 0) AS opportunity_score
                FROM products p
                JOIN platforms pl ON pl.id = p.platform_id
                LEFT JOIN product_scores s ON s.product_id = p.id
                WHERE p.is_active = TRUE AND p.current_price > 0 AND p.currency NOT IN ('UNK', 'N/D')
                  AND p.image_url IS NOT NULL AND p.image_url <> ''
                  AND p.product_url IS NOT NULL AND p.product_url <> ''
                  AND p.title IS NOT NULL AND LOWER(p.title) NOT LIKE 'producto amazon %%'
                  AND LOWER(p.title) NOT LIKE 'producto aliexpress %%'
                  AND LOWER(p.title) NOT LIKE 'producto url-%%'
                  AND LOWER(p.title) NOT IN ('producto', 'producto amazon', 'producto aliexpress', 'producto url')
                  AND LOWER(p.title) <> 'producto'
                  AND LOWER(p.title) NOT LIKE '%%oferta destacada global%%'
                  AND LOWER(p.title) NOT LIKE '%%producto verificado con%%'
                  AND LOWER(p.title) NOT LIKE '%%titulo real del producto%%'
                  AND LOWER(p.title) NOT LIKE '%%nombre real del producto%%'
                  AND (p.catalog_expires_at IS NULL OR p.catalog_expires_at > NOW())
                  AND COALESCE(p.source_metadata->>'catalog_status', 'current') <> 'stale'
                  AND pl.name IN ('amazon', 'aliexpress')
                ORDER BY p.updated_at DESC LIMIT 1000
            """)
            catalog_rows = cur.fetchall()
        published = 0
        for row in catalog_rows:
            if publish_catalog_product(conn, row["id"], row, row["opportunity_score"], commit=False):
                published += 1
        if published:
            conn.commit()
        print(f"[storefront] productos reales elegibles publicados/actualizados: {published}")

        averages = {}
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.name AS category, p.currency, AVG(p.current_price) AS avg_price
                FROM products p
                JOIN categories c ON c.id = p.category_id
                WHERE p.is_active = TRUE AND p.current_price IS NOT NULL AND p.currency NOT IN ('UNK', 'N/D')
                GROUP BY c.name, p.currency
            """)
            for row in cur.fetchall():
                averages[(row["category"], row["currency"])] = float(row["avg_price"] or 0)
            cur.execute("""
                SELECT p.id
                FROM products p
                WHERE p.is_active = TRUE
                  AND p.current_price IS NOT NULL
                  AND p.current_price > 0
                  AND p.category_id IS NOT NULL
                  AND p.title IS NOT NULL
                  AND LOWER(p.title) NOT LIKE 'producto amazon %%'
                  AND LOWER(p.title) NOT LIKE 'producto aliexpress %%'
                  AND LOWER(p.title) NOT LIKE 'producto url-%%'
                  AND LOWER(p.title) <> 'producto'
                  AND LOWER(p.title) NOT LIKE '%%oferta destacada global%%'
                  AND LOWER(p.title) NOT LIKE '%%producto verificado con%%'
                  AND LOWER(p.title) NOT LIKE '%%titulo real del producto%%'
                  AND LOWER(p.title) NOT LIKE '%%nombre real del producto%%'
                  AND COALESCE(p.source_metadata->>'catalog_status', 'current') <> 'stale'
                  AND p.image_url IS NOT NULL
                  AND p.image_url <> ''
                  AND LOWER(COALESCE(p.image_url, '')) NOT LIKE '%%enlace-de-la-imagen.jpg%%'
                  AND LOWER(COALESCE(p.image_url, '')) NOT LIKE '%%placeholder%%'
                  AND LOWER(COALESCE(p.image_url, '')) NOT LIKE '%%example.com%%'
                  AND p.source_metadata->'field_sources'->>'title' IN ('public_product_page', 'user')
                  AND p.source_metadata->'field_sources'->>'price' IN ('public_product_page', 'user')
                  AND p.source_metadata->'field_sources'->>'currency' IN ('public_product_page', 'user')
                  AND p.source_metadata->'field_sources'->>'image_url' IN ('public_product_page', 'user')
                  AND p.source_metadata->'field_sources'->>'category' IN ('public_product_page', 'user')
                  AND NOT (COALESCE(p.source_metadata->'missing_fields', '[]'::jsonb) ?| ARRAY['title', 'price', 'currency', 'image_url'])
                ORDER BY p.updated_at DESC
                LIMIT 500
            """)
            product_ids = [row["id"] for row in cur.fetchall()]
        print(f"[catalog] productos listos para puntuar: {len(product_ids)}")

        for product_id in set(product_ids):
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT c.name AS category, p.currency
                    FROM products p
                    LEFT JOIN categories c ON c.id = p.category_id
                    WHERE p.id = %s
                """, (product_id,))
                row = cur.fetchone()
            if not row:
                continue
            score = score_product(conn, product_id, averages.get((row["category"], row["currency"]), 0))
            print(f"Producto {product_id} -> opportunity_score = {score}")
            if score >= SCORE_THRESHOLD:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT p.title, p.current_price, p.product_url, pl.name AS platform
                        FROM products p JOIN platforms pl ON pl.id = p.platform_id
                        WHERE p.id = %s
                    """, (product_id,))
                    product = cur.fetchone()
                if product:
                    try:
                        send_telegram_alert({
                            "title": product["title"],
                            "price": product["current_price"],
                            "url": build_url(product["title"], product["product_url"]),
                            "score": round(score, 1),
                            "platform": product["platform"],
                        })
                    except Exception as exc:
                        print(f"[notifications] no se pudo enviar la alerta; el análisis continúa: {type(exc).__name__}")
    finally:
        conn.close()


    if creator_configured():
        try:
            result = sync_showcase(limit=200)
            print(f"[tiktok] productos sincronizados: {result.get('synced', 0)}")
        except Exception as exc:
            print(f"[tiktok] error sincronizando Creator: {exc}")
    else:
        print("[tiktok] integración no configurada; se conserva el ciclo principal.")
    print("== Ciclo completo ==")


def _run_pipeline_safely():
    try:
        run_pipeline()
    except Exception as exc:
        print(f"[worker] ciclo fallido; el servicio permanece activo: {type(exc).__name__}: {str(exc)[:180]}")


def run_worker():
    print("Esperando a que la base de datos esté lista...")
    while True:
        try:
            init_database()
            break
        except Exception as exc:
            print(f"[worker] base de datos no disponible; nuevo intento en 30 s: {type(exc).__name__}")
            time.sleep(30)
    try:
        seeded = seed_catalog()
        print(
            f"== Catálogo inicial: Amazon {seeded['amazon']} | "
            f"AliExpress por URL {seeded['aliexpress']} | Propios {seeded['personal']} =="
        )
    except Exception as exc:
        print(f"[worker] no se pudo precargar el catálogo; continúa el procesamiento: {type(exc).__name__}")
    _run_pipeline_safely()
    schedule.every(15).minutes.do(_run_pipeline_safely)
    while True:
        schedule.run_pending()
        time.sleep(10)


if __name__ == "__main__":
    run_worker()
