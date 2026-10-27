import html
import hashlib
import json
import re
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

AMAZON_HOST_RE = re.compile(r"(^|\.)(amazon\.[a-z.]+|amzn\.to|amzn\.com|a\.co)$", re.I)
ASIN_RE = re.compile(r"(?:/dp/|/gp/product/)([A-Z0-9]{10})(?:[/?]|$)", re.I)
ALIEXPRESS_ITEM_RE = re.compile(r"/item/(\d{8,})(?:\.html)?", re.I)


def detect_platform(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if AMAZON_HOST_RE.search(host):
        return "amazon"
    if host == "aliexpress.com" or host.endswith(".aliexpress.com") or host == "aliexpress.us" or host.endswith(".aliexpress.us"):
        return "aliexpress"
    raise ValueError("La URL debe pertenecer a Amazon o AliExpress.")


def _asin(url: str) -> str | None:
    match = ASIN_RE.search(urlparse(url).path)
    return match.group(1).upper() if match else None


def _aliexpress_affiliate_url(source_url: str) -> str | None:
    """Convierte una URL con la API oficial de afiliados de AliExpress."""
    app_key = os.getenv("ALIEXPRESS_APP_KEY", "").strip()
    app_secret = os.getenv("ALIEXPRESS_APP_SECRET", "").strip()
    tracking_id = (os.getenv("ALIEXPRESS_TRACKING_ID") or os.getenv("ALIEXPRESS_AFFILIATE_TRACKING_ID") or "").strip()
    if not (app_key and app_secret and tracking_id):
        return None
    params = {
        "app_key": app_key,
        "method": "aliexpress.affiliate.link.generate",
        "sign_method": "md5",
        "timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
        "format": "json",
        "v": "2.0",
        "promotion_link_type": "0",
        "source_values": source_url,
        "tracking_id": tracking_id,
    }
    app_signature = os.getenv("ALIEXPRESS_APP_SIGNATURE", "").strip()
    if app_signature:
        params["app_signature"] = app_signature
    signature_source = app_secret + "".join(key + str(params[key]) for key in sorted(params)) + app_secret
    params["sign"] = hashlib.md5(signature_source.encode("utf-8")).hexdigest().upper()
    response = requests.post("https://eco.taobao.com/router/rest", data=params, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=20)
    response.raise_for_status()
    payload = response.json()
    def find_link(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"promotion_link", "promotionLink"} and isinstance(child, str) and child.startswith(("https://", "http://")):
                    return child
                found = find_link(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = find_link(child)
                if found:
                    return found
        return None
    link = find_link(payload)
    if not link:
        raise RuntimeError("AliExpress no devolvió un enlace promocional; revisa credenciales, tracking ID y elegibilidad.")
    return link

def _parse_price(content: str) -> tuple[float | None, str | None]:
    """Obtiene el precio principal visible del producto Amazon."""
    content = html.unescape(content)
    marker = re.search(
        r'id=["\']corePriceDisplay_desktop_feature_div["\']',
        content,
        re.I,
    )
    if marker:
        block = content[marker.start():marker.start() + 30000]
        price_match = re.search(
            r'<span[^>]+class=["\'][^"\']*a-price[^"\']*["\'][^>]*>'
            r'.{0,5000}?'
            r'<span[^>]+class=["\'][^"\']*a-price-symbol[^"\']*["\'][^>]*>'
            r'\s*([^<]+?)\s*</span>'
            r'.{0,3000}?'
            r'<span[^>]+class=["\'][^"\']*a-price-whole[^"\']*["\'][^>]*>'
            r'\s*([0-9][0-9,]*)\s*</span>'
            r'.{0,1000}?'
            r'<span[^>]+class=["\'][^"\']*a-price-fraction[^"\']*["\'][^>]*>'
            r'\s*([0-9]{1,2})\s*</span>',
            block,
            re.I | re.S,
        )
        if price_match:
            symbol = price_match.group(1).strip()
            whole = price_match.group(2).replace(",", "")
            fraction = price_match.group(3)
            currency = {"$": "USD", "€": "EUR", "£": "GBP"}.get(symbol)
            if currency:
                try:
                    value = float(f"{whole}.{fraction}")
                    if value > 0:
                        return value, currency
                except ValueError:
                    pass

    for element_id in (
        "priceblock_ourprice",
        "priceblock_dealprice",
        "priceblock_saleprice",
    ):
        match = re.search(
            rf'id=["\']{element_id}["\'][^>]*>\s*([^<]+)',
            content,
            re.I | re.S,
        )
        if not match:
            continue
        text = html.unescape(match.group(1)).strip()
        currency_symbol = "$" if "$" in text else "€" if "€" in text else "£" if "£" in text else None
        currency = {"$": "USD", "€": "EUR", "£": "GBP"}.get(currency_symbol)
        if not currency:
            continue
        number = re.search(r'([0-9][0-9,]*(?:\.[0-9]{1,2})?)', text)
        if not number:
            continue
        try:
            value = float(number.group(1).replace(",", ""))
        except ValueError:
            continue
        if value > 0:
            return value, currency
    return None, None


def _metadata(url: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; CentralProductRadar/1.0)",
        "Accept-Language": "es-CO,es;q=0.9,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    }
    try:
        response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"[url-import] metadata unavailable: {exc}")
        return {}

    content = response.text[:2_000_000]
    result = {"resolved_url": response.url}
    patterns = {
        "title": r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        "image_url": r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']|<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:image["\']',
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, content, re.I)
        if match:
            value = match.group(1) or match.group(2)
            result[key] = html.unescape(value).strip()

    price, price_currency = _parse_price(content)
    if price is not None:
        result["price"] = price
    if price_currency:
        result["currency"] = price_currency
    result["gallery_urls"] = _gallery_from_content(content)

    if not result.get("title"):
        match = re.search(r"<title[^>]*>(.*?)</title>", content, re.I | re.S)
        if match:
            result["title"] = html.unescape(re.sub(r"\s+", " ", match.group(1))).strip()
    return result


def _gallery_from_content(content: str) -> list[str]:
    urls = []
    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        content,
        re.I | re.S,
    ):
        try:
            data = json.loads(html.unescape(match.group(1)))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            images = node.get("image")
            if isinstance(images, str):
                images = [images]
            if isinstance(images, list):
                urls.extend(x for x in images if isinstance(x, str))
    return list(dict.fromkeys(x.strip() for x in urls if re.match(r"^https?://", x.strip())))[:24]


