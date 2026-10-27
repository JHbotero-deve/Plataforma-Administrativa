import os
import re

import requests
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from affiliate_links import generate_aliexpress_affiliate_url


def _database_url() -> str:
    db_url = (os.getenv("DATABASE_URL") or "").strip()
    if not db_url:
        raise RuntimeError("DATABASE_URL es obligatoria; Central usa PostgreSQL de Railway como unica base de datos.")

    # psycopg2 expects a PostgreSQL URL, not SQLAlchemy's driver-qualified URL.
    for prefix in ("postgresql+psycopg://", "postgres+psycopg://"):
        if db_url.startswith(prefix):
            db_url = "postgresql://" + db_url[len(prefix):]
            break

    parsed = urlsplit(db_url)
    if not parsed.scheme.startswith("postgres") or not parsed.hostname:
        raise RuntimeError("DATABASE_URL no es una URL PostgreSQL válida. Configura Railway con la referencia ${{Postgres.DATABASE_URL}}.")
    if parsed.hostname.lower() in {"un_host_publico", "localhost_placeholder"} or ":puerto" in db_url:
        raise RuntimeError("DATABASE_URL contiene un host/puerto de ejemplo. Configura la referencia ${{Postgres.DATABASE_URL}} en Railway.")
    return db_url


def get_connection():
    return psycopg2.connect(_database_url(), cursor_factory=RealDictCursor)


def _env_value(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value and value.strip():
        return value.strip()
    target = name.strip().replace("\\u00a0", "").casefold()
    for key, candidate in os.environ.items():
        if key.strip().replace("\\u00a0", "").casefold() == target and candidate and str(candidate).strip():
            return str(candidate).strip()
    return default

def _affiliate_url(product: dict, platform_name: str | None = None) -> str | None:
    url = (product.get("product_url") or "").strip()
    platform = str(platform_name or product.get("platform") or "").strip().lower()
    if not url:
        return None
    if platform == "amazon":
        tag = (_env_value("AMAZON_PARTNER_TAG") or _env_value("AMAZON_ASSOCIATE_TAG")).strip()
        if not tag:
            return None
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        if "tag" in query and query["tag"]:
            return url
        if not re.search(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?:[/?]|$)", parts.path, re.I):
            return None
        query["tag"] = tag
        tracking = _env_value("AMAZON_TRACKING_ID")
        if tracking:
            query["ascsubtag"] = tracking
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    if platform == "aliexpress":
        try:
            return generate_aliexpress_affiliate_url(url)
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            print(f"[aliexpress] enlace afiliado no generado: {type(exc).__name__}")
            return None
    return None
def upsert_product(conn, platform_name: str, category_name: str, product: dict):
    # Generar el enlace fuera del cursor para no mantener una consulta abierta durante la llamada externa.
    affiliate_url = _affiliate_url(product, platform_name)
    product["affiliate_url"] = affiliate_url
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
                %s, %s, %s, %s, %s, %s, %s, NOW(), NOW() + INTERVAL '48 hours', NOW()
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
                affiliate_url,
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
