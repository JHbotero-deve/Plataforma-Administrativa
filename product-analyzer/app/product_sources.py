"""Enriquecimiento de productos desde metadatos públicos y exportaciones autorizadas."""
import json
import math
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

from db import _affiliate_url
from url_import import ASIN_RE

ENRICHMENT_VERSION = 13
ALIEXPRESS_ITEM_RE = re.compile(r"/item/(\d+)(?:\.html)?", re.I)

MAX_REDIRECTS = 12
ALLOWED_HOSTS = {
    "amazon": ("amazon.com", "amazon.ca", "amazon.com.mx", "amazon.co.uk", "amazon.de", "amazon.es", "amazon.fr", "amazon.it", "amazon.co.jp", "amazon.com.au", "amazon.in", "amazon.nl", "amazon.se", "amazon.pl", "amazon.ae", "amazon.sg", "amazon.com.tr", "amazon.com.br", "link.amazon", "amzn.to", "amzn.eu", "amzn.asia", "amzn.com", "amzn.ca", "amzn.de", "amzn.co.uk", "a.co"),
    "aliexpress": ("aliexpress.com", "aliexpress.us", "aliexpress.ru", "a.aliexpress.com", "alii.pub", "s.click.aliexpress.com", "click.aliexpress.com"),
}
ALLOWED_CATEGORIES = {"ropa", "calzado", "accesorios", "tecnologia", "hogar", "moda", "deportes", "belleza", "bienestar", "juguetes", "oficina"}
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
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


def _is_placeholder_title(value):
    title = re.sub(r"\s+", " ", str(value or "").strip().casefold())
    normalized = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    generic_id = re.match(r"^producto (?:amazon|aliexpress|url)(?:[ -]?[a-z0-9_-]{6,})?$", title)
    return not title or title == "producto" or bool(generic_id) or any(
        phrase in normalized
        for phrase in ("oferta destacada global", "producto verificado con envio rapido", "producto verificado con envio", "titulo real del producto", "nombre real del producto")
    )


def _is_placeholder_image_url(value):
    try:
        parsed = urlsplit(str(value or "").strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        return host in {"enlace-de-la-imagen.jpg", "example.com", "www.example.com", "placeholder.com"} or "placeholder" in parsed.path.casefold()
    except ValueError:
        return True


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
        whole, separator, fraction = raw.partition(",")
        raw = whole + fraction if len(fraction) == 3 else whole + "." + fraction
    elif "." in raw:
        whole, separator, fraction = raw.partition(".")
        raw = whole + fraction if len(fraction) == 3 else raw
    try:
        result = float(raw)
        return result if result > 0 else None
    except (TypeError, ValueError):
        return None


def _category(value, title=""):
    text = unicodedata.normalize("NFKD", f"{value or ''} {title or ''}").encode("ascii", "ignore").decode().lower()
    if any(word in text for word in ("smartphone", "iphone", "android", "laptop", "notebook", "computer", "tablet", "monitor", "keyboard", "mouse", "technology", "electronics", "electronica", "tecnologia", "celular", "portatil", "gaming", "videojuego", "videojuegos", "consola", "pc gamer", "alienware", "geforce", "rtx", "core ultra")):
        return "tecnologia"
    if any(word in text for word in ("home decor", "kitchen", "furniture", "household", "decoracion", "cocina", "muebles", "hogar", "lampara")):
        return "hogar"
    if any(word in text for word in ("sports", "fitness", "gym", "exercise", "deportes", "gimnasio", "entrenamiento")):
        return "deportes"
    if any(word in text for word in ("beauty", "makeup", "cosmetic", "skincare", "maquillaje", "belleza", "cuidado de la piel")):
        return "belleza"
    if any(word in text for word in ("personal care", "wellness", "self care", "bienestar", "cuidado personal")):
        return "bienestar"
    if any(word in text for word in ("toy", "toys", "juguete", "juguetes", "muneco", "munecos", "peluche", "peluches")):
        return "juguetes"
    if any(word in text for word in ("office", "stationery", "school supplies", "papeleria", "oficina", "estudio")):
        return "oficina"
    if any(word in text for word in ("footwear", "shoes", "sneaker", "boots", "sandals", "zapato", "tenis", "botas", "sandalia", "calzado")):
        return "calzado"
    if any(word in text for word in ("clothing", "apparel", "shirt", "pants", "dress", "jacket", "hoodie", "sweater", "camisa", "pantalon", "vestido", "chaqueta", "sudadera", "blusa", "falda", "ropa")):
        return "ropa"
    if any(word in text for word in ("fashion", "moda")):
        return "moda"
    if any(word in text for word in ("accessories", "accessory", "bag", "backpack", "watch", "jewelry", "phone case", "bolso", "mochila", "reloj", "joya", "funda", "lentes", "gorra", "cinturon", "accesorios", "auricular", "audifono", "audifonos", "earbuds", "headphones", "headset")):
        return "accesorios"
    return None
def _normalized_url(url):
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))


