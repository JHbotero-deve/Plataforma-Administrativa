import html
import ipaddress
import json
import re
import socket
from urllib.parse import urlparse

import requests

from amazon_api import creators_api_configured, fetch_amazon_product_by_asin

AMAZON_HOST_RE = re.compile(r"(^|\.)amazon\.[a-z.]+$", re.I)
TIKTOK_HOST_RE = re.compile(r"(^|\.)tiktok(?:shop)?\.com$", re.I)
ALIEXPRESS_HOST_RE = re.compile(r"(^|\.)aliexpress\.[a-z.]+$", re.I)
ASIN_RE = re.compile(r"(?:/dp/|/gp/product/)([A-Z0-9]{10})(?:[/?]|$)", re.I)
BLOCKED_HOSTS = {"localhost", "metadata.google.internal", "host.docker.internal"}


def _public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("La URL debe comenzar con http:// o https://.")
    host = parsed.hostname.lower().rstrip(".")
    if host in BLOCKED_HOSTS or host.endswith(".local"):
        raise ValueError("La URL no apunta a un origen público permitido.")
    try:
        addresses = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("No fue posible resolver el dominio de la URL.") from exc
    for address in addresses:
        if not ipaddress.ip_address(address[4][0]).is_global:
            raise ValueError("La URL no apunta a un origen público permitido.")


