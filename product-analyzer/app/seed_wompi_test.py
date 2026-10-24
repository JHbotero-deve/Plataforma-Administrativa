import os
import psycopg2
from psycopg2.extras import Json


def main():
    if os.getenv("CENTRAL_ALLOW_WOMPI_TEST_SEED", "").strip().lower() != "true":
        raise SystemExit("Operación protegida: define CENTRAL_ALLOW_WOMPI_TEST_SEED=true solo en una base de pruebas.")

    DB_URL = os.getenv("DATABASE_URL")
    if not DB_URL:
        raise RuntimeError("DATABASE_URL no configurada")
    
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO platforms(name, base_url) VALUES ('personal', NULL) ON CONFLICT (name) DO NOTHING")
            cur.execute("INSERT INTO categories(name) VALUES ('pruebas') ON CONFLICT (name) DO NOTHING")
            cur.execute("SELECT id FROM platforms WHERE name='personal'")
            platform_id = cur.fetchone()[0]
            cur.execute("SELECT id FROM categories WHERE name='pruebas'")
            category_id = cur.fetchone()[0]
    
            cur.execute("""
                INSERT INTO products (
                    platform_id, category_id, external_id, sku, title, description,
                    image_url, image_gallery, product_url, current_price, previous_price,
                    currency, stock, is_active, catalog_expires_at, updated_at
                )
                VALUES (
                    %s,%s,%s,%s,%s,%s,NULL,%s,%s,1000,NULL,'COP',10,TRUE,NULL,NOW()
                )
                ON CONFLICT(platform_id, external_id) DO UPDATE SET
                    category_id=EXCLUDED.category_id,
                    sku=EXCLUDED.sku,
                    title=EXCLUDED.title,
                    description=EXCLUDED.description,
                    image_url=EXCLUDED.image_url,
                    image_gallery=EXCLUDED.image_gallery,
                    product_url=EXCLUDED.product_url,
                    current_price=1000,
                    previous_price=NULL,
                    currency='COP',
                    stock=10,
                    is_active=TRUE,
                    catalog_expires_at=NULL,
                    updated_at=NOW()
                RETURNING id
            """, (
                platform_id, category_id, "TEST-WOMPI-1000", "TEST-WOMPI-1000",
                "Producto de prueba Wompi — $1.000 COP",
                "Producto temporal para validar el flujo real de pago de Wompi.",
                Json([]), "/producto/test-wompi-1000"
            ))
            product_id = cur.fetchone()[0]
    
            cur.execute("""
                INSERT INTO published_cards (
                    product_id, title, subtitle, price_display, image_url,
                    product_url, opportunity_score, footer, is_published,
                    sort_order, published_at, updated_at, sale_price,
                    cost_price, profit_amount, profit_margin_pct
                )
                VALUES (
                    %s,%s,%s,%s,NULL,%s,0,'Prueba de pago Wompi',TRUE,-1000,NOW(),NOW(),
                    1000,1000,0,0
                )
                ON CONFLICT(product_id) DO UPDATE SET
                    title=EXCLUDED.title,
                    subtitle=EXCLUDED.subtitle,
                    price_display=EXCLUDED.price_display,
                    image_url=NULL,
                    product_url=EXCLUDED.product_url,
                    footer=EXCLUDED.footer,
                    is_published=TRUE,
                    sort_order=-1000,
                    updated_at=NOW(),
                    sale_price=1000,
                    cost_price=1000,
                    profit_amount=0,
                    profit_margin_pct=0
                RETURNING id
            """, (
                product_id,
                "Producto de prueba Wompi — $1.000 COP",
                "Pago de prueba del flujo completo",
                "$1.000 COP",
                "/producto/test-wompi-1000"
            ))
            publication_id = cur.fetchone()[0]
    
            cur.execute(
                "INSERT INTO price_history(product_id, price) SELECT %s,1000 WHERE NOT EXISTS (SELECT 1 FROM price_history WHERE product_id=%s AND price=1000)",
                (product_id, product_id)
            )
        conn.commit()
        print(f"WOMPI_TEST_PRODUCT_READY product_id={product_id} publication_id={publication_id} price=1000 stock=10")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
