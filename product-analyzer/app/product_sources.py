"""Enriquecimiento de productos desde metadatos públicos y exportaciones autorizadas."""
import json
import os
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

from db import _affiliate_url
from url_import import ASIN_RE

ENRICHMENT_VERSION = 7
ALIEXPRESS_ITEM_RE = re.compile(r"/item/(\d+)(?:\.html)?", re.I)

MAX_REDIRECTS = 12
ALLOWED_HOSTS = {
    "amazon": ("amazon.com", "amazon.ca", "amazon.com.mx", "amazon.co.uk", "amazon.de", "amazon.es", "amazon.fr", "amazon.it", "amazon.co.jp", "amazon.com.au", "amazon.in", "amazon.nl", "amazon.se", "amazon.pl", "amazon.ae", "amazon.sg", "amazon.com.tr", "amazon.com.br", "link.amazon", "amzn.to", "amzn.com", "a.co"),
    "aliexpress": ("aliexpress.com", "aliexpress.us", "aliexpress.ru", "alii.pub", "s.click.aliexpress.com"),
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
    if any(word in text for word in ("smartphone", "iphone", "android", "laptop", "notebook", "computer", "tablet", "monitor", "keyboard", "mouse", "technology", "electronics", "electronica", "tecnologia", "celular")):
        return "tecnologia"
    if any(word in text for word in ("home decor", "kitchen", "furniture", "household", "decoracion", "cocina", "muebles", "hogar", "lampara")):
        return "hogar"
    if any(word in text for word in ("sports", "fitness", "gym", "exercise", "deportes", "gimnasio", "entrenamiento")):
        return "deportes"
    if any(word in text for word in ("beauty", "makeup", "cosmetic", "skincare", "maquillaje", "belleza", "cuidado de la piel")):
        return "belleza"
    if any(word in text for word in ("personal care", "wellness", "self care", "bienestar", "cuidado personal")):
        return "bienestar"
    if any(word in text for word in ("toy", "toys", "game", "juguete", "juegos")):
        return "juguetes"
    if any(word in text for word in ("office", "stationery", "school supplies", "papeleria", "oficina", "estudio")):
        return "oficina"
    if any(word in text for word in ("footwear", "shoes", "sneaker", "boots", "sandals", "zapato", "tenis", "botas", "sandalia", "calzado")):
        return "calzado"
    if any(word in text for word in ("clothing", "apparel", "shirt", "pants", "dress", "jacket", "hoodie", "sweater", "camisa", "pantalon", "vestido", "chaqueta", "sudadera", "blusa", "falda", "ropa")):
        return "ropa"
    if any(word in text for word in ("fashion", "moda")):
        return "moda"
    if any(word in text for word in ("accessories", "accessory", "bag", "backpack", "watch", "jewelry", "phone case", "bolso", "mochila", "reloj", "joya", "funda", "lentes", "gorra", "cinturon", "accesorios", "earbuds", "headphones", "headset")):
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
        response = requests.get(current, headers=BROWSER_HEADERS, timeout=15, allow_redirects=False)
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
        "reviews_count": int(_number(reviews_count) or 0),
        "category": _category(category, title),
        "seller": seller,
        "seller_url": urljoin(current, seller_url) if seller_url else None,
        "resolved_url": current,
    }



def _resolve_product_url(url, platform):
    """Resuelve enlaces cortos de afiliado (s.click.aliexpress.com, amzn.to) siguiendo HTTP y redirecciones HTML/JS."""
    current = url
    seen = set()
    for _ in range(MAX_REDIRECTS + 1):
        parsed = urlsplit(current)
        host = (parsed.hostname or "").lower().rstrip(".")
        if platform not in ALLOWED_HOSTS or parsed.scheme not in ("http", "https") or not _allowed_host(host, platform):
            raise ValueError("La URL redirigió fuera de los dominios permitidos de la tienda.")
        normalized = _normalized_url(current)
        if normalized in seen:
            raise RuntimeError("La tienda devolvió un ciclo de redirección.")
        seen.add(normalized)
        try:
            response = requests.get(
                current,
                headers=BROWSER_HEADERS,
                timeout=15,
                allow_redirects=True,
            )
            final_url = response.url
            html_text = response.text[:200_000]
            response.close()
        except Exception:
            return current

        if ALIEXPRESS_ITEM_RE.search(urlsplit(final_url).path) or ASIN_RE.search(urlsplit(final_url).path):
            return final_url

        match_meta = re.search(r'content=["\'][^"\']*url=([^"\']+)["\']', html_text, re.I)
        if match_meta:
            target = match_meta.group(1)
            current = urljoin(final_url, target)
            continue

        match_js = re.search(r'(?:window\.)?location\.href\s*=\s*["\']([^"\']+)["\']', html_text, re.I)
        if match_js:
            target = match_js.group(1)
            current = urljoin(final_url, target)
            continue

        match_item = re.search(r'aliexpress\.com/item/(\d+)', html_text, re.I)
        if match_item:
            return f"https://www.aliexpress.com/item/{match_item.group(1)}.html"

        return final_url
    return current


