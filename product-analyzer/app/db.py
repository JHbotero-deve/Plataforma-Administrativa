import os
import psycopg2
from psycopg2.extras import Json, RealDictCursor


def get_connection():
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        return psycopg2.connect(db_url, cursor_factory=RealDictCursor)
    return psycopg2.connect(host=os.getenv("DB_HOST", "db"), port=os.getenv("DB_PORT", "5432"), dbname=os.getenv("DB_NAME", "productos_db"), user=os.getenv("DB_USER", "productos_user"), password=os.getenv("DB_PASSWORD", "productos_pass"), cursor_factory=RealDictCursor)


def upsert_product(conn, platform_name: str, category_name: str, product: dict):
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM platforms WHERE name = %s", (platform_name,))
        platform = cur.fetchone()
        if not platform:
            raise ValueError(f"Plataforma no registrada: {platform_name}")
        cur.execute("SELECT id FROM categories WHERE name = %s", (category_name,))
        category = cur.fetchone()
        seller_data = product.get("seller") or {}
        seller_id = None
        if seller_data.get("external_id"):
            cur.execute("INSERT INTO sellers (platform_id, external_id, name, reputation) VALUES (%s,%s,%s,%s) ON CONFLICT (platform_id, external_id) DO UPDATE SET name=COALESCE(EXCLUDED.name,sellers.name), reputation=COALESCE(EXCLUDED.reputation,sellers.reputation) RETURNING id", (platform["id"], str(seller_data["external_id"]), seller_data.get("name"), seller_data.get("reputation")))
            seller_id = cur.fetchone()["id"]
        cur.execute("""
            INSERT INTO products (platform_id, category_id, seller_id, external_id, title, image_url, product_url, current_price, currency, rating, reviews_count, sales_estimate, source_metadata, catalog_batch_id, catalog_expires_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW()+INTERVAL '48 hours',NOW())
            ON CONFLICT (platform_id, external_id) DO UPDATE SET
                category_id=EXCLUDED.category_id, seller_id=COALESCE(EXCLUDED.seller_id,products.seller_id),
                title=EXCLUDED.title, image_url=COALESCE(EXCLUDED.image_url,products.image_url),
                product_url=COALESCE(EXCLUDED.product_url,products.product_url), current_price=EXCLUDED.current_price,
                currency=EXCLUDED.currency, rating=COALESCE(EXCLUDED.rating,products.rating),
                reviews_count=COALESCE(EXCLUDED.reviews_count,products.reviews_count),
                sales_estimate=COALESCE(EXCLUDED.sales_estimate,products.sales_estimate),
                source_metadata=EXCLUDED.source_metadata, is_active=TRUE, updated_at=NOW()
            RETURNING id
        """, (platform["id"], category["id"] if category else None, seller_id, product["external_id"], product["title"], product.get("image_url"), product.get("product_url"), product.get("price"), product.get("currency") or "COP", product.get("rating"), product.get("reviews_count",0), product.get("sales_estimate"), Json(product.get("source_metadata") or {})))
        product_id = cur.fetchone()["id"]
        if product.get("price") is not None:
            cur.execute("INSERT INTO price_history (product_id, price) VALUES (%s,%s)", (product_id, product["price"]))
    conn.commit()
    return product_id
