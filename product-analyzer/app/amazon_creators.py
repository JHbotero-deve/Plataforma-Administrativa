"""Búsqueda de catálogo mediante Amazon Creators API oficial."""
import os
import re
import time
from urllib.parse import urlsplit

import requests

from analysis import score_product
from db import get_connection, upsert_product
from pricing import build_customer_offer, publish_discounted_offer
from storefront_sections import ensure_storefront_section_storage, get_refill_sections

API_URL = "https://creatorsapi.amazon"
TOKEN_CACHE = {"value": None, "expires_at": 0, "version": None}
TOKEN_ENDPOINTS = {
    "3.1": "https://api.amazon.com/auth/o2/token",
    "3.2": "https://api.amazon.co.uk/auth/o2/token",
    "3.3": "https://api.amazon.co.jp/auth/o2/token",
}
RESOURCES = [
    "images.primary.large",
    "images.variants.large",
    "itemInfo.title",
    "itemInfo.features",
    "itemInfo.byLineInfo",
    "itemInfo.classifications",
    "offersV2.listings.price",
    "offersV2.listings.merchantInfo",
]


def _env(name, default=""):
    target = name.replace("\u00a0", "").strip().casefold()
    for key, value in os.environ.items():
        if key.replace("\u00a0", "").strip().casefold() == target and value and str(value).strip():
            return str(value).strip()
    return default


def _configuration():
    version = _env("AMAZON_CREDENTIAL_VERSION", "3.1")
    endpoint = TOKEN_ENDPOINTS.get(version)
    credential_id = _env("AMAZON_CREDENTIAL_ID")
    credential_secret = _env("AMAZON_CREDENTIAL_SECRET")
    partner_tag = _env("AMAZON_PARTNER_TAG") or _env("AMAZON_ASSOCIATE_TAG")
    marketplace = _env("AMAZON_MARKETPLACE", "www.amazon.com").strip().lower()
    marketplace = marketplace.removeprefix("https://").removeprefix("http://").strip("/")
    if not endpoint or not credential_id or not credential_secret or not partner_tag:
        raise RuntimeError("Amazon Creators API requiere credenciales versión 3.1/3.2/3.3 y Partner Tag.")
    if marketplace not in {"www.amazon.com", "amazon.com", "www.amazon.ca", "amazon.ca", "www.amazon.com.mx", "www.amazon.co.uk", "www.amazon.de", "www.amazon.es", "www.amazon.fr", "www.amazon.it", "www.amazon.co.jp", "www.amazon.com.au", "www.amazon.in", "www.amazon.com.br", "www.amazon.nl", "www.amazon.se", "www.amazon.pl", "www.amazon.ae", "www.amazon.sg", "www.amazon.com.tr"}:
        raise RuntimeError("AMAZON_MARKETPLACE no pertenece a un dominio Amazon admitido.")
    return version, endpoint, credential_id, credential_secret, partner_tag, marketplace


