import os
import time
from typing import Any

import requests

BASE_URL = "https://creatorsapi.amazon"
MARKETPLACE = os.getenv("AMAZON_MARKETPLACE", "www.amazon.com")
CREDENTIAL_ID = os.getenv("AMAZON_CREDENTIAL_ID", "").strip()
CREDENTIAL_SECRET = os.getenv("AMAZON_CREDENTIAL_SECRET", "").strip()
PARTNER_TAG = os.getenv("AMAZON_PARTNER_TAG", "").strip()
CREDENTIAL_VERSION = os.getenv("AMAZON_CREDENTIAL_VERSION", "3.1").strip()

_TOKEN: str | None = None
_TOKEN_EXPIRES_AT = 0.0


def _token_endpoints() -> list[str]:
    configured = CREDENTIAL_VERSION[:3]
    endpoints = {
        "3.1": "https://api.amazon.com/auth/o2/token",
        "3.2": "https://api.amazon.co.uk/auth/o2/token",
        "3.3": "https://api.amazon.co.jp/auth/o2/token",
    }
    preferred = endpoints.get(configured, endpoints["3.1"])
    return [preferred] + [url for version, url in endpoints.items() if url != preferred]


def _access_token() -> str:
    global _TOKEN, _TOKEN_EXPIRES_AT
    if _TOKEN and time.time() < _TOKEN_EXPIRES_AT - 120:
        return _TOKEN
    if not CREDENTIAL_ID or not CREDENTIAL_SECRET or not PARTNER_TAG:
        raise RuntimeError("Faltan AMAZON_CREDENTIAL_ID, AMAZON_CREDENTIAL_SECRET o AMAZON_PARTNER_TAG")

    last_error = "sin respuesta"
    for endpoint in _token_endpoints():
        try:
            response = requests.post(
                endpoint,
                headers={"Content-Type": "application/json"},
                json={
                    "grant_type": "client_credentials",
                    "client_id": CREDENTIAL_ID,
                    "client_secret": CREDENTIAL_SECRET,
                    "scope": "creatorsapi::default",
                },
                timeout=20,
            )
            if response.ok:
                data = response.json()
                token = data.get("access_token")
                if token:
                    _TOKEN = token
                    _TOKEN_EXPIRES_AT = time.time() + int(data.get("expires_in", 3600))
                    return token
                last_error = "respuesta sin access_token"
            else:
                try:
                    detail = response.json()
                    detail = detail.get("error_description") or detail.get("error") or str(detail)
                except ValueError:
                    detail = response.text[:300]
                last_error = f"HTTP {response.status_code}: {detail}"
        except requests.RequestException as exc:
            last_error = str(exc)

    raise RuntimeError(f"Amazon OAuth rechazó las credenciales: {last_error}")


def _request(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload)
    payload["marketplace"] = MARKETPLACE
    payload["partnerTag"] = PARTNER_TAG
    response = requests.post(
        f"{BASE_URL}/catalog/v1/{operation}",
        headers={
            "Authorization": f"Bearer {_access_token()}",
            "Content-Type": "application/json",
            "x-marketplace": MARKETPLACE,
        },
        json=payload,
        timeout=30,
    )
    if response.status_code == 401:
        global _TOKEN, _TOKEN_EXPIRES_AT
        _TOKEN = None
        _TOKEN_EXPIRES_AT = 0
        response = requests.post(
            f"{BASE_URL}/catalog/v1/{operation}",
            headers={
                "Authorization": f"Bearer {_access_token()}",
                "Content-Type": "application/json",
                "x-marketplace": MARKETPLACE,
            },
            json=payload,
            timeout=30,
        )
    response.raise_for_status()
    data = response.json()
    if data.get("errors"):
        raise RuntimeError(str(data["errors"]))
    return data


def _first_image(item: dict[str, Any]) -> str | None:
    images = item.get("images") or {}
    primary = images.get("primary") or {}
    for key in ("large", "medium", "small"):
        node = primary.get(key) or {}
        url = node.get("url")
        if url:
            return str(url)
    return None


def _title(item: dict[str, Any]) -> str | None:
    title = ((item.get("itemInfo") or {}).get("title") or {}).get("displayValue")
    return str(title).strip() if title else None


def _offer(item: dict[str, Any]) -> tuple[float | None, str | None]:
    listings = ((item.get("offersV2") or {}).get("listings") or [])
    if not listings:
        return None, None
    price = (listings[0].get("price") or {})
    amount = price.get("amount")
    currency = price.get("currency")
    try:
        return (float(amount), str(currency).upper()) if amount is not None else (None, None)
    except (TypeError, ValueError):
        return None, None


def search_products(keywords: str, limit: int = 10) -> list[dict[str, Any]]:
    data = _request(
        "searchItems",
        {
            "keywords": keywords,
            "searchIndex": "All",
            "itemCount": min(max(limit, 1), 10),
            "sortBy": "Relevance",
            "resources": [
                "images.primary.large",
                "images.primary.medium",
                "itemInfo.title",
                "offersV2.listings.price",
            ],
        },
    )
    return ((data.get("searchResult") or {}).get("items") or [])


def _map_item(item: dict[str, Any], category: str) -> dict[str, Any] | None:
    asin = str(item.get("asin") or "").strip().upper()
    title = _title(item)
    image = _first_image(item)
    price, currency = _offer(item)
    url = item.get("detailPageURL")
    if not asin or not title or not image or price is None or not url:
        return None
    return {
        "platform": "amazon",
        "external_id": asin,
        "title": title[:500],
        "image_url": image,
        "product_url": str(url),
        "price": price,
        "currency": currency or "USD",
        "rating": None,
        "reviews_count": 0,
        "sales_estimate": None,
        "source_metadata": {
            "import_method": "creators_api",
            "metadata_source": "amazon_creators_api",
            "marketplace": MARKETPLACE,
            "keywords": category,
        },
    }


def fetch_amazon_products(existing_ids: set[str], limit: int = 15) -> list[tuple[str, dict[str, Any]]]:
    searches = [
        ("Electronics", "wireless headphones"),
        ("Electronics", "smart watch"),
        ("HomeAndKitchen", "home gadgets"),
        ("Computers", "computer accessories"),
        ("VideoGames", "gaming accessories"),
    ]
    results: list[tuple[str, dict[str, Any]]] = []
    seen = set(existing_ids)
    for category, keywords in searches:
        try:
            items = search_products(keywords, 10)
            added = 0
            for item in items:
                product = _map_item(item, category)
                if not product or product["external_id"] in seen:
                    continue
                seen.add(product["external_id"])
                results.append((category, product))
                added += 1
                if len(results) >= limit:
                    return results
            print(f"[amazon] {keywords}: {added} productos nuevos válidos")
        except Exception as exc:
            print(f"[amazon] error buscando '{keywords}': {exc}")
    return results