def import_url(
    url: str,
    category: str,
    title: str | None = None,
    price: float | None = None,
    currency: str = "USD",
    image_url: str | None = None,
) -> dict:
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("La URL debe comenzar con http:// o https://.")

    platform = detect_platform(url)
    # Amazon: no descargar ni raspar la página; importar por URL sin provocar
    # peticiones repetidas al marketplace. Se usan datos aportados o API autorizada.
    metadata = {} if platform == "amazon" else _metadata(url)
    resolved_url = url if platform == "amazon" else (metadata.get("resolved_url") or url)
    external_id = _asin(resolved_url) if platform == "amazon" else None
    if platform == "aliexpress":
        match = ALIEXPRESS_ITEM_RE.search(urlparse(resolved_url).path)
        external_id = "AE-" + match.group(1) if match else None
    if not external_id:
        path_id = re.search(r"/([A-Z]{2,4}-?[0-9]{6,})", parsed.path, re.I)
        external_id = path_id.group(1).upper() if path_id else re.sub(
            r"[^a-zA-Z0-9]+", "-", parsed.path.strip("/")
        )[:120]
    if not external_id:
        raise ValueError("No fue posible identificar el producto en la URL.")

    final_title = (title or metadata.get("title") or f"Producto {platform.title()} {external_id}").strip()
    final_image = (image_url or metadata.get("image_url") or "").strip() or None
    final_price = price if price is not None else metadata.get("price")
    final_currency = metadata.get("currency") or currency
    affiliate_url = resolved_url
    if platform == "amazon" and external_id:
        partner_tag = (os.getenv("AMAZON_PARTNER_TAG") or os.getenv("AMAZON_ASSOCIATE_TAG") or "").strip()
        if partner_tag:
            parts = urlparse(resolved_url)
            query = dict(parse_qsl(parts.query, keep_blank_values=True))
            query["tag"] = partner_tag
            affiliate_url = urlunparse((parts.scheme, parts.netloc, parts.path, parts.params, urlencode(query), parts.fragment))
    elif platform == "aliexpress":
        try:
            affiliate_url = _aliexpress_affiliate_url(resolved_url) or resolved_url
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            print(f"[aliexpress] no se pudo generar el enlace afiliado: {type(exc).__name__}")
            affiliate_url = resolved_url

    return {
        "platform": platform,
        "external_id": external_id,
        "title": final_title[:500],
        "image_url": final_image,
        "product_url": resolved_url,
        "affiliate_url": affiliate_url,
        "price": final_price,
        "currency": str(final_currency).upper()[:10] if final_currency else "USD",
        "rating": None,
        "reviews_count": 0,
        "sales_estimate": None,
        "source_metadata": {
            "import_method": "product_url",
            "metadata_source": "open_graph" if metadata else "url_only",
            "original_url": url,
            "resolved_url": resolved_url,
            "affiliate_link_status": "generated" if affiliate_url != resolved_url else "pending_or_not_configured",
            "gallery_urls": metadata.get("gallery_urls") or ([final_image] if final_image else []),
        },
    }