def _aliexpress_product_data(product_id, category=None):
    from affiliate_links import aliexpress_api_request
    from promotion_bots import PRODUCT_FIELDS, _official_url

    tracking_id = os.getenv("ALIEXPRESS_TRACKING_ID", "").strip() or os.getenv("ALIEXPRESS_AFFILIATE_TRACKING_ID", "").strip()
    payload = aliexpress_api_request(
        "aliexpress.affiliate.productdetail.get",
        {
            "product_ids": str(product_id),
            "fields": PRODUCT_FIELDS,
            "target_currency": "USD",
            "target_language": "ES",
            "tracking_id": tracking_id,
            "country": "CO",
        },
    )
    root = payload.get("aliexpress_affiliate_productdetail_get_response", payload)
    response = root.get("resp_result", root) if isinstance(root, dict) else {}
    if not isinstance(response, dict):
        raise RuntimeError("AliExpress devolvió una respuesta de detalle inválida.")
    code = response.get("resp_code")
    if code not in (None, "", 0, "0", 200, "200"):
        raise RuntimeError(f"AliExpress rechazó el detalle del producto ({code}).")
    result = response.get("result") or {}
    products = result.get("products") or []
    if isinstance(products, dict):
        products = products.get("product") or products.get("products") or products.get("product_detail") or []
    if isinstance(products, dict):
        products = [products]
    for raw in products if isinstance(products, list) else []:
        if not isinstance(raw, dict) or str(raw.get("product_id") or "") != str(product_id):
            continue
        title = str(raw.get("product_title") or "").strip()[:500] or None
        product_url = _official_url(raw.get("product_detail_url")) or f"https://www.aliexpress.com/item/{product_id}.html"
        image = _official_url(raw.get("product_main_image_url"), image=True)
        gallery = raw.get("product_small_image_urls") or []
        if isinstance(gallery, dict):
            gallery = gallery.get("string") or gallery.get("url") or []
        if isinstance(gallery, str):
            gallery = [gallery]
        if not isinstance(gallery, list):
            gallery = []
        gallery = list(dict.fromkeys(
            value for item in gallery
            if (value := _official_url(item, image=True))
        ))
        if image and image not in gallery:
            gallery.insert(0, image)
        sale_price = _number(raw.get("target_sale_price") or raw.get("sale_price"))
        original_price = _number(raw.get("target_original_price") or raw.get("original_price"))
        inferred = category or _category(
            raw.get("first_level_category_name") or raw.get("second_level_category_name"),
            title,
        )
        shop_id = str(raw.get("shop_id") or "").strip()
        shop_url = _official_url(raw.get("shop_url"))
        seller_name = str(raw.get("shop_name") or raw.get("seller_name") or "").strip()
        if not seller_name and shop_id:
            seller_name = f"Tienda AliExpress #{shop_id}"
        seller = {"external_id": shop_id or seller_name, "name": seller_name} if seller_name else None
        promotion_link = _official_url(raw.get("promotion_link"))
        if not title and sale_price is None and not image:
            continue
        return {
            "external_id": str(product_id),
            "title": title,
            "product_url": product_url,
            "resolved_url": product_url,
            "affiliate_url": promotion_link,
            "price": sale_price,
            "previous_price": original_price,
            "currency": str(raw.get("target_sale_price_currency") or raw.get("sale_price_currency") or "USD").upper()[:10],
            "image_url": image or (gallery[0] if gallery else None),
            "gallery_urls": gallery[:10],
            "description": str(raw.get("product_description") or "").strip()[:5000] or None,
            "rating": None,
            "reviews_count": 0,
            "sales_estimate": int(_number(raw.get("lastest_volume")) or 0),
            "category": inferred,
            "seller": seller,
            "seller_url": shop_url,
            "source_metadata": {
                "metadata_source": "aliexpress_affiliate_api",
                "import_method": "official_aliexpress_affiliate_productdetail_api",
                "seller_name": seller_name or None,
                "seller_id": shop_id or None,
                "seller_url": shop_url,
                "lastest_volume": int(_number(raw.get("lastest_volume")) or 0),
            },
        }
    errors = payload.get("error_response") if isinstance(payload, dict) else None
    code = errors.get("code") if isinstance(errors, dict) else "ProductNotAccessible"
    raise RuntimeError(f"AliExpress no devolvió datos utilizables para el producto ({code}).")

