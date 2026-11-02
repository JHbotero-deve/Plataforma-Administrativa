"""Enriquecimiento de productos desde metadatos públicos y exportaciones autorizadas."""
import json
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

MAX_REDIRECTS = 12
ALLOWED_HOSTS = {
    "amazon": ("amazon.com", "amazon.ca", "amazon.com.mx", "amazon.co.uk", "amazon.de", "amazon.es", "amazon.fr", "amazon.it", "amazon.co.jp", "amzn.to", "amzn.com", "a.co"),
    "aliexpress": ("aliexpress.com", "aliexpress.us", "aliexpress.ru", "alii.pub"),
}
ALLOWED_CATEGORIES = {"ropa", "calzado", "accesorios"}


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


def _category(value, title=""):
    text = unicodedata.normalize("NFKD", f"{value or ''} {title or ''}").encode("ascii", "ignore").decode().lower()
    if any(word in text for word in ("footwear", "shoes", "sneaker", "boots", "sandals", "zapato", "tenis", "botas", "sandalia", "calzado")):
        return "calzado"
    if any(word in text for word in ("clothing", "apparel", "shirt", "pants", "dress", "jacket", "hoodie", "sweater", "camisa", "pantalon", "vestido", "chaqueta", "sudadera", "blusa", "falda", "ropa")):
        return "ropa"
    if any(word in text for word in ("accessories", "accessory", "bag", "backpack", "watch", "jewelry", "phone case", "bolso", "mochila", "reloj", "joya", "funda", "lentes", "gorra", "cinturon", "accesorios")):
        return "accesorios"
    return None


def _normalized_url(url):
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))


def _public_product_page(url, platform):
    current = url
    seen_urls = set()
    redirect_hosts = []
    for hop in range(MAX_REDIRECTS + 1):
        parsed = urlsplit(current)
        host = (parsed.hostname or "").lower().rstrip(".")
        if platform not in ALLOWED_HOSTS or parsed.scheme not in ("http", "https") or not _allowed_host(host, platform):
            raise ValueError("La URL redirigió fuera de los dominios permitidos de la tienda.")
        normalized = _normalized_url(current)
        if normalized in seen_urls:
            raise RuntimeError("La tienda devolvió un ciclo de redirección, normalmente causado por un inicio de sesión obligatorio.")
        seen_urls.add(normalized)
        response = requests.get(current, headers={"User-Agent": "Mozilla/5.0 (compatible; CentralRadar/1.0)", "Accept": "text/html,application/xhtml+xml"}, timeout=15, allow_redirects=False)
        if response.status_code in (301, 302, 303, 307, 308):
            redirect_hosts.append(host)
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise RuntimeError("La tienda no entregó el destino del enlace.")
            if hop >= MAX_REDIRECTS:
                raise RuntimeError(f"La tienda excedió el límite de {MAX_REDIRECTS} redirecciones.")
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
        chain = " -> ".join(redirect_hosts[-6:])
        raise RuntimeError(f"La tienda excedió el límite de {MAX_REDIRECTS} redirecciones ({chain}).")

    metadata = parser.meta
    title = metadata.get("og:title") or metadata.get("twitter:title") or ""
    image = metadata.get("og:image") or metadata.get("twitter:image")
    price = metadata.get("product:price:amount") or metadata.get("og:price:amount") or metadata.get("price")
    currency = metadata.get("product:price:currency") or metadata.get("og:price:currency")
    category = metadata.get("product:category") or metadata.get("og:category")
    rating = metadata.get("rating")
    reviews_count = metadata.get("review_count") or metadata.get("rating_count")
    gallery = []
    if image:
        gallery.append(urljoin(current, image))
    for raw in parser.jsonld:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            continue
        for product in _walk_products(data):
            title = title or product.get("name") or ""
            image_value = product.get("image")
            images = image_value if isinstance(image_value, list) else [image_value]
            gallery.extend(urljoin(current, item) for item in images if isinstance(item, str) and item)
            offers = product.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            if isinstance(offers, dict) and isinstance(offers.get("offers"), list):
                offers = offers["offers"][0] if offers["offers"] else {}
            price = price or (offers.get("price") if isinstance(offers, dict) else None) or (offers.get("lowPrice") if isinstance(offers, dict) else None)
            currency = (offers.get("priceCurrency") if isinstance(offers, dict) else None) or currency
            category = product.get("category") or category
            aggregate = product.get("aggregateRating") or {}
            rating = rating or (aggregate.get("ratingValue") if isinstance(aggregate, dict) else None)
            reviews_count = reviews_count or ((aggregate.get("reviewCount") or aggregate.get("ratingCount")) if isinstance(aggregate, dict) else None)
            break
    gallery = list(dict.fromkeys(item for item in gallery if isinstance(item, str) and item.startswith(("http://", "https://"))))[:10]
    image_url = gallery[0] if gallery else None
    return {
        "title": str(title)[:500] or None,
        "image_url": image_url,
        "gallery_urls": gallery,
        "price": _number(price),
        "currency": str(currency).upper()[:10] if currency else None,
        "rating": _number(rating),
        "reviews_count": int(_number(reviews_count) or 0),
        "category": _category(category, title),
        "resolved_url": current,
    }


