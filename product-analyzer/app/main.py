import os
import time

import schedule

from amazon_api import fetch_amazon_products
from analysis import score_product
from db import get_connection, upsert_product
from ingest import fetch_mercadolibre
from init_db import init_database
from notifications import send_telegram_alert
from tiktok_creator import creator_configured, sync_showcase

SEARCH_CONFIG = [
    ("ropa", "remera hombre"),
    ("ropa", "campera mujer"),
    ("calzado", "zapatillas urbanas"),
]
SCORE_THRESHOLD = float(os.getenv("SCORE_THRESHOLD", "50"))
AMAZON_BATCH_SIZE = int(os.getenv("AMAZON_BATCH_SIZE", "15"))


def build_url(title: str, product_url: str | None) -> str:
    if product_url and product_url != "#":
        return product_url
    return f"https://listado.mercadolibre.com.co/{title.replace(' ', '-')}"


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


def ingest_amazon(conn) -> list[int]:
    ids = []
    with conn.cursor() as cur:
        cur.execute("""
            SELECT p.external_id
            FROM products p
            JOIN platforms pl ON pl.id = p.platform_id
            WHERE pl.name = 'amazon' AND p.is_active = TRUE
        """)
        existing_ids = {str(row["external_id"]).upper() for row in cur.fetchall()}

    products = fetch_amazon_products(existing_ids, AMAZON_BATCH_SIZE)
    print(f"[amazon] lote recibido desde Creators API: {len(products)}")
    for category, product in products:
        try:
            product["source_metadata"]["seed_batch"] = f"amazon-renewal-{AMAZON_BATCH_SIZE}"
            ids.append(upsert_product(conn, "amazon", category, product))
            print(f"[amazon] {product['external_id']} -> {product['title']} | {product['currency']} {product['price']} | imagen=si")
        except Exception as exc:
            conn.rollback()
            print(f"[amazon] error insertando {product.get('external_id')}: {exc}")
    return ids


def run_pipeline():
    print("== Iniciando ciclo de ingesta y análisis ==")
    expire_catalog()
    conn = get_connection()
    product_ids = []
    try:
        try:
            product_ids.extend(ingest_amazon(conn))
        except Exception as exc:
            print(f"[amazon] ciclo abortado: {exc}")

        for category, term in SEARCH_CONFIG:
            try:
                products = fetch_mercadolibre(term)
            except Exception as exc:
                print(f"[mercadolibre] error trayendo '{term}': {exc}")
                continue
            if not products:
                print(f"[mercadolibre] sin resultados reales para '{term}'.")
                continue
            for product in products:
                try:
                    product_ids.append(upsert_product(conn, "mercadolibre", category, product))
                except Exception as exc:
                    conn.rollback()
                    print(f"[mercadolibre] error insertando producto: {exc}")

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
                    cur.execute("SELECT p.title, p.current_price, p.product_url FROM products p WHERE p.id = %s", (product_id,))
                    product = cur.fetchone()
                if product:
                    send_telegram_alert({
                        "title": product["title"],
                        "price": product["current_price"],
                        "url": build_url(product["title"], product["product_url"]),
                        "score": round(score, 1),
                    })
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


def run_worker():
    print("Esperando a que la base de datos esté lista...")
    time.sleep(5)
    init_database()
    run_pipeline()
    schedule.every(2).hours.do(run_pipeline)
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    run_worker()
