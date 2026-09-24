import time
import os
import threading
import schedule
import uvicorn

from db import get_connection, upsert_product
from ingest import fetch_mercadolibre, fetch_amazon, fetch_tiktok
from analysis import score_product
from notifications import send_telegram_alert
from init_db import init_database
from api import app as api_app

SEARCH_CONFIG = [
    ("ropa", "remera hombre"),
    ("ropa", "campera mujer"),
    ("calzado", "zapatillas urbanas"),
]
AMAZON_TAG = os.getenv("AMAZON_PARTNER_TAG", "jh0c35-20")
SCORE_THRESHOLD = float(os.getenv("SCORE_THRESHOLD", "50"))


def build_url(platform, title, product_url):
    if product_url and product_url != "#":
        return product_url
    q = title.replace(" ", "+")
    if platform == "amazon":
        return f"https://www.amazon.com/s?k={q}&tag={AMAZON_TAG}"
    if platform == "mercadolibre":
        return f"https://listado.mercadolibre.com.co/{title.replace(' ', '-')}"
    if platform == "tiktok":
        return f"https://www.tiktok.com/search?q={q}"
    return f"https://www.google.com/search?q={q}"


def start_api_server():
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(api_app, host="0.0.0.0", port=port)


def run_pipeline():
    print("⏳ Esperando a que la base de datos esté lista...")
    time.sleep(5)
    print("== Iniciando ciclo de ingesta y análisis ==")
    conn = get_connection()
    product_ids = []

    source_functions = [
        ("mercadolibre", fetch_mercadolibre),
        ("amazon", fetch_amazon),
        ("tiktok", fetch_tiktok),
    ]

    for category, term in SEARCH_CONFIG:
        for platform_name, fetch_fn in source_functions:
            try:
                products = fetch_fn(term)
            except Exception as e:
                print(f"[{platform_name}] error trayendo '{term}': {e}")
                continue

            if not products:
                print(f"[{platform_name}] sin resultados reales para '{term}'.")
                continue

            valid_products = [
                p for p in products
                if p.get("external_id") and p.get("title") and p.get("price") not in (None, 0, "0", "0.0")
            ]
            print(f"[{platform_name}] {len(valid_products)} productos válidos para guardar.")
            for product in valid_products:
                try:
                    pid = upsert_product(conn, platform_name, category, product)
                    product_ids.append(pid)
                except Exception as e:
                    print(f"Error insertando producto de {platform_name}: {e}")

    averages = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.name AS category, p.currency, AVG(p.current_price) AS avg_price "
            "FROM products p JOIN categories c ON c.id = p.category_id "
            "WHERE p.is_active = TRUE AND p.current_price IS NOT NULL "
            "GROUP BY c.name, p.currency"
        )
        for row in cur.fetchall():
            averages[(row["category"], row["currency"])] = float(row["avg_price"] or 0)

    for pid in set(product_ids):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT c.name AS category, p.currency FROM products p "
                "LEFT JOIN categories c ON c.id = p.category_id WHERE p.id = %s",
                (pid,),
            )
            row = cur.fetchone()

        if not row:
            continue

        avg_price = averages.get((row["category"], row["currency"]), 0)
        score = score_product(conn, pid, avg_price)
        print(f"Producto {pid} -> opportunity_score = {score}")
        if score >= SCORE_THRESHOLD:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT p.title, p.current_price, p.product_url, pl.name AS platform FROM products p JOIN platforms pl ON pl.id = p.platform_id WHERE p.id = %s",
                    (pid,)
                )
                row = cur.fetchone()
                if row:
                    url = build_url(row["platform"],
                                    row["title"], row["product_url"])
                    send_telegram_alert({
                        "title": row["title"],
                        "price": row["current_price"],
                        "url": url,
                        "score": round(score, 1)
                    })

    conn.close()
    print("== Ciclo completo ==")


if __name__ == "__main__":
    threading.Thread(target=start_api_server, daemon=True).start()
    init_database()
    run_pipeline()

    schedule.every(6).hours.do(run_pipeline)
    while True:
        schedule.run_pending()
        time.sleep(30)