def enrich_product(product):
    url = str(product.get("product_url") or "")
    platform = product.get("platform")
    if platform not in ALLOWED_HOSTS or not url:
        return product
    metadata = dict(product.get("source_metadata") or {})
    if product.get("title") and not str(product["title"]).startswith("Producto ") and product.get("price") and product.get("image_url") and product.get("category"):
        return product
    try:
        fresh = _public_product_page(url, platform)
        if fresh.get("title") and (not product.get("title") or str(product["title"]).startswith("Producto ")):
            product["title"] = fresh["title"]
        for key in ("price", "image_url", "rating", "reviews_count"):
            if fresh.get(key) is not None and (product.get(key) is None or key == "reviews_count" and not product.get(key)):
                product[key] = fresh[key]
        if fresh.get("price") is not None and fresh.get("currency") and not product.get("price"):
            product["currency"] = fresh["currency"]
        if not product.get("category") and fresh.get("category"):
            product["category"] = fresh["category"]
            metadata["category_source"] = "public_product_page"
        if fresh.get("gallery_urls"):
            product["gallery_urls"] = list(dict.fromkeys((product.get("gallery_urls") or []) + fresh["gallery_urls"]))[:10]
        metadata.update({
            "metadata_source": "public_product_page",
            "resolved_url": fresh["resolved_url"],
            "enrichment_status": "complete" if product.get("title") and product.get("price") and product.get("image_url") and product.get("category") else "partial",
            "enrichment_attempted_at": datetime.now(timezone.utc).isoformat(),
            "enrichment_retry_after": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
        })
        metadata.pop("enrichment_error", None)
    except Exception as exc:
        message = str(exc)
        login_loop = "ciclo de redirección" in message.lower() or "límite de" in message.lower()
        metadata.update({
            "enrichment_status": "blocked" if login_loop or "403" in message or "401" in message else "retry",
            "enrichment_error": ("login_or_redirect_required" if login_loop else type(exc).__name__)[:80],
            "enrichment_attempted_at": datetime.now(timezone.utc).isoformat(),
            "enrichment_retry_after": (datetime.now(timezone.utc) + timedelta(hours=24 if login_loop else 2)).isoformat(),
        })
        print(f"[catalog] product URL enrichment unavailable ({platform}): {metadata['enrichment_error']}")
    product["source_metadata"] = metadata
    return product


def _retry_due(metadata):
    retry_after = (metadata or {}).get("enrichment_retry_after")
    if not retry_after:
        return True
    try:
        retry = datetime.fromisoformat(str(retry_after).replace("Z", "+00:00"))
        if retry.tzinfo is None:
            retry = retry.replace(tzinfo=timezone.utc)
        return retry <= datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return True


def _enrich_pending_row(row):
    source_metadata = row["source_metadata"] or {}
    category = row["category"]
    if category == "accesorios" and source_metadata.get("metadata_source") == "url_only" and not source_metadata.get("category_source"):
        category = None
    product = {
        "platform": row["platform"],
        "product_url": row["product_url"],
        "title": row["title"],
        "price": row["current_price"],
        "currency": row["currency"],
        "image_url": row["image_url"],
        "gallery_urls": row.get("image_gallery") or [],
        "rating": row.get("rating"),
        "reviews_count": row.get("reviews_count"),
        "category": category,
        "source_metadata": source_metadata,
    }
    return enrich_product(product)


def enrich_pending_products(conn, limit=80, max_workers=4):
    with conn.cursor() as cur:
        cur.execute("""SELECT p.id,p.title,p.current_price,p.currency,p.image_url,p.image_gallery,p.rating,p.reviews_count,p.product_url,pl.name AS platform,c.name AS category,p.source_metadata
            FROM products p JOIN platforms pl ON pl.id=p.platform_id LEFT JOIN categories c ON c.id=p.category_id
            WHERE p.is_active=TRUE AND p.product_url IS NOT NULL AND (p.current_price IS NULL OR p.image_url IS NULL OR p.title LIKE 'Producto %%' OR p.category_id IS NULL)
            ORDER BY p.updated_at DESC LIMIT %s""", (max(limit, limit * 5),))
        candidates = cur.fetchall()
    rows = [row for row in candidates if _retry_due(row.get("source_metadata") or {})][:limit]
    if not rows:
        return 0
    with ThreadPoolExecutor(max_workers=max(1, min(int(max_workers), len(rows)))) as executor:
        enriched_rows = list(executor.map(_enrich_pending_row, rows))
    recovered = 0
    for row, enriched in zip(rows, enriched_rows):
        metadata = enriched.get("source_metadata") or {}
        gallery = enriched.get("gallery_urls") or row.get("image_gallery") or []
        with conn.cursor() as cur:
            cur.execute("""UPDATE products SET
                category_id=CASE WHEN %s THEN (SELECT id FROM categories WHERE name=%s) ELSE COALESCE(category_id,(SELECT id FROM categories WHERE name=%s)) END,
                title=%s,
                current_price=COALESCE(%s,current_price),
                currency=COALESCE(%s,currency),
                image_url=COALESCE(%s,image_url),
                image_gallery=CASE WHEN %s::jsonb <> '[]'::jsonb THEN %s::jsonb ELSE image_gallery END,
                rating=COALESCE(%s,rating),
                reviews_count=COALESCE(%s,reviews_count),
                source_metadata=%s::jsonb,
                updated_at=NOW()
                WHERE id=%s""", (
                bool(row["category"] == "accesorios" and (row["source_metadata"] or {}).get("metadata_source") == "url_only" and not (row["source_metadata"] or {}).get("category_source")),
                enriched.get("category") if enriched.get("category") in ALLOWED_CATEGORIES else None,
                enriched.get("category") if enriched.get("category") in ALLOWED_CATEGORIES else None,
                enriched.get("title") or row["title"],
                enriched.get("price"), enriched.get("currency"), enriched.get("image_url"),
                json.dumps(gallery), json.dumps(gallery),
                enriched.get("rating"), enriched.get("reviews_count"),
                json.dumps(metadata), row["id"],
            ))
        if metadata.get("metadata_source") == "public_product_page" and any(enriched.get(key) for key in ("price", "image_url")):
            recovered += 1
    conn.commit()
    return recovered
