"""Ingesta recurrente e independiente de Amazon y AliExpress."""
import os

from amazon_creators import search_products as search_amazon
from analysis import score_product
from db import get_connection, upsert_product
from notifications import send_telegram_publication
from pricing import build_customer_offer, publish_discounted_offer
from promotion_bots import _search_bucket

AMAZON_QUERY = "best selling popular products technology home kitchen fashion sports beauty"
ALIEXPRESS_QUERY = "productos populares tecnologia hogar cocina moda deportes belleza accesorios"
SEARCH_CATEGORY = "busquedas"
DEFAULT_TARGET = 100
AMAZON_PAGE_SIZE = 10
ALIEXPRESS_PAGE_SIZE = 50
SCORE_THRESHOLD = float(os.getenv("SCORE_THRESHOLD", "50"))


def _unique_products(products, target):
    unique = {}
    for product in products:
        external_id = str(product.get("external_id") or "").strip()
        if external_id and external_id not in unique:
            unique[external_id] = product
        if len(unique) >= target:
            break
    return list(unique.values())


def _search_amazon_candidates(target):
    candidates = []
    pages = 0
    for page in range(1, 11):
        batch = search_amazon(AMAZON_QUERY, SEARCH_CATEGORY, limit=AMAZON_PAGE_SIZE, page=page)
        pages += 1
        candidates.extend(batch or [])
        unique = _unique_products(candidates, target)
        if len(unique) >= target or len(batch or []) < AMAZON_PAGE_SIZE:
            break
    return _unique_products(candidates, target), pages


def _search_aliexpress_candidates(target):
    candidates = []
    pages = 0
    for page in range(1, 3):
        batch = _search_bucket(
            SEARCH_CATEGORY,
            ALIEXPRESS_QUERY,
            page_no=page,
            review=False,
            minimum_discount=0,
        )
        pages += 1
        candidates.extend(batch or [])
        unique = _unique_products(candidates, target)
        if len(unique) >= target or len(batch or []) < ALIEXPRESS_PAGE_SIZE:
            break
    return _unique_products(candidates, target), pages


def _persist_candidates(platform, candidates):
    conn = get_connection()
    saved = 0
    published = 0
    telegram_sent = 0
    errors = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO categories(name) VALUES(%s) ON CONFLICT(name) DO NOTHING",
                (SEARCH_CATEGORY,),
            )
        conn.commit()
        for product in candidates:
            try:
                product_id = upsert_product(conn, platform, SEARCH_CATEGORY, product)
                saved += 1
                price = product.get("price")
                previous_price = product.get("previous_price")
                score = score_product(conn, product_id, float(price or 0))
                offer = build_customer_offer(price, previous_price)
                if not offer or score < SCORE_THRESHOLD:
                    continue
                published_offer = publish_discounted_offer(
                    conn,
                    product_id,
                    {
                        "title": product.get("title"),
                        "current_price": price,
                        "previous_price": previous_price,
                        "currency": product.get("currency"),
                        "image_url": product.get("image_url"),
                        "product_url": product.get("affiliate_url") or product.get("product_url"),
                    },
                    score,
                )
                if not published_offer:
                    continue
                published += 1
                metadata = product.get("source_metadata") or {}
                seller = product.get("seller") or {}
                base = os.getenv("PUBLIC_STORE_URL", "https://central-7ykr.vercel.app").rstrip("/")
                try:
                    send_telegram_publication(
                        {
                            "title": product.get("title"),
                            "subtitle": f"{published_offer['discount_pct']:.0f}% de descuento",
                            "description": product.get("description") or product.get("title"),
                            "price_display": f"{published_offer['sale_price']:,.2f} {product.get('currency') or 'USD'}",
                            "image_url": product.get("image_url"),
                            "seller_name": seller.get("name") or metadata.get("seller_name"),
                            "seller_url": metadata.get("seller_url"),
                            "store_url": f"{base}/tienda",
                            "product_url": product.get("affiliate_url") or product.get("product_url"),
                            "platform": platform,
                            "category": product.get("category") or SEARCH_CATEGORY,
                            "rating": product.get("rating"),
                            "reviews_count": product.get("reviews_count"),
                            "sales_estimate": product.get("sales_estimate"),
                            "discount": published_offer.get("discount_pct"),
                        }
                    )
                    telegram_sent += 1
                except Exception as exc:
                    errors.append(f"Telegram {product_id}: {type(exc).__name__}: {str(exc)[:160]}")
            except Exception as exc:
                errors.append(f"{platform} {product.get('external_id')}: {type(exc).__name__}: {str(exc)[:160]}")
        return {
            "received": len(candidates),
            "saved": saved,
            "published": published,
            "telegram_sent": telegram_sent,
            "errors": errors[:20],
        }
    finally:
        conn.close()


def run_marketplace_ingestion(target_per_platform=DEFAULT_TARGET):
    """Search up to 100 candidates per marketplace without depending on Telegram."""
    target = max(1, min(int(target_per_platform), 100))
    results = {}
    for platform, search in (
        ("amazon", _search_amazon_candidates),
        ("aliexpress", _search_aliexpress_candidates),
    ):
        try:
            candidates, pages = search(target)
            persisted = _persist_candidates(platform, candidates) if candidates else {
                "received": 0, "saved": 0, "published": 0, "telegram_sent": 0, "errors": []
            }
            results[platform] = {
                "requested": target,
                "pages": pages,
                **persisted,
                "status": "completed" if len(candidates) >= target else "partial",
            }
        except Exception as exc:
            results[platform] = {
                "requested": target,
                "received": 0,
                "saved": 0,
                "published": 0,
                "telegram_sent": 0,
                "status": "error",
                "error": f"{type(exc).__name__}: {str(exc)[:240]}",
            }
    print(
        "[marketplaces] "
        + " | ".join(
            f"{platform}: solicitados={data['requested']} recibidos={data['received']} "
            f"guardados={data['saved']} publicados={data['published']} "
            f"telegram={data['telegram_sent']} estado={data['status']}"
            for platform, data in results.items()
        )
    )
    return results
