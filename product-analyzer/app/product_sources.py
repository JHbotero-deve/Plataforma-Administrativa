"""Búsqueda y enriquecimiento de productos con APIs oficiales y metadatos públicos."""
import json
import os
import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import requests

_TOKEN = None
_TOKEN_EXPIRES = 0
ASIN_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?:[/?]|$)", re.I)


def _env(name, default=""):
    target = name.strip().casefold()
    for key, value in os.environ.items():
        if key.strip().casefold() == target and str(value or "").strip():
            return str(value).strip()
    return default


def _amazon_config():
    credential_id = _env("AMAZON_CREDENTIAL_ID")
    secret = _env("AMAZON_CREDENTIAL_SECRET")
    version = _env("AMAZON_CREDENTIAL_VERSION", "3.1")
    partner_tag = _env("AMAZON_PARTNER_TAG") or _env("AMAZON_ASSOCIATE_TAG")
    if not all((credential_id, secret, partner_tag)):
        raise RuntimeError("Amazon Creators API requiere Credential ID, Credential Secret y Partner Tag.")
    endpoints = {"3.1": "https://api.amazon.com/auth/o2/token", "3.2": "https://api.amazon.co.uk/auth/o2/token", "3.3": "https://api.amazon.co.jp/auth/o2/token"}
    if version not in endpoints:
        raise RuntimeError("AMAZON_CREDENTIAL_VERSION debe ser 3.1, 3.2 o 3.3.")
    return credential_id, secret, version, partner_tag, endpoints[version]


def _amazon_token():
    global _TOKEN, _TOKEN_EXPIRES
    if _TOKEN and time.time() < _TOKEN_EXPIRES:
        return _TOKEN
    credential_id, secret, _, _, endpoint = _amazon_config()
    response = requests.post(endpoint, json={"grant_type": "client_credentials", "client_id": credential_id, "client_secret": secret, "scope": "creatorsapi::default"}, timeout=15)
    response.raise_for_status()
    payload = response.json()
    token = payload.get("access_token")
    if not token:
        raise RuntimeError("Amazon no devolvió un token de acceso para Creators API.")
    _TOKEN = token
    _TOKEN_EXPIRES = time.time() + max(int(payload.get("expires_in", 3600)) - 60, 60)
    return _TOKEN


def _amazon_request(operation, marketplace, payload):
    _, _, _, partner_tag, _ = _amazon_config()
    response = requests.post("https://creatorsapi.amazon/catalog/v1/" + operation, headers={"Authorization": "Bearer " + _amazon_token(), "Content-Type": "application/json", "x-marketplace": marketplace}, json={**payload, "marketplace": marketplace, "partnerTag": partner_tag}, timeout=20)
    response.raise_for_status()
    data = response.json()
    if data.get("errors"):
        raise RuntimeError("Amazon Creators API devolvió errores de catálogo.")
    return data


def _first_price(item):
    offers = item.get("offersV2") or {}
    listings = offers.get("listings") or []
    if isinstance(listings, dict):
        listings = [listings]
    for listing in listings:
        price = listing.get("price") or {}
        money = price.get("money") or price
        amount = money.get("amount") or money.get("value")
        currency = money.get("currency") or money.get("currencyCode")
        try:
            if amount is not None and float(amount) > 0:
                return float(amount), str(currency or "USD").upper()
        except (TypeError, ValueError):
            pass
    return None, "USD"


def _amazon_product(item, category="accesorios"):
    asin = str(item.get("asin") or "").strip()
    info = item.get("itemInfo") or {}
    title = (info.get("title") or {}).get("displayValue") or ""
    images = item.get("images") or {}
    primary = images.get("primary") or {}
    image = next((primary.get(key, {}).get("url") for key in ("large", "medium", "small") if isinstance(primary.get(key), dict) and primary[key].get("url")), None)
    price, currency = _first_price(item)
    url = item.get("detailPageURL") or (f"https://www.amazon.com/dp/{asin}" if asin else None)
    if not asin or not title or not url:
        return None
    return {"platform": "amazon", "external_id": asin, "title": str(title)[:500], "image_url": image, "product_url": url, "price": price, "currency": currency, "rating": None, "reviews_count": 0, "sales_estimate": None, "category": category, "source_metadata": {"import_method": "amazon_creators_api", "metadata_source": "official_api"}}


