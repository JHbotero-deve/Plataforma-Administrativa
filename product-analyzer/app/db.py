import os
from urllib.parse import quote

import psycopg2
from psycopg2.extras import Json, RealDictCursor


def get_connection():
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL es obligatoria; Central usa PostgreSQL de Railway como unica base de datos.")
    return psycopg2.connect(db_url, cursor_factory=RealDictCursor)


def _affiliate_url(product: dict) -> str | None:
    url=(product.get("product_url") or "").strip()
    if product.get("platform")!="amazon" or not url: return None
    tag=(os.getenv("AMAZON_PARTNER_TAG") or os.getenv("AMAZON_ASSOCIATE_TAG") or "").strip()
    if not tag: return None
    result=url+("&" if "?" in url else "?")+"tag="+quote(tag,safe="")
    tracking=os.getenv("AMAZON_TRACKING_ID","").strip()
    if tracking: result+="&ascsubtag="+quote(tracking,safe="")
    return result

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
            cur.execute(
                """
                INSERT INTO sellers (platform_id, external_id, name, reputation)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (platform_id, external_id) DO UPDATE SET
                    name = COALESCE(EXCLUDED.name, sellers.name),
                    reputation = COALESCE(EXCLUDED.reputation, sellers.reputation)
                RETURNING id
                """,
                (
                    platform["id"],
                    str(seller_data["external_id"]),
                    seller_data.get("name"),
                    seller_data.get("reputation"),
                ),
            )
            seller_id = cur.fetchone()["id"]

        cur.execute(
            """
            INSERT INTO products (
                platform_id, category_id, seller_id, external_id, title,
                image_url, image_gallery, product_url, affiliate_url, current_price, currency, rating,
                reviews_count, sales_estimate, source_metadata,
                catalog_batch_id, catalog_expires_at, updated_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, NOW(), NOW() + INTERVAL '48 hours', NOW()
            )
            ON CONFLICT (platform_id, external_id) DO UPDATE SET
                category_id = EXCLUDED.category_id,
                seller_id = COALESCE(EXCLUDED.seller_id, products.seller_id),
                title = EXCLUDED.title,
                image_url = COALESCE(EXCLUDED.image_url, products.image_url),
                image_gallery = CASE WHEN EXCLUDED.image_gallery <> '[]'::jsonb THEN EXCLUDED.image_gallery ELSE products.image_gallery END,
                product_url = COALESCE(EXCLUDED.product_url, products.product_url),
                affiliate_url = COALESCE(EXCLUDED.affiliate_url, products.affiliate_url),
                current_price = COALESCE(EXCLUDED.current_price, products.current_price),
                currency = COALESCE(EXCLUDED.currency, products.currency),
                rating = COALESCE(EXCLUDED.rating, products.rating),
                reviews_count = COALESCE(EXCLUDED.reviews_count, products.reviews_count),
                sales_estimate = COALESCE(EXCLUDED.sales_estimate, products.sales_estimate),
                source_metadata = EXCLUDED.source_metadata,
                catalog_batch_id = NOW(),
                catalog_expires_at = NOW() + INTERVAL '48 hours',
                is_active = TRUE,
                updated_at = NOW()
            RETURNING id
            """,
            (
                platform["id"],
                category["id"] if category else None,
                seller_id,
                product["external_id"],
                product["title"],
                product.get("image_url"),
                Json(product.get("gallery_urls") or ([product.get("image_url")] if product.get("image_url") else [])),
                product.get("product_url"),
                _affiliate_url(product),
                product.get("price"),
                product.get("currency") or "COP",
                product.get("rating"),
                product.get("reviews_count", 0),
                product.get("sales_estimate"),
                Json(product.get("source_metadata") or {}),
            ),
        )
        product_id = cur.fetchone()["id"]

        if product.get("price") is not None:
            cur.execute(
                "INSERT INTO price_history (product_id, price) VALUES (%s, %s)",
                (product_id, product["price"]),
            )

    conn.commit()
    return product_id
