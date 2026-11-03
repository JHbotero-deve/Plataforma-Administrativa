import os
import time

import schedule

from analysis import score_product
from amazon_creators import run_amazon_storefront_refill
from db import get_connection, upsert_product
from init_db import init_database
from marketplace_search import process_marketplace_search_requests
from notifications import send_telegram_alert
from product_sources import enrich_pending_products
from promotion_bots import run_promotion_cycle, run_storefront_refill
from seed_catalog import seed_catalog
from tiktok_creator import creator_configured, sync_showcase

SCORE_THRESHOLD = float(os.getenv("SCORE_THRESHOLD", "50"))
PROMOTION_API_ENABLED = os.getenv("ENABLE_ALIEXPRESS_PROMOTION_API", "false").strip().lower() in {"1", "true", "yes"}
STOREFRONT_AUTO_REFILL_ENABLED = os.getenv("STOREFRONT_AUTO_REFILL_ENABLED", "true").strip().lower() in {"1", "true", "yes"}
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
                SET is_active = FALSE, updated_at = NOW()
                WHERE is_active = TRUE
                  AND catalog_expires_at IS NOT NULL
                  AND catalog_expires_at <= NOW()
                RETURNING id
            """)
            expired = cur.rowcount
        conn.commit()
        print(f"== Productos vencidos retirados: {expired} ==")
    finally:
        conn.close()


def run_pipeline():
    print("== Iniciando ciclo de análisis de catálogo y promociones AliExpress ==")
    expire_catalog()
    if PROMOTION_API_ENABLED:
        try:
            promotions = run_promotion_cycle()
            print(
                "[promotions] consultados={queried} aprobados={approved} "
                "nuevos_avisos={alerts}".format(**promotions)
            )
        except Exception as exc:
            print(f"[promotions] ciclo falló: {type(exc).__name__}: {exc}")
    else:
        print("[promotions] búsqueda por API desactivada; se mantienen las importaciones por URL.")

    conn = get_connection()
    try:
        print("[ingesta] se conservan las importaciones manuales y por CLI autorizado.")
        enriched = enrich_pending_products(conn, limit=20)
        print(f"[catalog] productos enriquecidos desde URLs públicas: {enriched}")

        averages = {}
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.name AS category, p.currency, AVG(p.current_price) AS avg_price
                FROM products p
                JOIN categories c ON c.id = p.category_id
                WHERE p.is_active = TRUE AND p.current_price IS NOT NULL
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
                  AND p.title NOT LIKE 'Producto %%'
                  AND p.image_url IS NOT NULL
                  AND p.image_url <> ''
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
                    send_telegram_alert({
                        "title": product["title"],
                        "price": product["current_price"],
                        "url": build_url(product["title"], product["product_url"]),
                        "score": round(score, 1),
                        "platform": product["platform"],
                    })
    finally:
        conn.close()

    if STOREFRONT_AUTO_REFILL_ENABLED:
        try:
            amazon_refill = run_amazon_storefront_refill()
            print(f"[storefront] reposición Amazon: {amazon_refill}")
        except Exception as exc:
            print(f"[amazon] reposición automática falló: {type(exc).__name__}: {str(exc)[:240]}")
        try:
            refill = run_storefront_refill()
            print(f"[storefront] reposición AliExpress: {refill}")
        except Exception as exc:
            print(f"[aliexpress] reposición automática falló: {type(exc).__name__}: {str(exc)[:240]}")
    else:
        print("[storefront] reposición automática desactivada por configuración.")

    if creator_configured():
        try:
            result = sync_showcase(limit=200)
            print(f"[tiktok] productos sincronizados: {result.get('synced', 0)}")
        except Exception as exc:
            print(f"[tiktok] error sincronizando Creator: {exc}")
    else:
        print("[tiktok] integración no configurada; se conserva el ciclo principal.")
    print("== Ciclo completo ==")


def run_worker():
    print("Esperando a que la base de datos esté lista...")
    time.sleep(5)
    init_database()
    seeded = seed_catalog()
    print(
        f"== Catálogo inicial: Amazon {seeded['amazon']} | "
        f"AliExpress por URL {seeded['aliexpress']} | Propios {seeded['personal']} =="
    )
    run_pipeline()
    schedule.every(2).hours.do(run_pipeline)
    while True:
        try:
            process_marketplace_search_requests(limit=1)
        except Exception as exc:
            print(f"[marketplace-search] cola falló: {type(exc).__name__}: {str(exc)[:240]}")
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    run_worker()
