import html
import json
import re
from urllib.parse import urlparse

import requests

















def detect_platform(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if AMAZON_HOST_RE.search(host):
        return "amazon"
    if "mercadolibre." in host or "mercadolivre." in host:
        return "mercadolibre"
    raise ValueError("La URL debe pertenecer a Amazon o Mercado Libre.")


def _asin(url: str) -> str | None:
    match = ASIN_RE.search(urlparse(url).path)
    return match.group(1).upper() if match else None



def _parse_price(content: str) -> tuple[float | None, str | None]:
    """
    Obtiene únicamente el precio principal visible del producto Amazon.

    Prioridad:
    1. Estructura a-price del producto principal.
    2. Formatos antiguos de Amazon.
    3. Si no puede identificarse con seguridad, devuelve None.
    """

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

            if symbol == "$":
                currency = "USD"
            elif symbol == "€":
                currency = "EUR"
            elif symbol == "£":
                currency = "GBP"
            else:
                currency = None

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

        if "$" in text:
            currency = "USD"
        elif "€" in text:
            currency = "EUR"
        elif "£" in text:
            currency = "GBP"
        else:
            continue

        number = re.search(
            r'([0-9][0-9,]*(?:\.[0-9]{1,2})?)',
            text,
        )

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
    for match in re.finditer(r'<script[^>]+type=["\\\']application/ld\\+json["\\\'][^>]*>(.*?)</script>', content, re.I | re.S):
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

def import_url(url: str, category: str, title: str | None = None,
               price: float | None = None, currency: str = "USD",
               image_url: str | None = None) -> dict:
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("La URL debe comenzar con http:// o https://.")

    platform = detect_platform(url)
    metadata = _metadata(url)

    resolved_url = metadata.get("resolved_url") or url
    external_id = _asin(resolved_url) if platform == "amazon" else None
    if not external_id:
        path_id = re.search(r"/([A-Z]{2,4}-?[0-9]{6,})", parsed.path, re.I)
        external_id = path_id.group(1).upper() if path_id else re.sub(r"[^a-zA-Z0-9]+", "-", parsed.path.strip("/"))[:120]

    if not external_id:
        raise ValueError("No fue posible identificar el producto en la URL.")

    final_title = (title or metadata.get("title") or f"Producto {platform.title()} {external_id}").strip()
    final_image = (image_url or metadata.get("image_url") or "").strip() or None
    final_price = price if price is not None else metadata.get("price")
    final_currency = metadata.get("currency") or currency

    return {
        "platform": platform,
        "external_id": external_id,
        "title": final_title[:500],
        "image_url": final_image,
        "product_url": resolved_url,
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
            "gallery_urls": metadata.get("gallery_urls") or ([final_image] if final_image else []),
        },
    }