def detect_platform(url: str) -> str:
    parsed = urlparse((url or "").strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in ("http", "https") or not host:
        raise ValueError("Envía una URL pública completa que comience con https:// o http://.")
    if re.search(r"(^|\.)mercadolibre\.com(\.[a-z]{2,3})?$", host) or re.search(r"(^|\.)mercadolivre\.com\.br$", host):
        raise ValueError("Mercado Libre ya no está habilitado como fuente de Central. Envía una URL de Amazon, AliExpress, TikTok Shop o una tienda propia.")
    if host == "a.co" or AMAZON_HOST_RE.search(host):
        return "amazon"
    if TIKTOK_HOST_RE.search(host):
        return "tiktok"
    if host in {"s.click.aliexpress.com", "a.aliexpress.com"} or ALIEXPRESS_HOST_RE.search(host):
        return "aliexpress"
    return "personal"


def _aliexpress_affiliate_url(url: str) -> str | None:
    """Conserva los enlaces cortos promocionales; no confunde una URL normal con una afiliada."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host in {"s.click.aliexpress.com", "a.aliexpress.com"}:
        return url.strip()
    if ALIEXPRESS_HOST_RE.search(host) and re.search(r"(?:^|&)(?:aff_[a-z0-9_]+|aff_platform|aff_trace_key|terminal_id)=", parsed.query, re.I):
        return url.strip()
    return None


def _asin(url: str) -> str | None:
    match = ASIN_RE.search(urlparse(url).path)
    return match.group(1).upper() if match else None


def _jsonld(content: str) -> list[dict]:
    nodes = []
    for match in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', content, re.I | re.S):
        try:
            data = json.loads(html.unescape(match.group(1)))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        values = data if isinstance(data, list) else [data]
        for value in values:
            if not isinstance(value, dict):
                continue
            nodes.append(value)
            graph = value.get("@graph")
            if isinstance(graph, list):
                nodes.extend(node for node in graph if isinstance(node, dict))
    return nodes


def _product_jsonld(content: str) -> dict:
    for node in _jsonld(content):
        types = node.get("@type")
        types = types if isinstance(types, list) else [types]
        if not any(str(value).lower() == "product" for value in types):
            continue
        offers = node.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        rating = node.get("aggregateRating") or {}
        seller = offers.get("seller") or node.get("seller") or {}
        brand = node.get("brand")
        return {
            "description": node.get("description"),
            "sku": node.get("sku") or node.get("mpn"),
            "brand": brand.get("name") if isinstance(brand, dict) else brand,
            "price": offers.get("price"),
            "currency": offers.get("priceCurrency"),
            "availability": offers.get("availability"),
            "rating": rating.get("ratingValue"),
            "reviews_count": rating.get("reviewCount"),
            "seller": seller.get("name") if isinstance(seller, dict) else seller,
            "seller_url": seller.get("url") if isinstance(seller, dict) else None,
        }
    return {}


def _gallery_from_content(content: str) -> list[str]:
    urls = []
    for node in _jsonld(content):
        images = node.get("image")
        if isinstance(images, str):
            images = [images]
        if isinstance(images, list):
            urls.extend(x for x in images if isinstance(x, str))
    return list(dict.fromkeys(x.strip() for x in urls if re.match(r"^https?://", x.strip())))[:24]


def _parse_price(content: str) -> tuple[float | None, str | None]:
    for pattern, currency in (
        (r'"priceCurrency"\s*:\s*"([A-Z]{3})".{0,500}"price"\s*:\s*"?([0-9]+(?:\.[0-9]+)?)', None),
        (r'"price"\s*:\s*"?([0-9]+(?:\.[0-9]+)?)".{0,500}"priceCurrency"\s*:\s*"([A-Z]{3})"', None),
    ):
        match = re.search(pattern, content, re.I | re.S)
        if match:
            if currency is None:
                first, second = match.groups()
                if len(first) == 3:
                    currency, amount = first.upper(), second
                else:
                    amount, currency = first, second.upper()
            try:
                value = float(amount)
                if value > 0:
                    return value, currency
            except (TypeError, ValueError):
                pass
    marker = re.search(r'id=["\']corePriceDisplay_desktop_feature_div["\']', content, re.I)
    if marker:
        block = content[marker.start():marker.start() + 30000]
        match = re.search(
            r'a-price-symbol[^>]*>\s*([^<]+).*?a-price-whole[^>]*>\s*([0-9][0-9,]*).*?a-price-fraction[^>]*>\s*([0-9]{1,2})',
            block,
            re.I | re.S,
        )
        if match:
            symbol, whole, fraction = match.groups()
            code = {"$": "USD", "€": "EUR", "£": "GBP"}.get(symbol.strip())
            if code:
                return float(f"{whole.replace(',', '')}.{fraction}"), code
    return None, None


def _metadata(url: str) -> dict:
    _public_url(url)
    response = requests.get(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; CentralProductRadar/2.0)",
            "Accept-Language": "es-CO,es;q=0.9,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        },
        timeout=20,
        allow_redirects=True,
    )
    response.raise_for_status()
    _public_url(response.url)
    content = response.text[:2_000_000]
    result = {"resolved_url": response.url, "gallery_urls": _gallery_from_content(content)}
    def meta_value(*keys):
        wanted = {key.casefold() for key in keys}
        for tag in re.findall(r"<meta\b[^>]*>", content, re.I):
            attrs = {
                match.group(1).lower(): html.unescape(match.group(3)).strip()
                for match in re.finditer(r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(["'])(.*?)\2""", tag, re.S)
            }
            property_name = (attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").casefold()
            if property_name in wanted and attrs.get("content"):
                return attrs["content"]
        return None
    result["price"] = meta_value("product:price:amount", "og:price:amount", "price")
    result["currency"] = meta_value("product:price:currency", "og:price:currency", "priceCurrency")
    result["image_url"] = meta_value("og:image", "twitter:image", "image")
    result["title"] = meta_value("og:title", "twitter:title")
    result["description"] = meta_value("og:description", "twitter:description")
    result["sku"] = meta_value("sku", "product:retailer_item_id", "product:sku")
    result["gtin"] = meta_value("gtin", "gtin8", "gtin12", "gtin13", "gtin14", "product:gtin")
    result["brand"] = meta_value("brand", "product:brand")
    result["availability"] = meta_value("availability", "product:availability")
    result["previous_price"] = meta_value("product:price:original_amount", "og:price:original_amount", "price:original")
    result["seller"] = meta_value("seller", "product:seller", "og:site_name")
    patterns = {
        "title": r'<meta[^>]+(?:property|name)=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']|<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:title["\']',
        "image_url": r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']|<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:image["\']',
        "description": r'<meta[^>]+(?:property|name)=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']|<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:description["\']',
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, content, re.I)
        if match:
            result[key] = html.unescape(match.group(1) or match.group(2)).strip()
    jsonld = _product_jsonld(content)
    result.update({k: v for k, v in jsonld.items() if v not in (None, "", [])})
    result["gtin"] = result.get("gtin") or next(
        (node.get(key) for node in _jsonld(content) if str(node.get("@type", "")).lower() == "product"
         for key in ("gtin", "gtin8", "gtin12", "gtin13", "gtin14") if node.get(key)),
        None,
    )
    offers = next(
        (node.get("offers") for node in _jsonld(content)
         if "product" in [str(value).lower() for value in (node.get("@type") if isinstance(node.get("@type"), list) else [node.get("@type")])]),
        {},
    )
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if isinstance(offers, dict):
        result["previous_price"] = result.get("previous_price") or offers.get("priceSpecification", {}).get("price") if isinstance(offers.get("priceSpecification"), dict) else result.get("previous_price")
    if result.get("price") is None:
        result["price"], result["currency"] = _parse_price(content)
    if not result.get("title"):
        match = re.search(r"<title[^>]*>(.*?)</title>", content, re.I | re.S)
        if match:
            result["title"] = html.unescape(re.sub(r"\s+", " ", match.group(1))).strip()
    return result


def import_url(
    url: str,
    category: str,
    title: str | None = None,
    price: float | None = None,
    currency: str = "USD",
    image_url: str | None = None,
    require_image: bool = False,
) -> dict:
    url = url.strip()
    _public_url(url)
    platform = detect_platform(url)
    asin = _asin(url) if platform == "amazon" else None
    metadata = None
    warnings = []

    # La API oficial de Amazon es preferida cuando está disponible, pero no
    # bloquea la importación por URL si devuelve error: se intenta leer el HTML
    # público y sus metadatos Open Graph / JSON-LD como alternativa.
    if platform == "amazon" and asin and creators_api_configured():
        try:
            amazon_product = fetch_amazon_product_by_asin(asin, category)
            if amazon_product:
                metadata = {
                    "resolved_url": amazon_product["product_url"],
                    "title": amazon_product["title"],
                    "description": amazon_product.get("description"),
                    "sku": asin,
                    "brand": amazon_product.get("brand"),
                    "price": amazon_product.get("price"),
                    "currency": amazon_product.get("currency"),
                    "image_url": amazon_product.get("image_url"),
                    "gallery_urls": amazon_product.get("gallery_urls") or [],
                    "rating": amazon_product.get("rating"),
                    "reviews_count": amazon_product.get("reviews_count"),
                    "seller": (amazon_product.get("seller") or {}).get("name"),
                    "metadata_source": "amazon_creators_api",
                }
            else:
                warnings.append("Amazon Creators API no devolvió datos del producto.")
        except (requests.RequestException, RuntimeError, ValueError) as exc:
            warnings.append(f"Amazon Creators API no disponible: {str(exc)[:160]}")

    if metadata is None:
        try:
            metadata = _metadata(url)
            if warnings:
                metadata["import_warning"] = " ".join(warnings)
                metadata["metadata_source"] = metadata.get("metadata_source") or "public_page_fallback"
        except (requests.RequestException, RuntimeError) as exc:
            warnings.append(f"No se pudieron leer los metadatos de la página: {str(exc)[:160]}")
            metadata = {
                "resolved_url": url,
                "gallery_urls": [],
                "metadata_source": "manual_draft",
                "import_warning": " ".join(warnings),
            }
    resolved_url = metadata.get("resolved_url") or url
    external_id = _asin(resolved_url) if platform == "amazon" else None
    if not external_id:
        path = urlparse(resolved_url).path
        match = re.search(r"/([A-Z]{2,4}-?\d{6,})", path, re.I)
        external_id = match.group(1).upper() if match else re.sub(r"[^a-zA-Z0-9]+", "-", path.strip("/"))[:120]
    final_title = (title or metadata.get("title") or "").strip()
    if not final_title:
        host = (urlparse(resolved_url).hostname or platform).replace("www.", "")
        final_title = f"Borrador importado de {host}"
    final_image = (image_url or metadata.get("image_url") or "").strip() or None
    final_price = price if price is not None else metadata.get("price")
    final_currency = (currency if price is not None else metadata.get("currency")) or currency
    if not final_image and require_image:
        raise ValueError("No fue posible obtener una imagen real del producto. Desactiva require_image para guardarlo como borrador.")
    gallery = metadata.get("gallery_urls") or []
    if final_image and final_image not in gallery:
        gallery.insert(0, final_image)
    return {
        "platform": platform,
        "external_id": external_id,
        "title": final_title[:500],
        "description": str(metadata.get("description") or "").strip()[:5000] or None,
        "sku": str(metadata.get("sku") or "").strip()[:150] or None,
        "gtin": str(metadata.get("gtin") or "").strip()[:32] or None,
        "brand": str(metadata.get("brand") or "").strip()[:200] or None,
        "previous_price": float(metadata["previous_price"]) if metadata.get("previous_price") not in (None, "") and float(metadata["previous_price"]) > 0 else None,
        "image_url": final_image,
        "gallery_urls": gallery[:24],
        "product_url": url if platform == "amazon" else resolved_url,
        "affiliate_url": (
            _aliexpress_affiliate_url(url) if platform == "aliexpress"
            else url if platform == "amazon" and re.search(r"(?:^|&)tag=", urlparse(url).query, re.I)
            else None
        ),
        "price": float(final_price) if final_price is not None and float(final_price) > 0 else None,
        "currency": str(final_currency).upper()[:10] if final_currency else "USD",
        "rating": float(metadata["rating"]) if metadata.get("rating") not in (None, "") else None,
        "reviews_count": int(float(metadata["reviews_count"])) if metadata.get("reviews_count") not in (None, "") else 0,
        "sales_estimate": None,
        "seller": {"name": metadata.get("seller"), "url": metadata.get("seller_url")} if metadata.get("seller") or metadata.get("seller_url") else {},
        "source_metadata": {
            "import_method": "product_url",
            "gtin": str(metadata.get("gtin") or "").strip() or None,
            "sku": str(metadata.get("sku") or "").strip() or None,
            "seller_url": metadata.get("seller_url"),
            "previous_price": metadata.get("previous_price"),
            "metadata_source": metadata.get("metadata_source") or "open_graph+jsonld",
            "import_warning": metadata.get("import_warning"),
            "draft": not bool(final_image and final_price is not None and float(final_price) > 0),
            "original_url": url,
            "resolved_url": resolved_url,
            "brand": metadata.get("brand"),
            "availability": metadata.get("availability"),
            "gallery_urls": gallery[:24],
        },
    }
