from meli_oauth import get_meli_tokens, save_meli_tokens
import os
import json
import re
from urllib.parse import quote_plus
import requests

MELI_SEARCH = "https://api.mercadolibre.com/sites/MCO/search"
MELI_ITEM = "https://api.mercadolibre.com/items/{id}"
MELI_PRICES = "https://api.mercadolibre.com/items/{id}/prices"
MELI_SALE_PRICE = "https://api.mercadolibre.com/items/{id}/sale_price"
MELI_OAUTH = "https://api.mercadolibre.com/oauth/token"


def _headers(include_auth=True):
    headers = {
        "User-Agent": "CentralProductAnalyzer/2.2",
        "Accept": "application/json",
    }
    token, _ = get_meli_tokens()
    token = token or (os.getenv("MELI_ACCESS_TOKEN", "").strip() or os.getenv("MERCADOLIBRE_ACCESS_TOKEN", "").strip())
    if include_auth and token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _refresh_access_token():
    _, refresh_token = get_meli_tokens()
    client_id = os.getenv("MELI_CLIENT_ID", "").strip()
    client_secret = os.getenv("MELI_CLIENT_SECRET", "").strip()
    if not (refresh_token and client_id and client_secret):
        return None
    response = requests.post(MELI_OAUTH, data={
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }, timeout=20)
    if not response.ok:
        print(f"[Mercado Libre] renovación de token: HTTP {response.status_code}")
        return None
    payload = response.json()
    token = str(payload.get("access_token") or "").strip()
    new_refresh = str(payload.get("refresh_token") or "").strip()
    if token and new_refresh:
        save_meli_tokens(token, new_refresh, int(payload.get("expires_in") or 0))
        print("[Mercado Libre] access token renovado.")
    return token or None


def _get(url, params=None, include_auth=True, retry_auth=True):
    response = requests.get(
        url,
        params=params,
        headers=_headers(include_auth),
        timeout=20,
    )
    if response.status_code in (401, 403) and include_auth and retry_auth:
        refreshed = _refresh_access_token()
        if refreshed:
            response = requests.get(
                url,
                params=params,
                headers={
                    "User-Agent": "CentralProductAnalyzer/2.3",
                    "Accept": "application/json",
                    "Authorization": f"Bearer {refreshed}",
                },
                timeout=20,
            )
    if response.status_code == 401:
        raise RuntimeError("Mercado Libre rechazó el token de acceso")
    if response.status_code == 403:
        raise RuntimeError("Mercado Libre rechazó la consulta (403)")
    response.raise_for_status()
    return response.json()


def _details(item_id):
    try:
        return _get(MELI_ITEM.format(id=item_id))
    except (requests.RequestException, RuntimeError) as exc:
        print(f"[Mercado Libre] detalle {item_id}: {exc}")
        return {}


def _prices(item_id):
    try:
        return _get(MELI_PRICES.format(id=item_id)).get("prices") or []
    except (requests.RequestException, RuntimeError) as exc:
        print(f"[Mercado Libre] prices {item_id}: {exc}")
        return []


def _sale_price(item_id):
    try:
        return _get(
            MELI_SALE_PRICE.format(id=item_id),
            {"context": "channel_marketplace"},
        )
    except (requests.RequestException, RuntimeError) as exc:
        print(f"[Mercado Libre] sale_price {item_id}: {exc}")
        return {}


