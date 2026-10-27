import hashlib
import re
from urllib.parse import urlparse

AMAZON_HOST_RE = re.compile(r"(^|\\.)(amazon\\.[a-z.]+|amzn\\.to|amzn\\.com|a\\.co)$", re.I)
ASIN_RE = re.compile(r"(?:/dp/|/gp/product/)([A-Z0-9]{10})(?:[/?]|$)", re.I)
ALIEXPRESS_ITEM_RE = re.compile(r"/item/(\\d+)(?:\\.html)?", re.I)


def detect_platform(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if AMAZON_HOST_RE.search(host):
        return "amazon"
    if (
        host == "aliexpress.com"
        or host.endswith(".aliexpress.com")
        or host == "aliexpress.us"
        or host.endswith(".aliexpress.us")
        or host.endswith(".aliexpress.co.uk")
    ):
        return "aliexpress"
    raise ValueError("La URL debe pertenecer a Amazon o AliExpress.")


def _external_id(url: str, platform: str) -> str:
    parsed = urlparse(url)
    if platform == "amazon":
        match = ASIN_RE.search(parsed.path)
        if match:
            return match.group(1).upper()
    elif platform == "aliexpress":
        match = ALIEXPRESS_ITEM_RE.search(parsed.path)
        if match:
            return match.group(1)
    return "URL-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:24].upper()


def import_url(
    url: str,
    category: str,
    title: str | None = None,
    price: float | None = None,
    currency: str = "USD",
    image_url: str | None = None,
) -> dict:
    """Registra enlaces proporcionados por el usuario o por los CLI autorizados.

    No descarga páginas, no sigue enlaces cortos y no usa scraping para evitar
    bloqueos. El CLI puede enviar título, precio, moneda e imagen junto con la URL.
    """
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("La URL debe comenzar con http:// o https://.")

    platform = detect_platform(url)
    external_id = _external_id(url, platform)
    final_title = (title or "").strip() or f"Producto {platform.title()} {external_id}"
    final_image = (image_url or "").strip() or None
    final_price = price if price is not None else None
    final_currency = (currency or "USD").strip().upper()[:10]

    if final_image and urlparse(final_image).scheme not in ("http", "https"):
        raise ValueError("La imagen debe ser una URL HTTP o HTTPS.")

    return {
        "platform": platform,
        "external_id": external_id,
        "title": final_title[:500],
        "image_url": final_image,
        "product_url": url,
        "price": final_price,
        "currency": final_currency or "USD",
        "rating": None,
        "reviews_count": 0,
        "sales_estimate": None,
        "source_metadata": {
            "import_method": "authorized_cli_url",
            "metadata_source": "cli" if title or price is not None or image_url else "url_only",
            "original_url": url,
            "resolved_url": url,
            "gallery_urls": [final_image] if final_image else [],
        },
    }