def _access_token(config):
    version, endpoint, credential_id, credential_secret, _, _ = config
    if TOKEN_CACHE["value"] and TOKEN_CACHE["version"] == version and time.time() < TOKEN_CACHE["expires_at"] - 120:
        return TOKEN_CACHE["value"]
    response = requests.post(
        endpoint,
        headers={"Content-Type": "application/json"},
        json={
            "grant_type": "client_credentials",
            "client_id": credential_id,
            "client_secret": credential_secret,
            "scope": "creatorsapi::default",
        },
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(f"Amazon OAuth rechazó la autenticación (HTTP {response.status_code}).")
    payload = response.json()
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not token:
        raise RuntimeError("Amazon OAuth no devolvió un token de acceso.")
    TOKEN_CACHE.update({
        "value": token,
        "version": version,
        "expires_at": time.time() + int(payload.get("expires_in") or 3600),
    })
    return token


def _api_search(config, keywords, limit, page=1):
    version, _, _, _, partner_tag, marketplace = config
    payload = {
        "keywords": keywords,
        "searchIndex": "All",
        "itemCount": min(max(int(limit), 1), 10),
        "itemPage": min(max(int(page), 1), 10),
        "sortBy": "Relevance",
        "partnerTag": partner_tag,
        "marketplace": marketplace,
        "resources": RESOURCES,
    }

    def request():
        return requests.post(
            f"{API_URL}/catalog/v1/searchItems",
            headers={
                "Authorization": f"Bearer {_access_token(config)}",
                "Content-Type": "application/json",
                "x-marketplace": marketplace,
            },
            json=payload,
            timeout=30,
        )

    response = request()
    if response.status_code == 401:
        TOKEN_CACHE.update({"value": None, "expires_at": 0, "version": None})
        response = request()
    if not response.ok:
        raise RuntimeError(f"Amazon Creators API SearchItems respondió HTTP {response.status_code}.")
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Amazon Creators API devolvió una respuesta inválida.")
    errors = data.get("errors") or []
    if errors and not (data.get("searchResult") or {}).get("items"):
        first = errors[0] if isinstance(errors, list) and errors else {}
        code = first.get("code") if isinstance(first, dict) else None
        raise RuntimeError(f"Amazon Creators API rechazó SearchItems ({code or 'error sin código'}).")
    result = data.get("searchResult") or {}
    items = result.get("items") or []
    if not isinstance(items, list):
        raise RuntimeError("Amazon Creators API devolvió una lista de artículos inválida.")
    return items


def _display_value(value):
    if isinstance(value, dict):
        return value.get("displayValue") or value.get("value")
    return value


def _amount(value):
    if not isinstance(value, dict):
        return None
    money = value.get("money") or value
    raw = money.get("amount") if isinstance(money, dict) else None
    try:
        amount = float(raw)
        return amount if amount > 0 else None
    except (TypeError, ValueError):
        return None


def _safe_url(value, image=False):
    try:
        parsed = urlsplit(str(value or "").strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme != "https" or not host or parsed.username or parsed.password or port not in {None, 443}:
        return None
    if image:
        allowed = host in {"m.media-amazon.com", "images-na.ssl-images-amazon.com", "images-eu.ssl-images-amazon.com"}
    else:
        allowed = host == "amazon.com" or host.endswith(".amazon.com") or any(
            host == domain or host.endswith("." + domain)
            for domain in ("amazon.ca", "amazon.com.mx", "amazon.co.uk", "amazon.de", "amazon.es", "amazon.fr", "amazon.it", "amazon.co.jp", "amazon.com.au", "amazon.in", "amazon.com.br", "amazon.nl", "amazon.se", "amazon.pl", "amazon.ae", "amazon.sg", "amazon.com.tr")
        )
    return parsed.geturl() if allowed else None


def normalize_item(item, category, marketplace="www.amazon.com"):
    if not isinstance(item, dict):
        return None
    asin = str(item.get("asin") or "").strip().upper()
    info = item.get("itemInfo") or {}
    title = str(_display_value(info.get("title")) or "").strip()
    url = _safe_url(item.get("detailPageURL"))
    images = item.get("images") or {}
    primary = images.get("primary") or {}
    image = _safe_url((primary.get("large") or {}).get("url"), image=True)
    gallery = [image] if image else []
    variants = images.get("variants") or []
    if isinstance(variants, list):
        for variant in variants:
            if isinstance(variant, dict):
                candidate = _safe_url(((variant.get("large") or {}).get("url")), image=True)
                if candidate and candidate not in gallery:
                    gallery.append(candidate)
    listings = ((item.get("offersV2") or {}).get("listings") or [])
    listing = listings[0] if listings and isinstance(listings[0], dict) else {}
    merchant = listing.get("merchantInfo") or {}
    seller_name = str(merchant.get("name") or "").strip()
    seller_id = str(merchant.get("id") or seller_name).strip()
    seller = {"external_id": seller_id, "name": seller_name} if seller_name and seller_id else None
    price_data = listing.get("price") or {}
    current_price = _amount(price_data)
    saving_basis = price_data.get("savingBasis") or {}
    previous_price = _amount(saving_basis)
    money = price_data.get("money") or {}
    saving_money = saving_basis.get("money") or {}
    currency = str(money.get("currency") or saving_money.get("currency") or "USD").upper()
    features = info.get("features") or {}
    feature_values = features.get("displayValues") or features.get("displayValue") or []
    if isinstance(feature_values, str):
        feature_values = [feature_values]
    brand = _display_value((info.get("byLineInfo") or {}).get("brand"))
    description_parts = ([f"Marca: {brand}"] if brand else []) + [
        str(value).strip() for value in feature_values if value
    ]
    description = " · ".join(description_parts)[:5000]
    if not asin or not title or not url or not image or current_price is None:
        return None
    return {
        "platform": "amazon",
        "external_id": asin,
        "title": title[:500],
        "description": description or None,
        "image_url": image,
        "gallery_urls": gallery[:10],
        "product_url": url,
        "price": current_price,
        "previous_price": previous_price if previous_price and previous_price > current_price else None,
        "currency": currency[:10],
        "rating": None,
        "reviews_count": 0,
        "sales_estimate": None,
        "category": category,
        "seller": seller,
        "source_metadata": {
            "import_method": "amazon_creators_api",
            "metadata_source": "amazon_creators_api",
            "marketplace": marketplace,
            "search_query": category,
            "brand": str(brand)[:200] if brand else None,
            "seller_name": seller_name[:200] if seller_name else None,
            "seller_id": seller_id[:200] if seller_id else None,
            "list_price_source": saving_basis.get("savingBasisType") if isinstance(saving_basis, dict) else None,
        },
    }


def search_products(keywords, category, limit=10, page=1):
    config = _configuration()
    return [
        product
        for item in _api_search(config, keywords, limit, page)
        if (product := normalize_item(item, category, config[5]))
    ]


def _count_active(conn, category_names):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT COUNT(*) AS total
               FROM products p
               JOIN platforms pl ON pl.id=p.platform_id
               JOIN categories c ON c.id=p.category_id
               WHERE p.is_active=TRUE AND pl.name='amazon'
                 AND c.name=ANY(%s)""",
            (category_names,),
        )
        return int((cur.fetchone() or {}).get("total") or 0)


def _count_published(conn, category_names):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT COUNT(*) AS total
               FROM published_cards pc
               JOIN products p ON p.id=pc.product_id
               JOIN platforms pl ON pl.id=p.platform_id
               JOIN categories c ON c.id=p.category_id
               WHERE pc.is_published=TRUE AND p.is_active=TRUE
                 AND pl.name='amazon' AND c.name=ANY(%s)""",
            (category_names,),
        )
        return int((cur.fetchone() or {}).get("total") or 0)


def run_amazon_storefront_refill():
    """Busca, modela y publica ofertas Amazon válidas directamente en la tienda."""
    try:
        config = _configuration()
    except RuntimeError as exc:
        return {"status": "disabled", "reason": str(exc), "published": 0, "sections": {}}
    ensure_storefront_section_storage()
    conn = get_connection()
    results = {}
    total_published = 0
    seen = set()
    try:
        for section in get_refill_sections():
            slug = section["slug"]
            category = section["category_name"]
            target = int(section["target_count"])
            current = _count_published(conn, section["category_names"])
            active = _count_active(conn, section["category_names"])
            missing = max(0, target - current)
            page = min(10, active // 10 + 1)
            published = 0
            try:
                candidates = search_products(section["search_keyword"], category, min(10, max(missing, 1)), page=page) if missing and active < 100 else []
            except Exception as exc:
                print(f"[amazon] búsqueda {slug} falló: {type(exc).__name__}: {str(exc)[:240]}")
                results[slug] = {"published": 0, "remaining": missing, "error": str(exc)[:240]}
                continue
            for product in candidates:
                if product["external_id"] in seen:
                    continue
                seen.add(product["external_id"])
                product_id = upsert_product(conn, "amazon", category, product)
                offer = build_customer_offer(product.get("price"), product.get("previous_price"))
                if not offer:
                    continue
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT AVG(p.current_price) AS average
                           FROM products p JOIN platforms pl ON pl.id=p.platform_id
                           JOIN categories c ON c.id=p.category_id
                           WHERE pl.name='amazon' AND c.name=%s AND p.currency=%s
                             AND p.current_price IS NOT NULL AND p.is_active=TRUE""",
                        (category, product["currency"]),
                    )
                    average = float((cur.fetchone() or {}).get("average") or product["price"])
                score = score_product(conn, product_id, average)
                published_offer = publish_discounted_offer(conn, product_id, {
                    "title": product["title"],
                    "current_price": product["price"],
                    "previous_price": product["previous_price"],
                    "currency": product["currency"],
                    "image_url": product["image_url"],
                    "product_url": product["product_url"],
                }, score)
                if published_offer:
                    published += 1
                    total_published += 1
                if current + published >= target:
                    break
            results[slug] = {
                "published": published,
                "remaining": max(0, target - current - published),
                "candidates": len(candidates),
                "page": page if missing and active < 100 else None,
            }
    finally:
        conn.close()
    return {"status": "completed", "published": total_published, "sections": results}