def _public_product_page(url, platform):
    current = url
    seen_urls = set()
    redirect_hosts = []
    referer_url = None
    for hop in range(MAX_REDIRECTS + 1):
        parsed = urlsplit(current)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
        if platform in ALLOWED_HOSTS and parsed.scheme == "http" and not parsed.username and not parsed.password and port in (None, 80) and _allowed_host(host, platform):
            current = parsed._replace(scheme="https", netloc=host).geturl()
            parsed = urlsplit(current)
            host = (parsed.hostname or "").lower().rstrip(".")
        if platform not in ALLOWED_HOSTS or parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443) or not _allowed_host(host, platform):
            raise ValueError(f"La URL redirigió fuera de los dominios HTTPS permitidos de la tienda (host={host or 'desconocido'}, esquema={parsed.scheme or 'vacío'}).")
        normalized = _normalized_url(current)
        if normalized in seen_urls:
            raise RuntimeError("La tienda devolvió un ciclo de redirección, normalmente causado por un inicio de sesión obligatorio.")
        seen_urls.add(normalized)
        headers = dict(BROWSER_HEADERS)
        if referer_url:
            headers["Referer"] = referer_url
        response = requests.get(current, headers=headers, timeout=15, allow_redirects=False)
        if response.status_code in (301, 302, 303, 307, 308):
            redirect_hosts.append(host)
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise RuntimeError("La tienda no entregó el destino del enlace.")
            if hop >= MAX_REDIRECTS:
                raise RuntimeError(f"La tienda excedió el límite de {MAX_REDIRECTS} redirecciones.")
            referer_url = current
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
    final_host = (urlsplit(current).hostname or "").lower()
    title_check = title.casefold()
    if final_host.startswith("login.") or any(term in title_check for term in ("sign in", "log in", "login", "iniciar sesión", "iniciar sesion")):
        raise RuntimeError("La tienda exige iniciar sesión para mostrar los datos del producto.")
    image = metadata.get("og:image") or metadata.get("twitter:image")
    price = metadata.get("product:price:amount") or metadata.get("og:price:amount") or metadata.get("price")
    previous_price = metadata.get("product:price:original") or metadata.get("product:original_price:amount") or metadata.get("og:price:standard_amount") or metadata.get("product:price:regular") or metadata.get("product:price:old_amount") or metadata.get("price:original")
    description = metadata.get("og:description") or metadata.get("twitter:description") or metadata.get("description") or ""
    currency = metadata.get("product:price:currency") or metadata.get("og:price:currency")
    category = metadata.get("product:category") or metadata.get("og:category")
    rating = metadata.get("rating")
    reviews_count = metadata.get("review_count") or metadata.get("rating_count")
    seller = None
    seller_url = None
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
            for item in images:
                if isinstance(item, dict):
                    item = item.get("url") or item.get("contentUrl")
                if isinstance(item, str) and item:
                    gallery.append(urljoin(current, item))
            offers = product.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            if isinstance(offers, dict) and isinstance(offers.get("offers"), list):
                offers = offers["offers"][0] if offers["offers"] else {}
            if isinstance(offers, dict):
                specifications = offers.get("priceSpecification") or []
                specifications = specifications if isinstance(specifications, list) else [specifications]
                for specification in specifications:
                    if not isinstance(specification, dict):
                        continue
                    price_type = str(specification.get("priceType") or specification.get("name") or "").casefold()
                    if any(term in price_type for term in ("list", "original", "regular", "strikethrough", "reference")):
                        previous_price = previous_price or specification.get("price")
            price = price or (offers.get("price") if isinstance(offers, dict) else None) or (offers.get("lowPrice") if isinstance(offers, dict) else None)
            currency = (offers.get("priceCurrency") if isinstance(offers, dict) else None) or currency
            description = description or product.get("description") or ""
            category = product.get("category") or category
            aggregate = product.get("aggregateRating") or {}
            rating = rating or (aggregate.get("ratingValue") if isinstance(aggregate, dict) else None)
            reviews_count = reviews_count or ((aggregate.get("reviewCount") or aggregate.get("ratingCount")) if isinstance(aggregate, dict) else None)
            seller_data = product.get("seller") or (offers.get("seller") if isinstance(offers, dict) else None) or {}
            if isinstance(seller_data, list):
                seller_data = seller_data[0] if seller_data else {}
            if isinstance(seller_data, dict):
                seller_name = str(seller_data.get("name") or "").strip()
                seller_url = seller_data.get("url") or seller_data.get("@id")
            else:
                seller_name = str(seller_data or "").strip()
            if seller_name:
                seller = {"external_id": str(seller_url or seller_name)[:255], "name": seller_name}
            break
    gallery = list(dict.fromkeys(item for item in gallery if isinstance(item, str) and item.startswith(("http://", "https://"))))[:10]
    image_url = gallery[0] if gallery else None
    return {
        "title": str(title)[:500] or None,
        "image_url": image_url,
        "gallery_urls": gallery,
        "price": _number(price),
        "previous_price": _number(previous_price),
        "description": str(description).strip()[:5000] or None,
        "currency": str(currency).upper()[:10] if currency else None,
        "rating": _number(rating),
        "reviews_count": int(_number(reviews_count)) if _number(reviews_count) is not None else None,
        "category": _category(category),
        "seller": seller,
        "seller_url": urljoin(current, seller_url) if seller_url else None,
        "resolved_url": current,
    }


