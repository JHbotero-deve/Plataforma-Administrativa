import os
import time

import schedule

from analysis import score_product
from db import get_connection
from init_db import init_database
from notifications import send_telegram_alert
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
    print("== Iniciando ciclo de análisis de catálogo por URL ==")
    expire_catalog()
    print("[marketplaces] modo URL: no se ejecutan búsquedas ni reposiciones por API.")

    conn = get_connection()
    try:
        print("[ingesta] se conservan las importaciones manuales y por CLI autorizado.")
        enriched = enrich_pending_products(conn, limit=120, max_workers=4)
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
