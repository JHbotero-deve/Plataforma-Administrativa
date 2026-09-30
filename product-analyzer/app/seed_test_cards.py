"""One-shot seed for Central Studio card testing. Idempotent by TEST-CARD-* SKU."""
from db import get_connection
from analysis import score_product
from psycopg2.extras import Json

PRODUCTS = [
    {
        "sku": "TEST-CARD-001",
        "title": "TEST Central Air Max Urban",
        "description": "Producto de prueba para validar tarjetas, precios, imágenes y publicación.",
        "price": 329900,
        "previous_price": 399900,
        "category": "calzado",
        "stock": 12,
        "image_url": "https://images.unsplash.com/photo-1542291026-7eec264c27ff?auto=format&fit=crop&w=900&q=80",
    },
    {
        "sku": "TEST-CARD-002",
        "title": "TEST Central Headphones Pro",
        "description": "Producto de prueba para validar Studio, imagen de producto y tarjeta 3D.",
        "price": 459900,
        "previous_price": 549900,
        "category": "audio",
        "stock": 8,
        "image_url": "https://images.unsplash.com/photo-1505740420928-5e560c06d30e?auto=format&fit=crop&w=900&q=80",
    },
    {
        "sku": "TEST-CARD-003",
        "title": "TEST Central Smartwatch X",
        "description": "Producto de prueba para validar score, precio y composición visual.",
        "price": 279900,
        "previous_price": 349900,
        "category": "relojes",
        "stock": 15,
        "image_url": "https://images.unsplash.com/photo-1523275335684-37898b6baf30?auto=format&fit=crop&w=900&q=80",
    },
    {
        "sku": "TEST-CARD-004",
        "title": "TEST Central Backpack Tech",
        "description": "Producto de prueba para validar tarjeta, catálogo y publicación pública.",
        "price": 189900,
        "previous_price": 229900,
        "category": "accesorios",
        "stock": 20,
        "image_url": "https://images.unsplash.com/photo-1553062407-98eeb64c6a62?auto=format&fit=crop&w=900&q=80",
    },
]

def main():
    conn = get_connection()
    ids = []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM platforms WHERE name='personal'")
            platform = cur.fetchone()
            if not platform:
                raise RuntimeError("platform personal no existe")
            for item in PRODUCTS:
                cur.execute(
                    "INSERT INTO categories(name) VALUES(%s) ON CONFLICT(name) DO NOTHING",
                    (item["category"],),
                )
                cur.execute("SELECT id FROM categories WHERE name=%s", (item["category"],))
                category = cur.fetchone()
                cur.execute(
                    """
                    INSERT INTO products (
                        platform_id, category_id, external_id, sku, title, description,
                        image_url, image_gallery, product_url, current_price, previous_price,
                        currency, stock, is_active, catalog_expires_at, updated_at
                    ) VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'COP',%s,TRUE,NULL,NOW()
                    )
                    ON CONFLICT(platform_id, external_id) DO UPDATE SET
                        category_id=EXCLUDED.category_id, sku=EXCLUDED.sku,
                        title=EXCLUDED.title, description=EXCLUDED.description,
                        image_url=EXCLUDED.image_url, image_gallery=EXCLUDED.image_gallery,
                        current_price=EXCLUDED.current_price, previous_price=EXCLUDED.previous_price,
                        currency='COP', stock=EXCLUDED.stock, is_active=TRUE,
                        catalog_expires_at=NULL, updated_at=NOW()
                    RETURNING id
                    """,
                    (
                        platform["id"], category["id"], item["sku"], item["sku"], item["title"],
                        item["description"], item["image_url"], Json([item["image_url"]]),
                        None, item["price"], item["previous_price"], item["stock"],
                    ),
                )
                product_id = cur.fetchone()["id"]
                cur.execute(
                    "INSERT INTO price_history(product_id,price) VALUES(%s,%s)",
                    (product_id, item["price"]),
                )
                ids.append(product_id)
        conn.commit()
        scores=[]
        for product_id in ids:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT AVG(current_price) AS avg_price FROM products WHERE platform_id=(SELECT id FROM platforms WHERE name='personal') AND is_active=TRUE AND current_price IS NOT NULL"
                )
                avg=float(cur.fetchone()["avg_price"] or 0)
            scores.append((product_id, score_product(conn, product_id, avg)))
        print({"seeded_product_ids": ids, "scores": scores})
    finally:
        conn.close()

if __name__ == "__main__":
    main()
