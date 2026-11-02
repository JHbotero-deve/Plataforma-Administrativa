"""Enriquecimiento de productos desde las páginas públicas de sus URLs."""
import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import requests

ALLOWED_HOSTS = {
    "amazon": ("amazon.com", "amazon.ca", "amazon.com.mx", "amazon.co.uk", "amazon.de", "amazon.es", "amazon.fr", "amazon.it", "amazon.co.jp", "amzn.to", "amzn.com", "a.co"),
    "aliexpress": ("aliexpress.com", "aliexpress.us", "aliexpress.ru", "alii.pub"),
}


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


def _allowed_host(host, platform):
    return any(host == domain or host.endswith("." + domain) for domain in ALLOWED_HOSTS[platform])


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


def _number(value):
    if value is None:
        return None
    raw = re.sub(r"[^0-9,.]", "", str(value)).strip()
    if not raw:
        return None
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".") if raw.rfind(",") > raw.rfind(".") else raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        result = float(raw)
        return result if result > 0 else None
    except (TypeError, ValueError):
        return None


def _public_product_page(url, platform):
    current = url
    for _ in range(6):
        parsed = urlsplit(current)
        host = (parsed.hostname or "").lower().rstrip(".")
        if platform not in ALLOWED_HOSTS or parsed.scheme not in ("http", "https") or not _allowed_host(host, platform):
            raise ValueError("La URL redirigió fuera de los dominios permitidos de la tienda.")
        response = requests.get(current, headers={"User-Agent": "Mozilla/5.0 (compatible; CentralRadar/1.0)", "Accept": "text/html,application/xhtml+xml"}, timeout=15, allow_redirects=False)
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise RuntimeError("La tienda no entregó el destino del enlace.")
            current = urljoin(current, location)
            continue
        try:
            response.raise_for_status()
            parser = _ProductHTML()
            parser.feed(response.text[:2_000_000])
        finally:
            response.close()
        break
    else:
        raise RuntimeError("La tienda excedió el límite de redirecciones.")
    metadata = parser.meta
    title = metadata.get("og:title") or metadata.get("twitter:title") or ""
    image = metadata.get("og:image") or metadata.get("twitter:image")
    price = metadata.get("product:price:amount") or metadata.get("og:price:amount") or metadata.get("price")
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
    return {"title": str(title)[:500] or None, "image_url": image if isinstance(image, str) and image.startswith(("http://", "https://")) else None, "price": _number(price), "currency": str(currency).upper()[:10], "rating": rating, "category": category, "resolved_url": current}


def enrich_product(product):
    url = str(product.get("product_url") or "")
    platform = product.get("platform")
    if platform not in ALLOWED_HOSTS or not url:
        return product
    try:
        fresh = _public_product_page(url, platform)
        for key in ("title", "image_url", "price", "currency", "rating"):
            if fresh.get(key) is not None and (key == "currency" or not product.get(key) or key == "title" and str(product.get(key)).startswith("Producto ")):
                product[key] = fresh[key]
        product["product_url"] = fresh["resolved_url"]
        product["source_metadata"] = {**(product.get("source_metadata") or {}), "metadata_source": "public_product_page", "resolved_url": fresh["resolved_url"]}
    except Exception as exc:
        print(f"[catalog] product URL enrichment unavailable ({platform}): {type(exc).__name__}")
    return product


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
        enriched = enrich_product(product)
        if enriched.get("title") == row["title"] and enriched.get("price") == row["current_price"] and enriched.get("image_url") == row["image_url"] and enriched.get("product_url") == row["product_url"]:
            continue
        with conn.cursor() as cur:
            cur.execute("""UPDATE products SET title=%s,current_price=COALESCE(%s,current_price),currency=COALESCE(%s,currency),image_url=COALESCE(%s,image_url),product_url=%s,source_metadata=%s::jsonb,updated_at=NOW() WHERE id=%s""", (enriched.get("title") or row["title"], enriched.get("price"), enriched.get("currency"), enriched.get("image_url"), enriched.get("product_url") or row["product_url"], json.dumps(enriched.get("source_metadata") or {}), row["id"]))
        conn.commit()
        updated += 1
    return updated
