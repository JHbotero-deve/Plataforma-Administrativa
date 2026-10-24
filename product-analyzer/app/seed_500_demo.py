"""
Carga 500 productos DEMO para probar el storefront de Central.

Seguridad:
- Solo usa external_id con prefijo DEMO-500-.
- No modifica productos reales.
- Puede revertirse con --delete.
- No depende de APIs externas ni de credenciales de marketplaces.
"""

import argparse
import os
import psycopg2
from psycopg2.extras import Json

COUNT = 500
PREFIX = "DEMO-500-"

CATEGORIES = [
    "accesorios",
    "electronica",
    "hogar",
    "ropa",
    "calzado",
    "fitness",
    "otros",
]

IMAGE_URLS = [
    "https://images.unsplash.com/photo-1505740420928-5e560c06d30e",
    "https://images.unsplash.com/photo-1523275335684-37898b6baf30",
    "https://images.unsplash.com/photo-1542291026-7eec264c27ff",
    "https://images.unsplash.com/photo-1525966222134-fcfa99b8ae77",
    "https://images.unsplash.com/photo-1556228578-8c89e6adf883",
    "https://images.unsplash.com/photo-1511707171634-5f897ff02aa9",
    "https://images.unsplash.com/photo-1495474472287-4d71bcdd2085",
    "https://images.unsplash.com/photo-1441986300917-64674bd600d8",
]

NAMES = [
    "Audifonos Bluetooth Pro",
    "Smartwatch Active",
    "Teclado Mecanico RGB",
    "Mouse Gamer Inalambrico",
    "Lampara LED Inteligente",
    "Organizador Multiuso",
    "Zapatillas Urbanas",
    "Mochila Ejecutiva",
    "Cargador Rapido USB-C",
    "Soporte Ajustable",
]

def connect():
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("DATABASE_URL es obligatoria")
    return psycopg2.connect(url)

def seed(conn):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO platforms(name, base_url)
            VALUES ('personal', NULL)
            ON CONFLICT(name) DO NOTHING
        """)
        for category in CATEGORIES:
            cur.execute(
                "INSERT INTO categories(name) VALUES(%s) ON CONFLICT(name) DO NOTHING",
                (category,),
            )

        cur.execute("SELECT id FROM platforms WHERE name='personal'")
        platform_id = cur.fetchone()[0]

        cur.execute(
            "SELECT name, id FROM categories WHERE name = ANY(%s)",
            (CATEGORIES,),
        )
        category_ids = {row[0]: row[1] for row in cur.fetchall()}

        for i in range(1, COUNT + 1):
            external_id = f"{PREFIX}{i:03d}"
            category = CATEGORIES[(i - 1) % len(CATEGORIES)]
            title = f"{NAMES[(i - 1) % len(NAMES)]} #{i:03d}"
            current_price = 29000 + ((i * 1379) % 420000)
            previous_price = int(round(current_price * 1.25 / 1000) * 1000)
            image_url = IMAGE_URLS[(i - 1) % len(IMAGE_URLS)]
            sku = external_id
            product_url = f"/producto/{external_id.lower()}"

            cur.execute("""
                INSERT INTO products (
                    platform_id, category_id, external_id, sku, title, description,
                    image_url, image_gallery, product_url,
                    current_price, previous_price, currency, stock,
                    is_active, is_blocked, catalog_expires_at, source_metadata, updated_at
                )
                VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,'COP',50,TRUE,FALSE,NULL,%s,NOW()
                )
                ON CONFLICT(platform_id, external_id) DO UPDATE SET
                    category_id=EXCLUDED.category_id,
                    sku=EXCLUDED.sku,
                    title=EXCLUDED.title,
                    description=EXCLUDED.description,
                    image_url=EXCLUDED.image_url,
                    image_gallery=EXCLUDED.image_gallery,
                    product_url=EXCLUDED.product_url,
                    current_price=EXCLUDED.current_price,
                    previous_price=EXCLUDED.previous_price,
                    currency='COP',
                    stock=50,
                    is_active=TRUE,
                    is_blocked=FALSE,
                    catalog_expires_at=NULL,
                    source_metadata=EXCLUDED.source_metadata,
                    updated_at=NOW()
                RETURNING id
            """, (
                platform_id,
                category_ids[category],
                external_id,
                sku,
                title,
                "Producto DEMO para pruebas del catalogo de Central.",
                image_url,
                Json([image_url]),
                product_url,
                current_price,
                previous_price,
                Json({"seed": "demo_500", "demo": True, "batch": "2026-10"}),
            ))
            product_id = cur.fetchone()[0]

            cur.execute("""
                INSERT INTO published_cards (
                    product_id, title, subtitle, price_display, image_url,
                    product_url, opportunity_score, footer, is_published,
                    sort_order, published_at, updated_at,
                    sale_price, cost_price, profit_amount, profit_margin_pct
                )
                VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,TRUE,%s,NOW(),NOW(),
                    %s,%s,%s,%s
                )
                ON CONFLICT(product_id) DO UPDATE SET
                    title=EXCLUDED.title,
                    subtitle=EXCLUDED.subtitle,
                    price_display=EXCLUDED.price_display,
                    image_url=EXCLUDED.image_url,
                    product_url=EXCLUDED.product_url,
                    opportunity_score=EXCLUDED.opportunity_score,
                    footer=EXCLUDED.footer,
                    is_published=TRUE,
                    sort_order=EXCLUDED.sort_order,
                    updated_at=NOW(),
                    sale_price=EXCLUDED.sale_price,
                    cost_price=EXCLUDED.cost_price,
                    profit_amount=EXCLUDED.profit_amount,
                    profit_margin_pct=EXCLUDED.profit_margin_pct
            """, (
                product_id,
                title,
                f"{category.title()} · producto DEMO",
                f"$ {current_price:,.0f} COP",
                image_url,
                product_url,
                70 + (i % 30),
                "Producto DEMO · Central",
                100000 + i,
                current_price,
                int(current_price * 0.70),
                int(current_price * 0.30),
                30.0,
            ))

            if i % 100 == 0:
                print(f"Insertados {i}/{COUNT} productos DEMO")

    conn.commit()

def delete_demo(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id
            FROM products
            WHERE external_id LIKE %s
        """, (PREFIX + "%",))
        ids = [row[0] for row in cur.fetchall()]

        if not ids:
            conn.commit()
            print("No hay productos DEMO-500 para eliminar.")
            return

        cur.execute(
            "DELETE FROM published_cards WHERE product_id = ANY(%s)",
            (ids,),
        )
        cur.execute(
            "DELETE FROM products WHERE id = ANY(%s)",
            (ids,),
        )
        conn.commit()
        print(f"Eliminados {len(ids)} productos DEMO y sus publicaciones.")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Elimina exclusivamente los productos external_id DEMO-500-*",
    )
    args = parser.parse_args()
    if os.getenv("CENTRAL_ALLOW_DEMO_SEED", "").strip().lower() != "true":
        parser.error("Operación protegida: define CENTRAL_ALLOW_DEMO_SEED=true solo en una base de pruebas.")

    conn = connect()
    try:
        if args.delete:
            delete_demo(conn)
        else:
            seed(conn)
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) AS total
                    FROM products
                    WHERE external_id LIKE %s
                      AND is_active = TRUE
                """, (PREFIX + "%",))
                total = cur.fetchone()[0]
            print(f"DEMO_500_READY total_activos={total}")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    main()
