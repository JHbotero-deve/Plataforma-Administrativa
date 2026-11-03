"""Búsquedas puntuales solicitadas desde Telegram y publicación automática."""
import os
import re
import unicodedata

from amazon_creators import search_products as search_amazon
from analysis import score_product
from db import get_connection, upsert_product
from notifications import send_telegram_publication
from pricing import build_customer_offer, publish_discounted_offer
from promotion_bots import _search_bucket

SEARCH_CATEGORY = "busquedas"
MAX_PUBLICATIONS_PER_SEARCH = 5


def _store_product_url(title, product_id):
    slug = unicodedata.normalize("NFKD", str(title or "")).encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")[:90] or "producto"
    base = os.getenv("PUBLIC_STORE_URL", "https://central-7ykr.vercel.app").strip().rstrip("/")
    return f"{base}/producto/{slug}-{product_id}"


def search_and_publish(query):
    conn = get_connection()
    errors = []
    candidates = []
    published = 0
    telegram_sent = 0
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO categories(name) VALUES(%s) ON CONFLICT(name) DO NOTHING", (SEARCH_CATEGORY,))
        conn.commit()
        try:
            candidates.extend(search_amazon(query, SEARCH_CATEGORY, limit=10, page=1))
        except Exception as exc:
            errors.append(f"Amazon: {type(exc).__name__}: {str(exc)[:180]}")
        try:
            tracking_id = (os.getenv("ALIEXPRESS_TRACKING_ID") or os.getenv("ALIEXPRESS_AFFILIATE_TRACKING_ID") or "").strip()
            if not (os.getenv("ALIEXPRESS_APP_KEY", "").strip() and os.getenv("ALIEXPRESS_APP_SECRET", "").strip() and tracking_id):
                raise RuntimeError("Faltan credenciales oficiales de búsqueda AliExpress.")
            candidates.extend(_search_bucket(SEARCH_CATEGORY, query, 1, review=False)[:10])
        except Exception as exc:
            errors.append(f"AliExpress: {type(exc).__name__}: {str(exc)[:180]}")
        saved = 0
        seen = set()
        for product in candidates:
            if published >= MAX_PUBLICATIONS_PER_SEARCH:
                break
            key = (product.get("platform"), product.get("external_id"))
            if key in seen:
                continue
            seen.add(key)
            product_id = upsert_product(conn, product["platform"], SEARCH_CATEGORY, product)
            saved += 1
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT AVG(p.current_price) AS average
                       FROM products p JOIN categories c ON c.id=p.category_id
                       WHERE c.name=%s AND p.currency=%s AND p.current_price>0
                         AND p.is_active=TRUE""",
                    (SEARCH_CATEGORY, product.get("currency") or "USD"),
                )
                average = float((cur.fetchone() or {}).get("average") or product["price"])
            score = score_product(conn, product_id, average)
            if not build_customer_offer(product.get("price"), product.get("previous_price")):
                continue
            offer = publish_discounted_offer(conn, product_id, {
                "title": product["title"],
                "current_price": product["price"],
                "previous_price": product.get("previous_price"),
                "currency": product.get("currency"),
                "image_url": product["image_url"],
                "product_url": product.get("affiliate_url") or product.get("product_url"),
            }, score)
            if not offer:
                continue
            published += 1
            seller = product.get("seller") or {}
            metadata = product.get("source_metadata") or {}
            seller_name = seller.get("name") or metadata.get("seller_name") or "Vendedor no informado"
            currency = str(product.get("currency") or "USD").upper()
            try:
                send_telegram_publication({
                    "title": product["title"],
                    "subtitle": f"{float(offer.get('discount_pct') or 40):.0f}% de descuento",
                    "description": product.get("description") or product["title"],
                    "price_display": f"{float(offer['sale_price']):,.2f} {currency}",
                    "image_url": product.get("image_url"),
                    "seller_name": seller_name,
                    "seller_url": metadata.get("seller_url"),
                    "store_url": _store_product_url(product["title"], product_id),
                    "product_url": product.get("affiliate_url") or product.get("product_url"),
                })
                telegram_sent += 1
            except Exception as exc:
                errors.append(f"Telegram: {type(exc).__name__}: {str(exc)[:180]}")
        return {
            "status": "completed" if candidates or not errors else "failed",
            "found": saved,
            "published": published,
            "telegram_sent": telegram_sent,
            "errors": errors,
        }
    finally:
        conn.close()


def process_marketplace_search_requests(limit=2):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE marketplace_search_requests
                   SET status='pending'
                   WHERE status='processing'
                     AND created_at < NOW() - INTERVAL '15 minutes'"""
            )
            cur.execute(
                """SELECT id, query
                   FROM marketplace_search_requests
                   WHERE status='pending'
                   ORDER BY created_at
                   LIMIT %s
                   FOR UPDATE SKIP LOCKED""",
                (max(1, min(int(limit), 5)),),
            )
            requests = cur.fetchall()
            for request in requests:
                cur.execute("UPDATE marketplace_search_requests SET status='processing' WHERE id=%s", (request["id"],))
        conn.commit()
    finally:
        conn.close()
    processed = 0
    for request in requests:
        result = None
        error = None
        status = "completed"
        try:
            result = search_and_publish(request["query"])
            if result.get("errors"):
                error = "; ".join(result["errors"])[:500]
            if result["status"] == "failed":
                status = "failed"
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:400]}"
            status = "failed"
        update = get_connection()
        try:
            with update.cursor() as cur:
                cur.execute(
                    """UPDATE marketplace_search_requests
                       SET status=%s, result_count=%s, published_count=%s,
                           error=%s, processed_at=NOW()
                       WHERE id=%s""",
                    (
                        status,
                        int((result or {}).get("found") or 0),
                        int((result or {}).get("published") or 0),
                        error,
                        request["id"],
                    ),
                )
            update.commit()
            processed += 1
        except Exception:
            update.rollback()
            raise
        finally:
            update.close()
        print(
            f"[marketplace-search] solicitud={request['id']} "
            f"encontrados={int((result or {}).get('found') or 0)} "
            f"publicados={int((result or {}).get('published') or 0)} "
            f"telegram={int((result or {}).get('telegram_sent') or 0)} "
            f"estado={status} error={error or 'ninguno'}"
        )
    return processed
