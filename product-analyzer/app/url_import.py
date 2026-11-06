"""URL import: resolves shortened links, detects platform and builds product dict."""

from __future__ import annotations
import hashlib
import io
import json
import logging
import math
import re
import zipfile
from typing import Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

AMAZON_DOMAINS = (
    "amazon.com", "amazon.ca", "amazon.com.mx", "amazon.co.uk", "amazon.de",
    "amazon.es", "amazon.fr", "amazon.it", "amazon.co.jp", "amazon.com.au",
    "amazon.in", "amazon.nl", "amazon.se", "amazon.pl", "amazon.ae",
    "amazon.sg", "amazon.com.tr", "amazon.com.br",
)
AMAZON_SHORTLINKS = {"amzn.to", "amzn.eu", "amzn.asia", "amzn.com", "amzn.ca", "amzn.de", "amzn.co.uk", "a.co", "link.amazon"}
ALIEXPRESS_DOMAINS = ("aliexpress.com", "aliexpress.us", "aliexpress.ru")
ALIEXPRESS_SHORTLINKS = {"s.click.aliexpress.com", "click.aliexpress.com", "alii.pub"}
ASIN_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})(?:[/?]|$)", re.I)
ALIEXPRESS_ITEM_RE = re.compile(r"/item/(\d+)(?:\.html)?", re.I)

MAX_ZIP_SIZE = 5 * 1024 * 1024  # 5 MB


def _host_matches(host: str, domains) -> bool:
    return any(host in {domain, "www." + domain, "m." + domain, "smile." + domain} for domain in domains)


def detect_platform(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https":
        raise ValueError("La URL del producto debe usar HTTPS.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("La URL contiene un puerto no válido.") from exc
    if parsed.username or parsed.password or port not in (None, 80, 443):
        raise ValueError("La URL contiene credenciales o un puerto no permitido.")
    if _host_matches(host, AMAZON_DOMAINS) or host in AMAZON_SHORTLINKS:
        return "amazon"
    if _host_matches(host, ALIEXPRESS_DOMAINS) or host in ALIEXPRESS_SHORTLINKS:
        return "aliexpress"
    raise ValueError("La URL debe pertenecer a Amazon o AliExpress mediante un dominio oficial o enlace corto oficial.")

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
    match_digits = re.search(r"(\d{8,})", parsed.path + parsed.query)
    if match_digits:
        return match_digits.group(1)
    return "URL-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:24].upper()


def import_url(
    url: str,
    category: Optional[str] = None,
    title: Optional[str] = None,
    price: Optional[float] = None,
    currency: Optional[str] = None,
    image_url: Optional[str] = None,
    external_id: Optional[str] = None,
) -> dict:
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("La URL debe comenzar con https://.")

    platform = detect_platform(url)
    resolved_url = url

    supplied_id = str(external_id or "").strip()
    external_id = supplied_id[:150] if supplied_id else _external_id(resolved_url, platform)
    raw_title = (title or "").strip()
    final_title = raw_title or f"Producto {platform.title()} {external_id}"
    final_image = (image_url or "").strip() or None
    final_price = price
    final_currency = (currency or "").strip().upper()[:10] or None
    category_value = str(category or "").strip().lower()
    allowed_categories = {
        "ropa", "calzado", "accesorios", "tecnologia", "hogar", "moda",
        "deportes", "belleza", "bienestar", "juguetes", "oficina",
    }
    final_category = category_value if category_value in allowed_categories else None

    if final_price is not None and (
        not math.isfinite(float(final_price)) or float(final_price) <= 0
    ):
        raise ValueError("El precio debe ser un número mayor que cero.")

    if final_image:
        image = urlparse(final_image)
        if image.scheme not in ("http", "https") or not image.hostname:
            raise ValueError("La imagen debe ser una URL HTTP o HTTPS válida.")

    missing_fields = []
    if not raw_title:
        missing_fields.append("title")
    if final_price is None:
        missing_fields.append("price")
    if not final_category:
        missing_fields.append("category")
    if not final_image:
        missing_fields.append("image_url")

    return {
        "platform": platform,
        "external_id": external_id,
        "title": final_title[:500],
        "category": final_category,
        "image_url": final_image,
        "product_url": resolved_url or url,
        "price": final_price,
        "currency": final_currency,
        "rating": None,
        "reviews_count": None,
        "gallery_urls": [final_image] if final_image else [],
        "sales_estimate": None,
        "source_metadata": {
            "import_method": "authorized_cli_url",
            "metadata_source": "cli" if title or price is not None or image_url or final_category or currency else "url_only",
            "manual_fields": [field for field, supplied in (("title", title), ("price", price), ("image_url", image_url), ("currency", currency), ("category", final_category)) if supplied is not None and supplied != ""],
            "category_source": "user" if final_category else None,
            "original_url": url,
            "resolved_url": resolved_url or url,
            "gallery_urls": [final_image] if final_image else [],
            "missing_fields": missing_fields,
            "field_sources": {
                key: "user" for key, supplied in (
                    ("title", bool(raw_title)),
                    ("price", final_price is not None),
                    ("currency", bool(currency)),
                    ("image_url", bool(final_image)),
                    ("category", bool(final_category)),
                ) if supplied
            },
        },
    }
