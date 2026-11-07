"""Descubre candidatos mediante APIs oficiales y verifica cada URL públicamente."""
import math

from amazon_creators import search_products as search_amazon
from analysis import score_product
from db import get_connection, upsert_product
from product_sources import enrich_product
from promotion_bots import _search_bucket

DISCOVERY_QUERIES = (
    ("tecnologia", "auriculares bluetooth"),
    ("hogar", "organizador cocina"),
    ("deportes", "accesorios fitness"),
    ("belleza", "cuidado personal"),
)


def _limit(value, default=8):
    try:
        return max(1, min(10, int(value)))
    except (TypeError, ValueError):
        return default


def _search_candidates(platform, query, category, limit):
    if platform == "amazon":
        return search_amazon(query, category, limit=limit, page=1) or []
    if platform == "aliexpress":
        return _search_bucket(category, query, page_no=1, review=False, minimum_discount=0)[:limit]
    raise ValueError("Marketplace no admitido.")


def _persist_verified(product, category):
    metadata = product.get("source_metadata") or {}
    if not metadata.get("catalog_refresh_verified") or metadata.get("enrichment_status") != "complete":
        return None
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO categories(name) VALUES(%s) ON CONFLICT(name) DO NOTHING",
                (category,),
            )
        conn.commit()
        product_id = upsert_product(conn, product["platform"], category, product)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT AVG(current_price) AS average FROM products
                   WHERE category_id=(SELECT id FROM categories WHERE name=%s)
                     AND currency=%s AND current_price IS NOT NULL
                     AND current_price>0 AND is_active=TRUE""",
                (category, product.get("currency") or "USD"),
            )
            row = cur.fetchone() or {}
        average = float(row.get("average") or product["price"])
        return product_id, score_product(conn, product_id, average)
    finally:
        conn.close()


def search_and_import(query, limit=8):
    """Busca en ambos marketplaces; solo guarda y puntúa datos verificados por URL."""
    query = " ".join(str(query or "").split())[:120]
    if not query:
        return {"products": [], "errors": ["Escribe un término de búsqueda."]}
    limit = _limit(limit)
    category = "busquedas"
    products = []
    errors = []
    seen = set()
    per_platform = max(1, math.ceil(limit / 2))
    for platform in ("amazon", "aliexpress"):
        try:
            candidates = _search_candidates(platform, query, category, per_platform)
        except Exception as exc:
            errors.append(f"{platform}: {type(exc).__name__}: {str(exc)[:180]}")
            continue
        for candidate in candidates:
            external_id = str(candidate.get("external_id") or "").strip()
            if not external_id or (platform, external_id) in seen:
                continue
            seen.add((platform, external_id))
            candidate = dict(candidate)
            candidate["platform"] = platform
            candidate["category"] = category
            metadata = dict(candidate.get("source_metadata") or {})
            metadata["discovery_query"] = query
            metadata["discovery_provider"] = "amazon_creators_api" if platform == "amazon" else "aliexpress_affiliate_api"
            candidate["source_metadata"] = metadata
            try:
                verified = enrich_product(candidate, force=True)
                verified_metadata = verified.get("source_metadata") or {}
                is_verified = bool(verified_metadata.get("catalog_refresh_verified") and verified_metadata.get("enrichment_status") == "complete")
                saved = _persist_verified(verified, category) if is_verified else None
                products.append({
                    "id": saved[0] if saved else None,
                    "title": verified.get("title") or candidate.get("title"),
                    "current_price": verified.get("price") if verified_metadata.get("catalog_refresh_verified") else None,
                    "currency": verified.get("currency") if verified_metadata.get("catalog_refresh_verified") else None,
                    "product_url": verified.get("product_url") or candidate.get("product_url"),
                    "affiliate_url": verified.get("affiliate_url") or candidate.get("affiliate_url"),
                    "image_url": verified.get("image_url") if verified_metadata.get("catalog_refresh_verified") else None,
                    "platform": platform,
                    "category": category,
                    "opportunity_score": saved[1] if saved else None,
                    "verified": bool(saved and verified_metadata.get("catalog_refresh_verified")),
                    "source_metadata": verified_metadata,
                })
            except Exception as exc:
                errors.append(f"{platform}/{external_id}: {type(exc).__name__}: {str(exc)[:180]}")
            if len(products) >= limit:
                return {"products": products, "errors": errors}
    return {"products": products, "errors": errors}


def run_marketplace_ingestion(target_per_platform=8):
    """Ciclo de descubrimiento independiente: una API fallida no detiene la otra."""
    target = _limit(target_per_platform)
    per_query = max(1, math.ceil(target / len(DISCOVERY_QUERIES)))
    results = {}
    for platform in ("amazon", "aliexpress"):
        received = 0
        verified = 0
        pending = 0
        errors = []
        for category, query in DISCOVERY_QUERIES:
            try:
                candidates = _search_candidates(platform, query, category, min(10, per_query))
            except Exception as exc:
                errors.append(f"{category}: {type(exc).__name__}: {str(exc)[:160]}")
                continue
            received += len(candidates)
            for candidate in candidates:
                candidate = dict(candidate)
                candidate["platform"] = platform
                candidate["category"] = category
                try:
                    enriched = enrich_product(candidate, force=True)
                    metadata = enriched.get("source_metadata") or {}
                    is_verified = bool(metadata.get("catalog_refresh_verified") and metadata.get("enrichment_status") == "complete")
                    saved = _persist_verified(enriched, category) if is_verified else None
                    if saved:
                        verified += 1
                    else:
                        pending += 1
                except Exception as exc:
                    errors.append(f"{category}/{candidate.get('external_id')}: {type(exc).__name__}: {str(exc)[:160]}")
        results[platform] = {
            "received": received,
            "verified_and_saved": verified,
            "pending_public_verification": pending,
            "errors": errors[:8],
            "status": "completed" if received else ("error" if errors else "empty"),
        }
    print("[marketplace-discovery] " + " | ".join(
        f"{platform}: recibidos={item['received']} verificados={item['verified_and_saved']} "
        f"pendientes={item['pending_public_verification']} estado={item['status']}"
        for platform, item in results.items()
    ))
    return results
