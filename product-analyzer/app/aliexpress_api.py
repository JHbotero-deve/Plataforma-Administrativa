import os
import re
import html
import requests
from typing import Any

def search_aliexpress(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Busca productos en AliExpress usando búsqueda web pública con respaldo Bing."""
    try:
        response = requests.get(
            "https://www.bing.com/search",
            params={"q": f'site:aliexpress.com/item "{query}"', "count": min(limit * 2, 20)},
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36",
                "Accept-Language": "es-ES,es;q=0.9,en;q=0.7",
            },
            timeout=20,
        )
        response.raise_for_status()
        content = response.text
        results = []
        seen = set()

        links = re.findall(r'href="(https://[a-zA-Z0-9.-]*aliexpress\.com/item/[0-9]+\.html[^"]*)"[^>]*>(.*?)</a>', content, re.I | re.S)
        for url, raw_title in links:
            item_match = re.search(r'/item/([0-9]+)\.html', url)
            if not item_match:
                continue
            item_id = item_match.group(1)
            if item_id in seen:
                continue
            seen.add(item_id)

            title = re.sub(r"<[^>]+>", " ", raw_title)
            title = re.sub(r"\s+", " ", html.unescape(title)).strip()
            if not title or len(title) < 5:
                continue

            results.append({
                "external_id": f"AE-{item_id}",
                "title": title[:500],
                "product_url": url.split("?")[0],
                "price": None,
                "currency": "USD",
                "image_url": f"https://ae01.alicdn.com/kf/S{item_id}.jpg",
                "platform": "aliexpress",
            })
            if len(results) >= limit:
                break
        return results
    except Exception as exc:
        print(f"[aliexpress] search error: {exc}")
        return []