def _aliexpress_product_by_title(title, category=None):
    """Busca un enlace corto por su título en la API oficial de afiliados, sin abrir la página."""
    title = " ".join(str(title or "").split())[:180]
    if not title or title.casefold().startswith("producto "):
        return None
    tracking_id = os.getenv("ALIEXPRESS_TRACKING_ID", "").strip() or os.getenv("ALIEXPRESS_AFFILIATE_TRACKING_ID", "").strip()
    if not (os.getenv("ALIEXPRESS_APP_KEY", "").strip() and os.getenv("ALIEXPRESS_APP_SECRET", "").strip() and tracking_id):
        return None

    normalized_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    stopwords = {"para", "con", "por", "una", "uno", "the", "for", "with", "and", "men", "women", "man", "woman", "hombre", "mujer", "talla", "nivel"}
    source_tokens = [token for token in re.findall(r"[a-z0-9]{3,}", normalized_title) if token not in stopwords]
    source_set = set(source_tokens)
    if len(source_set) < 2:
        return None

    from affiliate_links import aliexpress_api_request
    from promotion_bots import PRODUCT_FIELDS, _normalize_product, _product_results

    payload = aliexpress_api_request(
        "aliexpress.affiliate.product.query",
        {
            "keywords": title,
            "page_no": "1",
            "page_size": "20",
            "sort": "LAST_VOLUME_DESC",
            "target_currency": "USD",
            "target_language": "ES",
            "ship_to_country": "CO",
            "tracking_id": tracking_id,
            "fields": PRODUCT_FIELDS,
        },
    )
    matches = []
    for raw in _product_results(payload):
        candidate_title = str(raw.get("product_title") or "")
        normalized_candidate = unicodedata.normalize("NFKD", candidate_title).encode("ascii", "ignore").decode().lower()
        candidate_tokens = set(re.findall(r"[a-z0-9]{3,}", normalized_candidate)) - stopwords
        overlap = source_set & candidate_tokens
        if len(overlap) < 2:
            continue
        similarity = len(overlap) / max(1, min(len(source_set), len(candidate_tokens)))
        brand_match = bool(source_tokens and source_tokens[0] in candidate_tokens)
        if similarity < 0.4 and not brand_match:
            continue
        inferred = category or _category(
            raw.get("first_level_category_name") or raw.get("second_level_category_name"),
            candidate_title,
        )
        product = _normalize_product(raw, inferred)
        if product:
            product["source_metadata"]["metadata_source"] = "aliexpress_affiliate_api_search"
            product["resolved_url"] = product.get("product_url")
            matches.append((similarity + (0.25 if brand_match else 0), product))
    return max(matches, key=lambda item: item[0])[1] if matches else None


def _official_product_data(url, platform, category=None):
    if platform == "amazon":
        match = ASIN_RE.search(urlsplit(url).path)
        if match:
            from amazon_creators import get_item_by_asin
            marketplace = urlsplit(url).hostname or "www.amazon.com"
            product = get_item_by_asin(match.group(1), category, marketplace)
            if product:
                product["resolved_url"] = product.get("product_url") or url
                return product
    if platform == "aliexpress":
        match = ALIEXPRESS_ITEM_RE.search(urlsplit(url).path)
        if match:
            return _aliexpress_product_data(match.group(1), category)
    return None


