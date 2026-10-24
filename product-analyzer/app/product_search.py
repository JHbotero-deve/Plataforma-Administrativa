"""Real external product discovery for Central's Telegram bot."""
from __future__ import annotations

import html
import re
from urllib.parse import urlparse

import requests

from amazon_api import search_amazon_products

BING_SEARCH_URL = "https://www.bing.com/search"
SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


def _plain_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _parse_aliexpress_search_page(page: str, limit: int = 8) -> list[dict]:
    """Parse live public search results; require a direct product URL, title, and price."""
    blocks = re.split(r"(?i)(?=<li\b[^>]*class=[\"'][^\"']*\bb_algo\b)", page or "")
    if len(blocks) == 1:
        blocks = [page or ""]
    results, seen = [], set()
    for block in blocks:
        link = re.search(
            r"href=[\"'](https?://(?:[a-z0-9-]+\.)?aliexpress\.(?:com|us)/item/[^\"']+)[\"']",
            block, re.IGNORECASE,
        )
        if not link:
            continue
        raw_url = html.unescape(link.group(1)).replace("&amp;", "&")
        parsed = urlparse(raw_url)
        if not parsed.hostname or not (
            parsed.hostname.endswith("aliexpress.com") or parsed.hostname.endswith("aliexpress.us")
        ):
            continue
        product_path = parsed.path.rstrip("/")
        if not re.search(r"/item/[^/]+", product_path, re.IGNORECASE):
            continue
        product_url = f"https://{parsed.hostname}{product_path}"
        if product_url.lower() in seen:
            continue
        title_match = re.search(
            r"<h2\b[^>]*>[\s\S]*?<a\b[^>]*>([\s\S]*?)</a>[\s\S]*?</h2>",
            block, re.IGNORECASE,
        )
        title = _plain_text(title_match.group(1)) if title_match else ""
        title = re.sub(r"\s*[-|]\s*AliExpress\s*$", "", title, flags=re.IGNORECASE)
        price_match = re.search(r"(?:US\s*)?\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", block, re.IGNORECASE)
        if not title or not price_match:
            continue
        try:
            price = float(price_match.group(1).replace(",", ""))
        except ValueError:
            continue
        if price <= 0:
            continue
        id_match = re.search(r"/item/(\d+)", product_path, re.IGNORECASE)
        image_match = re.search(
            r"(?:src|data-src)=[\"'](https?://[^\"']+(?:alicdn|aliexpress)[^\"']+)[\"']",
            block, re.IGNORECASE,
        )
        results.append({
            "platform": "aliexpress",
            "external_id": id_match.group(1) if id_match else product_path.rsplit("/", 1)[-1],
            "title": title[:500],
            "product_url": product_url,
            "affiliate_url": product_url,
            "image_url": html.unescape(image_match.group(1)) if image_match else "",
            "price": price,
            "currency": "USD",
            "rating": None,
            "reviews_count": 0,
            "sales_estimate": None,
            "source_metadata": {"import_method": "public_search", "metadata_source": "bing_aliexpress_listing", "search_query": ""},
        })
        seen.add(product_url.lower())
        if len(results) >= max(1, min(int(limit), 10)):
            break
    return results


def search_aliexpress_products(term: str, limit: int = 3) -> list[dict]:
    """Query live AliExpress listings via public web search, without fabricated products."""
    response = requests.get(
        BING_SEARCH_URL,
        params={"q": f'site:aliexpress.com/item/ "{term}" price', "count": min(max(limit * 3, 10), 30)},
        headers=SEARCH_HEADERS,
        timeout=20,
    )
    response.raise_for_status()
    products = _parse_aliexpress_search_page(response.text, limit)
    for product in products:
        product["source_metadata"]["search_query"] = term[:200]
    print(f"[product-search] aliexpress query={term!r} results={len(products)}", flush=True)
    return products


def _valid_product(item: dict, platform: str) -> dict | None:
    if not isinstance(item, dict):
        return None
    title = str(item.get("title") or "").strip()
    product_url = str(item.get("product_url") or item.get("detailPageURL") or "").strip()
    try:
        price = float(item.get("price") or item.get("current_price") or 0)
    except (TypeError, ValueError):
        return None
    parsed = urlparse(product_url)
    if not title or price <= 0 or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    product = dict(item)
    product.update({
        "platform": platform,
        "title": title[:500],
        "product_url": product_url,
        "affiliate_url": str(item.get("affiliate_url") or product_url),
        "price": price,
        "currency": str(item.get("currency") or ("USD" if platform in {"amazon", "aliexpress"} else "COP")),
        "image_url": str(item.get("image_url") or ""),
        "rating": item.get("rating"),
        "reviews_count": item.get("reviews_count") or 0,
        "sales_estimate": item.get("sales_estimate"),
    })
    product.setdefault("external_id", product_url.rstrip("/").rsplit("/", 1)[-1])
    return product


def search_external_products(term: str, limit: int = 8) -> list[dict]:
    """Query each provider independently and combine verified, deduplicated results."""
    term = str(term or "").strip()
    if not term:
        return []
    per_source = max(1, min(6, (max(1, int(limit)) + 1) // 2))
    providers = (
        ("amazon", lambda: search_amazon_products(term, limit=per_source, category="otros")),
        ("aliexpress", lambda: search_aliexpress_products(term, limit=per_source)),
    )
    combined, seen_urls, seen_titles = [], set(), set()
    for platform, search in providers:
        try:
            candidates = search() or []
        except Exception as exc:
            print(f"[product-search] source={platform} failed={type(exc).__name__}: {str(exc)[:160]}", flush=True)
            continue
        accepted = 0
        for candidate in candidates:
            product = _valid_product(candidate, platform)
            if not product:
                continue
            url_key = product["product_url"].split("?", 1)[0].rstrip("/").lower()
            title_key = " ".join(product["title"].lower().split())
            if url_key in seen_urls or title_key in seen_titles:
                continue
            seen_urls.add(url_key)
            seen_titles.add(title_key)
            combined.append(product)
            accepted += 1
            if accepted >= per_source:
                break
        print(f"[product-search] source={platform} query={term!r} results={accepted}", flush=True)
    return combined[:max(1, min(int(limit), 12))]
