import os
import time
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import schedule

from analysis import score_product
from db import get_connection, upsert_product
from ingest import fetch_amazon, fetch_mercadolibre, fetch_tiktok
from init_db import init_database
from notifications import send_telegram_alert

SEARCH_CONFIG = [
    ("ropa", "remera hombre"),
    ("ropa", "campera mujer"),
    ("calzado", "zapatillas urbanas"),
]
AMAZON_TAG = os.getenv("AMAZON_PARTNER_TAG", "").strip()
SCORE_THRESHOLD = float(os.getenv("SCORE_THRESHOLD", "50"))


def build_url(platform: str, title: str, product_url: str | None) -> str:
    if product_url and product_url != "#":
        if platform == "amazon" and AMAZON_TAG:
            parsed = urlparse(product_url)
            if parsed.scheme in ("http", "https") and "amazon." in parsed.netloc:
                query = dict(parse_qsl(parsed.query, keep_blank_values=True))
                query["tag"] = AMAZON_TAG
                return urlunparse(parsed._replace(query=urlencode(query)))
        return product_url
    q = title.replace(" ", "+")
    if platform == "amazon" and AMAZON_TAG:
        return f"https://www.amazon.com/s?k={q}&tag={AMAZON_TAG}"
    if platform == "mercadolibre":
        return f"https://listado.mercadolibre.com.co/{title.replace(' ', '-')}"
    if platform == "tiktok":
        return f"https://www.tiktok.com/search?q={q}"
    return f"https://www.google.com/search?q={q}"


def _source_enabled(platform_name: str) -> bool:
    required = {
        "mercadolibre": (),
        "amazon": ("AMAZON_CREDENTIAL_ID", "AMAZON_CREDENTIAL_SECRET", "AMAZON_PARTNER_TAG"),
        "tiktok": ("TIKTOK_APP_KEY", "TIKTOK_APP_SECRET", "TIKTOK_ACCESS_TOKEN", "TIKTOK_SHOP_CIPHER"),
    }
    return all(os.getenv(name, "").strip() for name in required[platform_name])


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
    print("== Iniciando ciclo de ingesta y análisis ==")
    expire_catalog()
    conn = get_connection()
    product_ids = []
    source_functions = [("mercadolibre", fetch_mercadolibre), ("amazon", fetch_amazon), ("tiktok", fetch_tiktok)]
    for category, term in SEARCH_CONFIG:
        for platform_name, fetch_fn in source_functions:
            if not _source_enabled(platform_name):
                print(f"[{platform_name}] fuente deshabilitada: faltan credenciales.")
                continue
            try:
                products = fetch_fn(term)
            except Exception as exc:
                print(f"[{platform_name}] error trayendo '{term}': {exc}")
                continue
            if not products:
                print(f"[{platform_name}] sin resultados reales para '{term}'.")
                continue
            for product in products:
                try:
                    product_ids.append(upsert_product(conn, platform_name, category, product))
                except Exception as exc:
                    conn.rollback()
                    print(f"[{platform_name}] error insertando producto: {exc}")
    averages = {}
    with conn.cursor() as cur:
        cur.execute("""
            SELECT c.name AS category, p.currency, AVG(p.current_price) AS avg_price
            FROM products p JOIN categories c ON c.id = p.category_id
            WHERE p.is_active = TRUE AND p.current_price IS NOT NULL
            GROUP BY c.name, p.currency
        """)
        for row in cur.fetchall():
            averages[(row["category"], row["currency"])] = float(row["avg_price"] or 0)
    for product_id in set(product_ids):
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.name AS category, p.currency FROM products p
                LEFT JOIN categories c ON c.id = p.category_id WHERE p.id = %s
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
                    FROM products p JOIN platforms pl ON pl.id = p.platform_id WHERE p.id = %s
                """, (product_id,))
                product = cur.fetchone()
            if product:
                send_telegram_alert({"title": product["title"], "price": product["current_price"], "url": build_url(product["platform"], product["title"], product["product_url"]), "score": round(score, 1)})
    conn.close()
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
