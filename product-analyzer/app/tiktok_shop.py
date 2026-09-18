import hashlib
import hmac
import json
import os
import time
import requests

API_BASE = "https://open-api.tiktokglobalshop.com"
SEARCH_PATH = "/product/202502/products/search"


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Falta la variable de entorno {name}")
    return value


def _sign_request(app_secret: str, path: str, params: dict, body: str = "") -> str:
    params_to_sign = {
        key: value
        for key, value in params.items()
        if key not in {"sign", "access_token", "x-tts-access-token"}
        and not isinstance(value, (list, dict))
    }
    canonical = "".join(f"{key}{params_to_sign[key]}" for key in sorted(params_to_sign))
    string_to_sign = f"{path}{canonical}{body}"
    wrapped = f"{app_secret}{string_to_sign}{app_secret}"
    return hmac.new(
        app_secret.encode("utf-8"),
        wrapped.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _signed_params() -> tuple[dict, str]:
    app_key = _required("TIKTOK_APP_KEY")
    app_secret = _required("TIKTOK_APP_SECRET")
    shop_cipher = _required("TIKTOK_SHOP_CIPHER")
    timestamp = str(int(time.time()))

    params = {
        "app_key": app_key,
        "shop_cipher": shop_cipher,
        "timestamp": timestamp,
    }
    return params, app_secret


def search_products(keyword: str, limit: int = 20) -> list[dict]:
    """
    Busca productos del catálogo de la tienda TikTok Shop autorizada.
    La API de TikTok Shop consulta el catálogo del seller autorizado,
    no el marketplace público completo.
    """
    access_token = _required("TIKTOK_ACCESS_TOKEN")
    params, app_secret = _signed_params()
    body = json.dumps({"page_size": max(1, min(100, limit)), "status": "ALL"}, separators=(",", ":"))
    params["sign"] = _sign_request(app_secret, SEARCH_PATH, params, body)

    response = requests.post(
        f"{API_BASE}{SEARCH_PATH}",
        params=params,
        data=body,
        headers={
            "x-tts-access-token": access_token,
            "content-type": "application/json",
        },
        timeout=30,
    )

    if not response.ok:
        raise RuntimeError(
            f"TikTok Shop Search Products HTTP {response.status_code}: "
            f"{response.text[:800]}"
        )

    data = response.json()
    if data.get("code") not in (0, None):
        raise RuntimeError(
            f"TikTok Shop API {data.get('code')}: {data.get('message', 'error')}"
        )

    raw_products = data.get("data", {}).get("products", [])
    keyword_norm = keyword.casefold().strip()
    products = []

    for item in raw_products:
        title = str(item.get("title") or item.get("name") or "").strip()
        if keyword_norm and keyword_norm not in title.casefold():
            continue

        skus = item.get("skus") or []
        sku = skus[0] if skus else {}
        price_data = sku.get("price") or sku.get("sale_price") or {}
        if isinstance(price_data, dict):
            price = price_data.get("amount") or price_data.get("sale_price")
            currency = price_data.get("currency") or price_data.get("currency_code") or "USD"
        else:
            price = price_data
            currency = "USD"

        image = None
        images = item.get("images") or item.get("main_images") or []
        if images:
            first = images[0]
            image = first.get("url") if isinstance(first, dict) else first

        product_id = item.get("id") or item.get("product_id")
        if not product_id or not title or price is None:
            continue

        products.append(
            {
                "external_id": str(product_id),
                "title": title,
                "price": float(price),
                "image_url": image,
                "product_url": item.get("product_url") or item.get("detail_url"),
                "currency": currency,
                "rating": None,
                "reviews_count": 0,
                "sales_estimate": None,
            }
        )

    print(f"[TikTok Shop API] '{keyword}': {len(products)} productos del catálogo autorizado.")
    return products


__all__ = ["search_products", "_sign_request"]
