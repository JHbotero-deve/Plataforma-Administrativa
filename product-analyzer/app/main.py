import os
import time

import schedule

from db import get_connection
from init_db import init_database
from seed_catalog import seed_catalog

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
    print("== Iniciando ciclo de mantenimiento del catálogo ==")
    expire_catalog()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS active FROM products WHERE is_active = TRUE")
            active = cur.fetchone()["active"]
        print(f"[catálogo] productos activos tras vencimientos: {active}")
    finally:
        conn.close()

    # No se ejecutan búsquedas de marketplaces: solo importaciones por URL.
    try:
        publish_top_opportunities(limit=50)
    except Exception as exc:
        print(f"[tienda] error publicando catálogo por URL: {type(exc).__name__}: {exc}")
    print("== Ciclo completo ==")

def publish_top_opportunities(limit=15):
    """Publica productos activos de Amazon y AliExpress sin exigir una fila de score."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.id, p.title, p.current_price, p.image_url,
                       COALESCE(p.affiliate_url, p.product_url) AS product_url,
                       p.currency, COALESCE(s.opportunity_score, 0) AS opportunity_score
                FROM products p
                LEFT JOIN product_scores s ON s.product_id = p.id
                JOIN platforms pl ON pl.id = p.platform_id
                WHERE p.is_active = TRUE AND pl.name IN ('amazon', 'aliexpress')
                ORDER BY COALESCE(s.opportunity_score, 0) DESC, p.updated_at DESC
                LIMIT %s
            """, (limit,))
            products = cur.fetchall()

            # La tienda automática conserva solo productos de Amazon y AliExpress importados por URL.
            cur.execute("""
                UPDATE published_cards pc
                SET is_published = FALSE, updated_at = NOW()
                FROM products p
                JOIN platforms pl ON pl.id = p.platform_id
                WHERE pc.product_id = p.id
                  AND pl.name NOT IN ('amazon', 'aliexpress', 'personal')
                  AND pc.is_published = TRUE
            """)

            for p in products:
                price = p["current_price"]
                currency = p["currency"] or "COP"
                price_display = f"{price:,.0f} {currency}" if price is not None else "Consultar"
                cur.execute("""
                    INSERT INTO published_cards (
                        product_id, title, subtitle, price_display, image_url, product_url,
                        sale_price, cost_price, profit_amount, profit_margin_pct,
                        opportunity_score, footer, accent, is_published, published_at, updated_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,NOW(),NOW())
                    ON CONFLICT (product_id) DO UPDATE SET
                        title=EXCLUDED.title, subtitle=EXCLUDED.subtitle,
                        price_display=EXCLUDED.price_display,
                        image_url=EXCLUDED.image_url, product_url=EXCLUDED.product_url,
                        sale_price=EXCLUDED.sale_price, cost_price=EXCLUDED.cost_price,
                        opportunity_score=EXCLUDED.opportunity_score,
                        is_published=TRUE, published_at=NOW(), updated_at=NOW()
                """, (
                    p["id"], p["title"], "Producto real seleccionado por Central",
                    price_display, p["image_url"], p["product_url"],
                    price, price, 0, 0,
                    p["opportunity_score"], "Disponible en Central", "#b6f23a"
                ))

        conn.commit()
        print(f"== Tienda: {len(products)} productos reales de Amazon/AliExpress publicados ==")
    finally:
        conn.close()


def run_worker():
    print("Esperando a que la base de datos esté lista...")
    time.sleep(5)
    init_database()
    seeded = seed_catalog()
    print(f"== Catálogo inicial: Amazon {seeded.get('amazon', 0)} | AliExpress {seeded.get('aliexpress', 0)} | Propios {seeded.get('personal', 0)} ==")
    run_pipeline()
    schedule.every(2).hours.do(run_pipeline)
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    run_worker()
