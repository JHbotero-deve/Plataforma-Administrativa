import html
import re
from urllib.parse import urlparse

import requests

ASIN_RE = re.compile(r"(?:/dp/|/gp/product/|/product/)([A-Z0-9]{10})(?:[/?]|$)", re.I)
AMAZON_HOST_RE = re.compile(r"(^|\.)amazon\.[a-z.]+$", re.I)


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


def _metadata(url: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; CentralProductRadar/1.0)",
        "Accept-Language": "es-CO,es;q=0.9,en;q=0.7",
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
        "image_url": r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, content, re.I)
        if match:
            result[key] = html.unescape(match.group(1)).strip()

    if not result.get("title"):
        match = re.search(r"<title[^>]*>(.*?)</title>", content, re.I | re.S)
        if match:
            result["title"] = html.unescape(re.sub(r"\s+", " ", match.group(1))).strip()

    return result


def import_url(url: str, category: str, title: str | None = None,
               price: float | None = None, currency: str = "USD",
               image_url: str | None = None) -> dict:
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("La URL debe comenzar con http:// o https://.")

    platform = detect_platform(url)
    metadata = _metadata(url)

    resolved_url = metadata.get("resolved_url") or url\n    external_id = _asin(resolved_url) if platform == "amazon" else None
    if not external_id:
        path_id = re.search(r"/([A-Z]{2,4}-?[0-9]{6,})", parsed.path, re.I)
        external_id = path_id.group(1).upper() if path_id else re.sub(r"[^a-zA-Z0-9]+", "-", parsed.path.strip("/"))[:120]

    if not external_id:
        raise ValueError("No fue posible identificar el producto en la URL.")

    final_title = (title or metadata.get("title") or f"Producto {platform.title()} {external_id}").strip()
    final_image = (image_url or metadata.get("image_url") or "").strip() or None

    return {
        "platform": platform,
        "external_id": external_id,
        "title": final_title[:500],
        "image_url": final_image,
        "product_url": resolved_url,
        "price": price,
        "currency": currency.upper()[:10] if currency else "USD",
        "rating": None,
        "reviews_count": 0,
        "sales_estimate": None,
        "source_metadata": {
            "import_method": "product_url",
            "metadata_source": "open_graph" if metadata else "url_only",
            "original_url": url,\n            "resolved_url": resolved_url,
        },
    }