def _public_search(query, limit=20):
    """Fallback real-data search from Mercado Libre's public storefront."""
    url = "https://listado.mercadolibre.com.co/" + quote_plus(query).replace("+", "-")
    response = requests.get(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
            "Accept-Language": "es-CO,es;q=0.9,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml",
        },
        timeout=25,
        allow_redirects=True,
    )
    response.raise_for_status()
    html = response.text
    products = []
    seen = set()

    for raw in re.findall(r'<script[^>]+type=["\\']application/ld\\+json["\\'][^>]*>(.*?)</script>', html, re.I | re.S):
        try:
            data = json.loads(raw.strip())
        except (TypeError, ValueError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if isinstance(node.get("@graph"), list):
                    stack.extend(node["@graph"])
                if isinstance(node.get("item"), dict):
                    stack.append(node["item"])
                if str(node.get("@type", "")).lower() != "product":
                    continue
                name = str(node.get("name") or "").strip()
                image = node.get("image")
                if isinstance(image, list):
                    image = image[0] if image else None
                offers = node.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                price = offers.get("price") if isinstance(offers, dict) else None
                permalink = (offers.get("url") if isinstance(offers, dict) else None) or node.get("url")
                match = re.search(r"(MCO-\d+)", str(permalink or ""))
                if not match or not name or not image or price in (None, ""):
                    continue
                item_id = match.group(1)
                if item_id in seen:
                    continue
                try:
                    amount = float(str(price).replace(".", "").replace(",", "."))
                except ValueError:
                    continue
                seen.add(item_id)
                products.append({
                    "id": item_id, "title": name, "thumbnail": str(image),
                    "permalink": str(permalink), "price": amount, "currency_id": "COP",
                })
                if len(products) >= min(max(limit, 1), 20):
                    return {"results": products, "_source": "public_web"}

    return {"results": products, "_source": "public_web"}


def fetch_mercadolibre(query, limit=20):
    try:
        data = _get(MELI_SEARCH, {"q": query, "limit": min(limit, 50)}, include_auth=True)
    except RuntimeError as exc:
        if "403" not in str(exc):
            raise
        print(f"[Mercado Libre] OAuth rechazado para '{query}'; usando búsqueda pública real como respaldo.")
        data = _public_search(query, limit)
    products = []

    public_web = data.get("_source") == "public_web"
    for item in data.get("results", []):
        item_id = item.get("id")
        title = item.get("title")
        if not item_id or not title:
            continue

        if public_web:
            detail, prices, sale = {}, [], {}
        else:
            detail = _details(item_id)
            prices = _prices(item_id)
            sale = _sale_price(item_id)

        price = (
            sale.get("amount")
            or detail.get("price")
            or item.get("price")
        )
        currency = (
            sale.get("currency_id")
            or detail.get("currency_id")
            or item.get("currency_id")
            or "COP"
        )

        if price in (None, 0) and prices:
            current = sorted(
                prices,
                key=lambda value: value.get("last_updated") or "",
                reverse=True,
            )[0]
            price = current.get("amount")
            currency = current.get("currency_id") or currency

        if price in (None, 0):
            continue

        seller_id = detail.get("seller_id") or (item.get("seller") or {}).get("id")
        metadata = {
            "search_result": item,
            "item": detail,
            "prices": prices,
            "sale_price": sale,
            "site_id": "MCO",
            "category_id": detail.get("category_id"),
            "condition": detail.get("condition"),
            "buying_mode": detail.get("buying_mode"),
            "listing_type_id": detail.get("listing_type_id"),
            "status": detail.get("status"),
            "available_quantity": detail.get("available_quantity"),
            "sold_quantity": detail.get("sold_quantity"),
            "shipping": detail.get("shipping") or {},
            "attributes": detail.get("attributes") or [],
            "variations": detail.get("variations") or [],
            "tags": detail.get("tags") or [],
        }

        products.append({
            "external_id": item_id,
            "title": title,
            "image_url": (
                detail.get("thumbnail")
                or item.get("thumbnail")
                or ""
            ).replace("-I.", "-O."),
            "product_url": detail.get("permalink") or item.get("permalink"),
            "price": float(price),
            "currency": currency,
            "rating": None,
            "reviews_count": 0,
            "sales_estimate": detail.get("sold_quantity") or item.get("sold_quantity"),
            "seller": {
                "external_id": str(seller_id) if seller_id else None,
                "name": None,
                "reputation": None,
            },
            "source_metadata": metadata,
        })

    print(f"[Mercado Libre MCO] {query}: {len(products)} productos reales completos")
    return products


def fetch_amazon(query, limit=10):
    return amazon_search_items(query, min(limit, 10))


def fetch_tiktok(query, limit=20):
    return tiktok_search_products(query, min(limit, 100))