def _amazon_items(items, marketplace="www.amazon.com", category="accesorios"):
    payload = _amazon_request("getItems", marketplace, {"itemIds": items[:10], "itemIdType": "ASIN", "resources": ["images.primary.large", "images.primary.medium", "itemInfo.title", "offersV2.listings.price"]})
    result = payload.get("itemsResult") or {}
    return [product for item in result.get("items", []) if (product := _amazon_product(item, category))]


def search_amazon(term, category="accesorios", limit=10):
    payload = _amazon_request("searchItems", "www.amazon.com", {"keywords": term[:100], "searchIndex": "All", "itemCount": min(max(int(limit), 1), 10), "resources": ["images.primary.large", "itemInfo.title", "offersV2.listings.price"]})
    items = (payload.get("searchResult") or {}).get("items") or []
    return [product for item in items if (product := _amazon_product(item, category))]


def _resolve_amazon_url(url):
    allowed = ("amazon.com", "amazon.ca", "amazon.com.mx", "amazon.co.uk", "amazon.de", "amazon.es", "amazon.fr", "amazon.it", "amazon.co.jp", "amzn.to", "amzn.com", "a.co")
    current = url
    for _ in range(5):
        parsed = urlsplit(current)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in ("http", "https") or not any(host == domain or host.endswith("." + domain) for domain in allowed):
            raise ValueError("La URL redirigió fuera de los dominios permitidos de Amazon.")
        response = requests.get(current, headers={"User-Agent": "CentralRadar/1.0 (+product URL resolver)"}, timeout=12, allow_redirects=False, stream=True)
        try:
            if response.status_code not in (301, 302, 303, 307, 308):
                response.raise_for_status()
                return current
            location = response.headers.get("Location")
            if not location:
                return current
            current = urljoin(current, location)
        finally:
            response.close()
    return current


def _amazon_asin(url):
    match = ASIN_RE.search(urlsplit(url).path)
    return match.group(1).upper() if match else None


class _ProductHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}
        self.jsonld = []
        self._script = False
        self._script_data = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag.lower() == "meta":
            key = values.get("property") or values.get("name") or values.get("itemprop")
            if key and values.get("content"):
                self.meta[key.casefold()] = values["content"]
        if tag.lower() == "script" and values.get("type", "").lower() == "application/ld+json":
            self._script = True
            self._script_data = []

    def handle_data(self, data):
        if self._script:
            self._script_data.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "script" and self._script:
            self.jsonld.append("".join(self._script_data))
            self._script = False


