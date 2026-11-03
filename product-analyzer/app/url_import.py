import hashlib
import math
import re
from urllib.parse import urlparse

AMAZON_HOST_RE = re.compile(r"(^|\.)(amazon\.[a-z.]+|link\.amazon|amzn\.to|amzn\.com|a\.co)$", re.I)
ALIEXPRESS_HOST_RE = re.compile(r"(^|\.)(aliexpress\.[a-z.]+|aliexpress\.ru|alii\.pub)$", re.I)
ASIN_RE = re.compile(r"(?:/dp/|/gp/product/)([A-Z0-9]{10})(?:[/?]|$)", re.I)
ALIEXPRESS_ITEM_RE = re.compile(r"/item/(\d+)(?:\.html)?", re.I)


def detect_platform(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if AMAZON_HOST_RE.search(host):
        return "amazon"
    if ALIEXPRESS_HOST_RE.search(host):
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
    category: str | None,
    title: str | None = None,
    price: float | None = None,
    currency: str = "USD",
    image_url: str | None = None,
    external_id: str | None = None,
) -> dict:
    """Registra enlaces suministrados por el usuario o por los CLI autorizados.

    No descarga páginas ni sigue enlaces cortos automáticamente. El CLI puede
    enviar título, precio, moneda e imagen junto con la URL.
    """
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("La URL debe comenzar con http:// o https://.")

    platform = detect_platform(url)
    supplied_id = str(external_id or "").strip()
    external_id = supplied_id[:150] if supplied_id else _external_id(url, platform)
    final_title = (title or "").strip() or f"Producto {platform.title()} {external_id}"
    final_image = (image_url or "").strip() or None
    final_price = price if price is not None else None
    final_currency = (currency or "USD").strip().upper()[:10]

    if final_price is not None and (
        not math.isfinite(float(final_price)) or float(final_price) <= 0
    ):
        raise ValueError("El precio debe ser un número mayor que cero.")

    if final_image:
        image = urlparse(final_image)
        if image.scheme not in ("http", "https") or not image.hostname:
            raise ValueError("La imagen debe ser una URL HTTP o HTTPS válida.")

    return {
        "platform": platform,
        "external_id": external_id,
        "title": final_title[:500],
        "category": category if category in {"ropa", "calzado", "accesorios", "tecnologia", "hogar", "moda", "deportes", "belleza", "bienestar", "juguetes", "oficina"} else None,
        "image_url": final_image,
        "product_url": url,
        "price": final_price,
        "currency": final_currency or "USD",
        "rating": None,
        "reviews_count": 0,
        "gallery_urls": [final_image] if final_image else [],
        "sales_estimate": None,
        "source_metadata": {
            "import_method": "authorized_cli_url",
            "metadata_source": "cli" if title or price is not None or image_url else "url_only",
            "category_source": "user" if category in {"ropa", "calzado", "accesorios", "tecnologia", "hogar", "moda", "deportes", "belleza", "bienestar", "juguetes", "oficina"} else None,
            "original_url": url,
            "resolved_url": url,
            "gallery_urls": [final_image] if final_image else [],
        },
    }