def enrich_product(product, force=False):
    """Enriquece y revalida productos con la página pública de su URL."""
    url = str(product.get("product_url") or "").strip()
    platform = product.get("platform")
    if platform not in ALLOWED_HOSTS or not url:
        return product
    metadata = dict(product.get("source_metadata") or {})
    manual_fields = set(metadata.get("manual_fields") or [])
    field_sources = dict(metadata.get("field_sources") or {})
    if _is_placeholder_title(product.get("title")):
        manual_fields.discard("title")
        field_sources.pop("title", None)
    if _is_placeholder_image_url(product.get("image_url")):
        product["image_url"] = None
        manual_fields.discard("image_url")
        field_sources.pop("image_url", None)
    metadata["manual_fields"] = sorted(manual_fields)
    metadata["field_sources"] = field_sources
    if not product.get("category"):
        inferred_category = _category(None, product.get("title"))
        if inferred_category:
            product["category"] = inferred_category
            metadata["category_source"] = "title_inference"
    title = str(product.get("title") or "").strip()
    complete = bool(title and not _is_placeholder_title(title) and product.get("price") and product.get("currency") and product.get("currency") not in {"UNK", "N/D"} and product.get("image_url") and not _is_placeholder_image_url(product.get("image_url")) and product.get("category") and (field_sources.get("category") in {"public_product_page", "user"} or "category" in manual_fields))
    if complete and not force:
        for field in manual_fields:
            if product.get(field) is not None:
                field_sources[field] = "user"
        metadata["field_sources"] = field_sources
        metadata.update({"enrichment_status": "complete", "enrichment_version": ENRICHMENT_VERSION, "missing_fields": []})
        product["source_metadata"] = metadata
        return product
    try:
        legacy_url_only = metadata.get("metadata_source") == "url_only"
        page = _public_product_page(url, platform)
        resolved_url = page.get("resolved_url") or url
        page_sources = dict(page.get("_field_sources") or {})
        public_page_has_data = any(page.get(key) for key in ("title", "price", "image_url", "description"))

        has_page_data = any(page.get(key) for key in ("title", "price", "image_url", "description"))
        metadata["metadata_source"] = "public_product_page" if public_page_has_data else "public_product_page_empty"
        for field, key in (("title", "title"), ("price", "price"), ("previous_price", "previous_price"), ("currency", "currency"), ("image_url", "image_url"), ("description", "description"), ("rating", "rating"), ("reviews_count", "reviews_count"), ("category", "category")):
            current = product.get(field)
            missing_title = field == "title" and _is_placeholder_title(current)
            missing_currency = field == "currency" and str(current or "").upper() in {"", "UNK", "N/D"}
            replace_legacy = legacy_url_only and field in {"price", "currency"}
            replace_unverified_category = field == "category" and field_sources.get("category") not in {"public_product_page", "user"}
            should_update = force or current is None or current == "" or missing_title or missing_currency or replace_legacy or replace_unverified_category
            if should_update and field not in manual_fields and page.get(key) is not None:
                product[field] = page[key]
        if page.get("category") and "category" not in manual_fields:
            metadata.pop("category_source", None)
        product["gallery_urls"] = list(dict.fromkeys((product.get("gallery_urls") or []) + (page.get("gallery_urls") or [])))[:10]
        if (not product.get("image_url") or _is_placeholder_image_url(product.get("image_url"))) and product["gallery_urls"] and "image_url" not in manual_fields:
            product["image_url"] = product["gallery_urls"][0]
        if not product.get("seller") and page.get("seller"):
            product["seller"] = page["seller"]
        product["product_url"] = resolved_url
        page_price = page.get("price")
        page_currency = str(page.get("currency") or "").upper()
        try:
            price_matches = page_price is not None and math.isclose(float(product.get("price")), float(page_price), rel_tol=0.01, abs_tol=0.01)
        except (TypeError, ValueError):
            price_matches = False
        currency_matches = bool(page_currency and page_currency == str(product.get("currency") or "").upper())
        category_verified = bool(page.get("category") or ("category" in manual_fields and product.get("category")))
        refresh_verified = bool(page.get("title") and page_price is not None and page_currency and page.get("image_url") and category_verified and price_matches and currency_matches)
        field_sources = dict(metadata.get("field_sources") or {})
        for field in ("title", "price", "previous_price", "currency", "image_url", "description", "rating", "reviews_count", "category", "seller"):
            if field not in manual_fields:
                field_sources.pop(field, None)
        for field in manual_fields:
            if product.get(field) is not None:
                field_sources[field] = "user"
        for field, key in (("title", "title"), ("price", "price"), ("previous_price", "previous_price"), ("currency", "currency"), ("image_url", "image_url"), ("description", "description"), ("rating", "rating"), ("reviews_count", "reviews_count"), ("category", "category"), ("seller", "seller")):
            if page.get(key) is not None and field not in manual_fields:
                field_sources[field] = page_sources.get(key, "public_product_page")
        metadata["field_sources"] = field_sources
        category_verified = field_sources.get("category") in {"public_product_page", "user"}
        has_complete_data = bool(product.get("title") and not _is_placeholder_title(product.get("title")) and product.get("price") and product.get("currency") and product.get("currency") not in {"UNK", "N/D"} and product.get("image_url") and not _is_placeholder_image_url(product.get("image_url")) and product.get("category") and category_verified)
        manual_complete = all(field_sources.get(field) == "user" and product.get(field) not in (None, "") for field in ("title", "price", "currency", "image_url", "category"))
        catalog_current = refresh_verified or manual_complete
        metadata.update({
            "catalog_status": "current" if catalog_current else "stale",
            "catalog_refresh_verified": refresh_verified,
            "original_url": metadata.get("original_url") or url,
            "resolved_url": resolved_url,
            "enrichment_status": "complete" if has_complete_data else "partial",
            "enrichment_version": ENRICHMENT_VERSION,
            "enriched_at": datetime.now(timezone.utc).isoformat(),
        })
        metadata.pop("network_enrichment_disabled", None)
        if has_complete_data:
            metadata.pop("enrichment_error", None)
        else:
            metadata["enrichment_error"] = "La página pública no expone todos los metadatos necesarios; completa los campos manualmente o reintenta más tarde."
        if not has_complete_data or not catalog_current:
            metadata["enrichment_retry_after"] = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
        else:
            metadata.pop("enrichment_retry_after", None)
    except Exception as exc:
        field_sources = dict(metadata.get("field_sources") or {})
        manual_complete = all(field_sources.get(field) == "user" and product.get(field) not in (None, "") for field in ("title", "price", "currency", "image_url", "category"))
        metadata.update({
            "original_url": metadata.get("original_url") or url,
            "enrichment_status": "complete" if manual_complete else "partial",
            "enrichment_version": ENRICHMENT_VERSION,
            "catalog_refresh_verified": False,
            "catalog_status": "current" if manual_complete else "stale",
            "enrichment_error": f"{type(exc).__name__}: {str(exc)[:180]}",
        })
        if manual_complete:
            metadata.pop("enrichment_retry_after", None)
        else:
            metadata["enrichment_retry_after"] = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
        metadata.pop("network_enrichment_disabled", None)
    if not product.get("category"):
        inferred_category = _category(None, product.get("title"))
        if inferred_category:
            product["category"] = inferred_category
            metadata["category_source"] = "title_inference"
    path = urlsplit(str(product.get("product_url") or url)).path
    match = ASIN_RE.search(path) if platform == "amazon" else ALIEXPRESS_ITEM_RE.search(path)
    if match and (not product.get("external_id") or str(product["external_id"]).startswith("URL-")):
        product["external_id"] = match.group(1).upper() if platform == "amazon" else match.group(1)
        metadata["external_id_source"] = "resolved_url"
    missing = []
    if _is_placeholder_title(product.get("title")):
        missing.append("title")
    if not product.get("price"):
        missing.append("price")
    if not product.get("currency") or product.get("currency") in {"UNK", "N/D"}:
        missing.append("currency")
    if not product.get("category") or metadata.get("field_sources", {}).get("category") not in {"public_product_page", "user"}:
        missing.append("category")
    if not product.get("image_url") or _is_placeholder_image_url(product.get("image_url")):
        missing.append("image_url")
    metadata["missing_fields"] = missing
    product["source_metadata"] = metadata
    return product