def _walk_products(value):
    if isinstance(value, dict):
        kind = value.get("@type")
        if kind == "Product" or isinstance(kind, list) and "Product" in kind:
            yield value
        for child in value.values():
            yield from _walk_products(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_products(child)


def _public_aliexpress_page(url):
    allowed = ("aliexpress.com", "aliexpress.us", "aliexpress.ru", "alii.pub")
    parsed_url = urlsplit(url)
    current = parsed_url._replace(scheme="https").geturl() if parsed_url.scheme == "http" else url
    response = None
    for _ in range(5):
        parsed = urlsplit(current)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or not any(host == domain or host.endswith("." + domain) for domain in allowed):
            raise ValueError("La URL redirigió fuera de los dominios permitidos de AliExpress.")
        response = requests.get(current, headers={"User-Agent": "CentralRadar/1.0 (+product metadata request)", "Accept": "text/html,application/xhtml+xml"}, timeout=15, allow_redirects=False)
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            if not location:
                break
            current = urljoin(current, location)
            continue
        response.raise_for_status()
        break
    if response is None or response.status_code in (301, 302, 303, 307, 308):
        raise RuntimeError("AliExpress no permitió resolver la URL del producto.")
    parser = _ProductHTML()
    parser.feed(response.text[:2_000_000])
    metadata = parser.meta
    title = metadata.get("og:title") or metadata.get("twitter:title") or ""
    image = metadata.get("og:image") or metadata.get("twitter:image")
    price = metadata.get("product:price:amount") or metadata.get("og:price:amount")
    currency = metadata.get("product:price:currency") or metadata.get("og:price:currency") or "USD"
    category = "accesorios"
    rating = None
    for raw in parser.jsonld:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            continue
        for product in _walk_products(data):
            title = title or product.get("name") or ""
            image_value = product.get("image")
            image = image or (image_value[0] if isinstance(image_value, list) and image_value else image_value if isinstance(image_value, str) else None)
            offers = product.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            price = price or offers.get("price") or offers.get("lowPrice")
            currency = offers.get("priceCurrency") or currency
            category = product.get("category") or category
            rating = (product.get("aggregateRating") or {}).get("ratingValue")
            break
    try:
        price = float(str(price).replace(",", ".")) if price else None
        if price is not None and price <= 0:
            price = None
    except (TypeError, ValueError):
        price = None
    return {"title": str(title)[:500] or None, "image_url": image, "price": price, "currency": str(currency).upper()[:10], "rating": rating, "category": category, "resolved_url": current}


def enrich_product(product):
    url = str(product.get("product_url") or "")
    platform = product.get("platform")
    try:
        if platform == "amazon":
            resolved_url = url if _amazon_asin(url) else _resolve_amazon_url(url)
            asin = _amazon_asin(resolved_url)
            if resolved_url != url:
                product["product_url"] = resolved_url
            if asin:
                items = _amazon_items([asin], category=product.get("category") or "accesorios")
                if items:
                    fresh = items[0]
                    for key in ("title", "image_url", "price", "currency"):
                        if fresh.get(key) is not None:
                            product[key] = fresh[key]
                    product["source_metadata"] = {**(product.get("source_metadata") or {}), **fresh["source_metadata"]}
        elif platform == "aliexpress":
            fresh = _public_aliexpress_page(url)
            for key in ("title", "image_url", "price", "currency", "rating"):
                if fresh.get(key) is not None:
                    product[key] = fresh[key]
            product["source_metadata"] = {**(product.get("source_metadata") or {}), "metadata_source": "public_product_metadata", "resolved_url": fresh["resolved_url"]}
    except Exception as exc:
        print(f"[catalog] product enrichment unavailable ({platform}): {type(exc).__name__}")
    return product


def search_marketplaces(term, category="accesorios", limit=10):
    products = []
    errors = []
    try:
        products.extend(search_amazon(term, category, limit))
    except Exception as exc:
        errors.append("Amazon: " + str(exc)[:160])
    try:
        from promotion_bots import _search_bucket
        products.extend(_search_bucket(category, term)[:limit])
    except Exception as exc:
        errors.append("AliExpress: " + str(exc)[:160])
    unique = {}
    for product in products:
        unique[(product["platform"], product["external_id"])] = product
    return list(unique.values())[:limit * 2], errors


def enrich_pending_products(conn, limit=80):
    with conn.cursor() as cur:
        cur.execute("""SELECT p.id,p.title,p.current_price,p.currency,p.image_url,p.product_url,pl.name AS platform,c.name AS category,p.source_metadata
            FROM products p JOIN platforms pl ON pl.id=p.platform_id LEFT JOIN categories c ON c.id=p.category_id
            WHERE p.is_active=TRUE AND p.product_url IS NOT NULL AND (p.current_price IS NULL OR p.image_url IS NULL OR p.title LIKE 'Producto %')
            ORDER BY p.updated_at DESC LIMIT %s""", (limit,))
        rows = cur.fetchall()
    updated = 0
    for row in rows:
        product = {"platform": row["platform"], "product_url": row["product_url"], "title": row["title"], "price": row["current_price"], "currency": row["currency"], "image_url": row["image_url"], "category": row["category"] or "accesorios", "source_metadata": row["source_metadata"] or {}}
        try:
            enriched = enrich_product(product)
        except Exception as exc:
            print(f"[catalog] enrichment failed for {row['id']}: {type(exc).__name__}")
            continue
        if enriched.get("title") == row["title"] and enriched.get("price") == row["current_price"] and enriched.get("image_url") == row["image_url"]:
            continue
        with conn.cursor() as cur:
            cur.execute("""UPDATE products SET title=%s,current_price=COALESCE(%s,current_price),currency=COALESCE(%s,currency),image_url=COALESCE(%s,image_url),source_metadata=%s::jsonb,updated_at=NOW() WHERE id=%s""", (enriched.get("title") or row["title"], enriched.get("price"), enriched.get("currency"), enriched.get("image_url"), json.dumps(enriched.get("source_metadata") or {}), row["id"]))
        conn.commit()
        updated += 1
    return updated