def enrich_product(product):
    """Enriquece desde la página pública de la URL, sin APIs de búsqueda de marketplaces."""
    url = str(product.get("product_url") or "").strip()
    platform = product.get("platform")
    if platform not in ALLOWED_HOSTS or not url:
        return product
    metadata = dict(product.get("source_metadata") or {})
    if not product.get("category"):
        inferred_category = _category(None, product.get("title"))
        if inferred_category:
            product["category"] = inferred_category
            metadata["category_source"] = "title_inference"
    title = str(product.get("title") or "").strip()
    complete = bool(title and not title.casefold().startswith("producto ") and product.get("price") and product.get("image_url") and product.get("category"))
    if complete and metadata.get("enrichment_version") == ENRICHMENT_VERSION:
        metadata["enrichment_status"] = "complete"
        product["source_metadata"] = metadata
        return product
    try:
        page = _public_product_page(url, platform)
        resolved_url = page.get("resolved_url") or url
        for field, key in (("title", "title"), ("price", "price"), ("previous_price", "previous_price"), ("currency", "currency"), ("image_url", "image_url"), ("description", "description"), ("rating", "rating"), ("reviews_count", "reviews_count"), ("category", "category")):
            current = product.get(field)
            missing_title = field == "title" and (not current or str(current).casefold().startswith("producto "))
            if (current is None or current == "" or missing_title) and page.get(key) is not None:
                product[field] = page[key]
        product["gallery_urls"] = list(dict.fromkeys((product.get("gallery_urls") or []) + (page.get("gallery_urls") or [])))[:10]
        if not product.get("image_url") and product["gallery_urls"]:
            product["image_url"] = product["gallery_urls"][0]
        if not product.get("seller") and page.get("seller"):
            product["seller"] = page["seller"]
        original_url = metadata.get("original_url") or url
        product["product_url"] = resolved_url
        metadata.update({
            "original_url": original_url,
            "resolved_url": resolved_url,
            "metadata_source": "public_product_page" if any(page.get(key) for key in ("title", "price", "image_url", "description")) else metadata.get("metadata_source", "url_only"),
            "enrichment_status": "complete" if product.get("title") and not str(product["title"]).casefold().startswith("producto ") and product.get("price") and product.get("image_url") else "partial",
            "enrichment_version": ENRICHMENT_VERSION,
            "enriched_at": datetime.now(timezone.utc).isoformat(),
        })
        metadata.pop("network_enrichment_disabled", None)
        metadata.pop("enrichment_error", None)
        metadata.pop("enrichment_retry_after", None)
    except Exception as exc:
        metadata.update({
            "original_url": metadata.get("original_url") or url,
            "enrichment_status": "partial",
            "enrichment_version": ENRICHMENT_VERSION,
            "enrichment_error": f"{type(exc).__name__}: {str(exc)[:180]}",
            "enrichment_retry_after": (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat(),
        })
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
    missing = []
    if not product.get("title") or str(product.get("title")).casefold().startswith("producto "):
        missing.append("title")
    if not product.get("price"):
        missing.append("price")
    if not product.get("category"):
        missing.append("category")
    if not product.get("image_url"):
        missing.append("image_url")
    metadata["missing_fields"] = missing
    product["source_metadata"] = metadata
    return product

def _retry_due(metadata):
    if (metadata or {}).get("enrichment_version") != ENRICHMENT_VERSION:
        return True
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
    return enrich_product(product)


def enrich_pending_products(conn, limit=80, max_workers=4, force=False):
    with conn.cursor() as cur:
        cur.execute("""SELECT p.id,p.external_id,p.title,p.current_price,p.currency,p.image_url,p.image_gallery,p.rating,p.reviews_count,p.product_url,p.affiliate_url,pl.name AS platform,c.name AS category,p.source_metadata,s.external_id AS seller_external_id,s.name AS seller_name
            FROM products p JOIN platforms pl ON pl.id=p.platform_id LEFT JOIN categories c ON c.id=p.category_id LEFT JOIN sellers s ON s.id=p.seller_id
            WHERE p.is_active=TRUE AND p.product_url IS NOT NULL AND (p.current_price IS NULL OR p.image_url IS NULL OR p.title LIKE 'Producto %%' OR p.category_id IS NULL OR p.seller_id IS NULL)
            ORDER BY p.updated_at ASC LIMIT %s""", (max(limit, limit * 5),))
        candidates = cur.fetchall()
    rows = [row for row in candidates if force or _retry_due(row.get("source_metadata") or {})][:limit]
    if not rows:
        return 0
    with ThreadPoolExecutor(max_workers=max(1, min(int(max_workers), len(rows)))) as executor:
        enriched_rows = list(executor.map(_enrich_pending_row, rows))
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
                json.dumps(metadata), row["id"],
            ))
        if metadata.get("metadata_source") in {"public_product_page", "amazon_creators_api", "aliexpress_affiliate_api"} and any(enriched.get(key) for key in ("price", "image_url")):
            recovered += 1
    conn.commit()
    return recovered