def _retry_due(metadata):
    if (metadata or {}).get("enrichment_version") != ENRICHMENT_VERSION:
        return True
    if (metadata or {}).get("enrichment_status") == "complete":
        return False
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


def _enrich_pending_row(row, force=False):
    source_metadata = row["source_metadata"] or {}
    category = row["category"]
    if category == "accesorios" and source_metadata.get("metadata_source") == "url_only" and not source_metadata.get("category_source"):
        category = None
    product = {
        "platform": row["platform"],
        "external_id": row.get("external_id"),
        "product_url": row["product_url"],
        "title": row["title"],
        "price": row["current_price"],
        "currency": row["currency"],
        "image_url": row["image_url"],
        "gallery_urls": row.get("image_gallery") or [],
        "rating": row.get("rating"),
        "reviews_count": row.get("reviews_count"),
        "category": category,
        "seller": {"external_id": row.get("seller_external_id"), "name": row.get("seller_name")} if row.get("seller_external_id") and row.get("seller_name") else None,
        "source_metadata": source_metadata,
    }
    return enrich_product(product, force=force)


def enrich_pending_products(conn, limit=80, max_workers=4, force=False):
    with conn.cursor() as cur:
        cur.execute("""SELECT p.id,p.external_id,p.title,p.current_price,p.currency,p.image_url,p.image_gallery,p.rating,p.reviews_count,p.product_url,p.affiliate_url,pl.name AS platform,c.name AS category,p.source_metadata,s.external_id AS seller_external_id,s.name AS seller_name,p.is_active,
            (p.catalog_expires_at IS NOT NULL AND p.catalog_expires_at <= NOW()) AS catalog_expired
            FROM products p JOIN platforms pl ON pl.id=p.platform_id LEFT JOIN categories c ON c.id=p.category_id LEFT JOIN sellers s ON s.id=p.seller_id
            WHERE p.product_url IS NOT NULL AND (
                (p.current_price IS NULL OR p.currency IS NULL OR p.currency = 'UNK' OR p.image_url IS NULL OR LOWER(p.title) LIKE 'producto amazon %%' OR LOWER(p.title) LIKE 'producto aliexpress %%' OR LOWER(p.title) LIKE 'producto url-%%' OR LOWER(p.title) IN ('producto', 'producto amazon', 'producto aliexpress', 'producto url') OR LOWER(p.title) LIKE '%%titulo real del producto%%' OR LOWER(p.title) LIKE '%%nombre real del producto%%' OR LOWER(p.title) LIKE '%%oferta destacada global%%' OR LOWER(p.title) LIKE '%%producto verificado con%%' OR LOWER(COALESCE(p.image_url, '')) LIKE '%%enlace-de-la-imagen.jpg%%' OR LOWER(COALESCE(p.image_url, '')) LIKE '%%placeholder%%' OR LOWER(COALESCE(p.image_url, '')) LIKE '%%example.com%%' OR p.category_id IS NULL)
                OR (p.catalog_expires_at IS NOT NULL AND p.catalog_expires_at <= NOW())
                OR (LOWER(p.title) LIKE '%%oferta destacada global%%' OR LOWER(p.title) LIKE '%%producto verificado con%%')
                OR (COALESCE(p.source_metadata->>'enrichment_version', '') <> %s)
                OR (COALESCE(p.source_metadata->>'catalog_status', '') = 'stale')
            )
            ORDER BY p.is_active DESC,(p.catalog_expires_at IS NOT NULL AND p.catalog_expires_at <= NOW()) DESC,p.catalog_expires_at ASC NULLS LAST,p.updated_at ASC LIMIT %s""", (str(ENRICHMENT_VERSION), max(limit, limit * 5)))
        candidates = cur.fetchall()
    due_rows = [row for row in candidates if force or row.get("catalog_expired") or str((row.get("source_metadata") or {}).get("enrichment_version") or "") != str(ENRICHMENT_VERSION) or _retry_due(row.get("source_metadata") or {})]
    rows = due_rows[:limit]
    if not rows:
        print(f"[url-enrichment] candidatos={len(candidates)} listos_para_reintento={len(due_rows)} intentadas=0 recuperadas=0")
        return 0
    with ThreadPoolExecutor(max_workers=max(1, min(int(max_workers), len(rows)))) as executor:
        enriched_rows = list(executor.map(_enrich_pending_row, rows, [bool(force or row.get("catalog_expired") or str((row.get("source_metadata") or {}).get("enrichment_version") or "") != str(ENRICHMENT_VERSION)) for row in rows]))
    recovered = 0
    for row, enriched in zip(rows, enriched_rows):
        metadata = enriched.get("source_metadata") or {}
        gallery = enriched.get("gallery_urls") or row.get("image_gallery") or []
        seller = enriched.get("seller") or {}
        seller_id = None
        candidate_external_id = enriched.get("external_id") if enriched.get("external_id") != row.get("external_id") else None
        affiliate_url = enriched.get("affiliate_url") if enriched.get("affiliate_url") != row.get("affiliate_url") else None
        if not affiliate_url and enriched.get("product_url") != row["product_url"]:
            affiliate_url = _affiliate_url(enriched, row["platform"])
        if seller.get("external_id") and seller.get("name"):
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO sellers(platform_id,external_id,name)
                    VALUES((SELECT id FROM platforms WHERE name=%s),%s,%s)
                    ON CONFLICT(platform_id,external_id) DO UPDATE SET name=COALESCE(EXCLUDED.name,sellers.name)
                    RETURNING id""", (row["platform"], str(seller["external_id"])[:255], str(seller["name"])[:255]))
                seller_row = cur.fetchone()
                seller_id = seller_row["id"] if seller_row else None
        with conn.cursor() as cur:
            cur.execute("""UPDATE products SET
                external_id=CASE WHEN %s IS NOT NULL AND NOT EXISTS (SELECT 1 FROM products existing WHERE existing.platform_id=products.platform_id AND existing.external_id=%s AND existing.id<>products.id) THEN %s ELSE external_id END,
                product_url=COALESCE(%s,product_url),
                affiliate_url=COALESCE(%s,affiliate_url),
                seller_id=COALESCE(%s,seller_id),
                category_id=CASE WHEN %s THEN (SELECT id FROM categories WHERE name=%s) ELSE COALESCE(category_id,(SELECT id FROM categories WHERE name=%s)) END,
                title=%s,
                current_price=COALESCE(%s,current_price),
                currency=COALESCE(%s,currency),
                image_url=COALESCE(%s,image_url),
                image_gallery=CASE WHEN %s::jsonb <> '[]'::jsonb THEN %s::jsonb ELSE image_gallery END,
                rating=COALESCE(%s,rating),
                reviews_count=COALESCE(%s,reviews_count),
                source_metadata=%s::jsonb,
                is_active=CASE WHEN %s THEN TRUE ELSE is_active END,
                catalog_expires_at=CASE WHEN %s THEN NOW()+INTERVAL '48 hours' WHEN %s THEN NOW()+INTERVAL '12 hours' ELSE catalog_expires_at END,
                updated_at=NOW()
                WHERE id=%s""", (
                candidate_external_id, candidate_external_id, candidate_external_id,
                enriched.get("product_url") if enriched.get("product_url") != row["product_url"] else None,
                affiliate_url,
                seller_id,
                bool(row["category"] == "accesorios" and (row["source_metadata"] or {}).get("metadata_source") == "url_only" and not (row["source_metadata"] or {}).get("category_source")),
                enriched.get("category") if enriched.get("category") in ALLOWED_CATEGORIES else None,
                enriched.get("category") if enriched.get("category") in ALLOWED_CATEGORIES else None,
                enriched.get("title") or row["title"],
                enriched.get("price"), enriched.get("currency"), enriched.get("image_url"),
                json.dumps(gallery), json.dumps(gallery),
                enriched.get("rating"), enriched.get("reviews_count"),
                json.dumps(metadata),
                bool(metadata.get("catalog_refresh_verified")),
                bool(metadata.get("catalog_refresh_verified")),
                bool(row.get("catalog_expired")),
                row["id"],
            ))
        if metadata.get("metadata_source") == "public_product_page" and any(enriched.get(key) for key in ("price", "image_url")):
            recovered += 1
    conn.commit()
    complete = sum(1 for item in enriched_rows if (item.get("source_metadata") or {}).get("enrichment_status") == "complete")
    verified = sum(1 for item in enriched_rows if (item.get("source_metadata") or {}).get("catalog_refresh_verified"))
    reactivated = sum(1 for row, item in zip(rows, enriched_rows) if not row.get("is_active") and (item.get("source_metadata") or {}).get("catalog_refresh_verified"))
    print(f"[url-enrichment] intentadas={len(rows)} completas={complete} parciales={len(rows) - complete} revalidadas={verified} reactivadas={reactivated} recuperadas={recovered}")
    return recovered
