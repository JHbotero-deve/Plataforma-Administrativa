import os
import time
import requests

TOKEN_URLS = {
    "3.1": "https://api.amazon.com/auth/o2/token",
    "3.2": "https://api.amazon.co.uk/auth/o2/token",
    "3.3": "https://api.amazon.co.jp/auth/o2/token",
}

API_BASE = "https://creatorsapi.amazon"
_token_cache = {"access_token": None, "expires_at": 0.0}


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Falta la variable de entorno {name}")
    return value


def _get_access_token() -> str:
    now = time.time()
    if _token_cache["access_token"] and now < _token_cache["expires_at"]:
        return _token_cache["access_token"]

    credential_id = _required("AMAZON_CREDENTIAL_ID")
    credential_secret = _required("AMAZON_CREDENTIAL_SECRET")
    version = os.getenv("AMAZON_CREDENTIAL_VERSION", "3.1").strip()
    token_url = os.getenv("AMAZON_TOKEN_URL", "").strip() or TOKEN_URLS.get(version)

    if not token_url:
        raise RuntimeError(
            "AMAZON_CREDENTIAL_VERSION debe ser 3.1, 3.2 o 3.3 "
            "(o define AMAZON_TOKEN_URL)."
        )

    response = requests.post(
        token_url,
        json={
            "grant_type": "client_credentials",
            "client_id": credential_id,
            "client_secret": credential_secret,
            "scope": "creatorsapi::default",
        },
        headers={"Content-Type": "application/json"},
        timeout=20,
    )
    if not response.ok:
        raise RuntimeError(
            f"Amazon Creators API token error {response.status_code}: "
            f"{response.text[:500]}"
        )

    data = response.json()
    access_token = data.get("access_token")
    expires_in = int(data.get("expires_in", 3600))
    if not access_token:
        raise RuntimeError("Amazon no devolvió access_token.")

    _token_cache["access_token"] = access_token
    _token_cache["expires_at"] = now + max(60, expires_in - 60)
    return access_token


def search_items(keyword: str, item_count: int = 10) -> list[dict]:
    partner_tag = _required("AMAZON_PARTNER_TAG")
    marketplace = os.getenv("AMAZON_MARKETPLACE", "www.amazon.com").strip()
    currency = os.getenv("AMAZON_CURRENCY", "USD").strip()

    payload = {
        "partnerTag": partner_tag,
        "keywords": keyword,
        "marketplace": marketplace,
        "itemCount": max(1, min(10, item_count)),
        "resources": [
            "images.primary.large",
            "images.primary.medium",
            "itemInfo.title",
            "offersV2.listings.price",
        ],
    }

    response = requests.post(
        f"{API_BASE}/catalog/v1/searchItems",
        json=payload,
        headers={
            "Authorization": f"Bearer {_get_access_token()}",
            "Content-Type": "application/json",
            "x-marketplace": marketplace,
        },
        timeout=30,
    )

    if not response.ok:
        raise RuntimeError(
            f"Amazon SearchItems error {response.status_code}: "
            f"{response.text[:800]}"
        )

    data = response.json()
    items = data.get("searchResult", {}).get("items", [])
    products = []

    for item in items:
        title = (
            item.get("itemInfo", {})
            .get("title", {})
            .get("displayValue")
            or f"Amazon {item.get('asin', '')}".strip()
        )
        image = (
            item.get("images", {})
            .get("primary", {})
            .get("large")
            or item.get("images", {})
            .get("primary", {})
            .get("medium")
            or {}
        )
        listing = (item.get("offersV2", {}).get("listings") or [None])[0] or {}
        money = listing.get("price", {}).get("money", {})
        amount = money.get("amount")
        item_currency = money.get("currency") or currency

        if amount is None:
            continue

        products.append(
            {
                "external_id": item.get("asin"),
                "title": title.strip(),
                "price": float(amount),
                "image_url": image.get("url"),
                "product_url": item.get("detailPageURL"),
                "currency": item_currency,
                "rating": None,
                "reviews_count": 0,
                "sales_estimate": None,
            }
        )

    print(f"[Amazon Creators API] '{keyword}': {len(products)} productos reales.")
    return products
