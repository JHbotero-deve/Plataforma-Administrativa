import hashlib
import re
from html import unescape
from urllib.parse import urlparse

import requests

from db import get_connection, upsert_product

PLATFORM_DOMAINS = {
    "amazon": ("amazon.", "amzn.to"),
    "mercadolibre": ("mercadolibre.", "mercadolivre."),
    "tiktok": ("tiktok.com", "tiktokshop."),
}


def detect_platform(url: str) -> str:
    host = urlparse(url).netloc.lower()
    for platform, domains in PLATFORM_DOMAINS.items():
        if any(domain in host for domain in domains):
            return platform
    return "manual"


def _meta(html: str, key: str) -> str | None:
    patterns = [
        rf'<meta[^>]+property=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']{re.escape(key)}["\']',
        rf'<meta[^>]+name=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.I)
        if match:
            return unescape(match.group(1)).strip()
    return None


def import_product_url(url: str, category: str = "accesorios", title: str | None = None, notes: str | None = None):
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("La URL debe comenzar con http:// o https://")

    platform = detect_platform(url)
    original_url = url
    headers = {"User-Agent": "Mozilla/5.0 CentralProductAnalyzer/2.1", "Accept": "text/html,application/xhtml+xml"}
    html = ""
    try:
        response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        response.raise_for_status()
        html = response.text[:1000000]
        url = response.url
        platform = detect_platform(url)
    except requests.RequestException as exc:
        print(f"[URL CURADA] no se pudo leer metadata: {exc}")

    resolved_title = title or _meta(html, "og:title") or _meta(html, "twitter:title") or parsed.path.rstrip("/").split("/")[-1] or parsed.netloc
    image_url = _meta(html, "og:image") or _meta(html, "twitter:image")
    external_id = "curated:" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:40]

    conn = get_connection()
    try:
        if platform == "manual":
            platform_name = "manual"
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM platforms WHERE name = %s", (platform_name,))
                if not cur.fetchone():
                    cur.execute("INSERT INTO platforms (name, base_url) VALUES (%s,%s) ON CONFLICT (name) DO NOTHING", (platform_name, parsed.scheme + "://" + parsed.netloc))
        product = {
            "external_id": external_id,
            "title": resolved_title[:500],
            "image_url": image_url,
            "product_url": url,
            "price": None,
            "currency": "COP",
            "rating": None,
            "reviews_count": 0,
            "sales_estimate": None,
            "seller": None,
            "source_metadata": {
                "curated": True,
                "original_url": original_url,
                "resolved_url": url,
                "platform_detected": platform,
                "notes": notes,
            },
        }
        product_id = upsert_product(conn, platform, category, product)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO curated_links (product_id, original_url, platform, title, notes, updated_at)
                VALUES (%s,%s,%s,%s,%s,NOW())
                ON CONFLICT (original_url) DO UPDATE SET
                    product_id=EXCLUDED.product_id,
                    platform=EXCLUDED.platform,
                    title=EXCLUDED.title,
                    notes=EXCLUDED.notes,
                    updated_at=NOW()
                RETURNING id
                """,
                (product_id, original_url, platform, resolved_title[:500], notes),
            )
        conn.commit()
        return {"id": product_id, "title": resolved_title[:500], "platform": platform, "product_url": url, "image_url": image_url, "curated": True}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
