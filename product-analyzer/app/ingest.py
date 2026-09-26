import os
import requests

MELI_SEARCH = "https://api.mercadolibre.com/sites/MCO/search"
MELI_ITEM = "https://api.mercadolibre.com/items/{id}"
MELI_PRICES = "https://api.mercadolibre.com/items/{id}/prices"
MELI_SALE_PRICE = "https://api.mercadolibre.com/items/{id}/sale_price"


def _headers(include_auth=True):
    headers = {
        "User-Agent": "CentralProductAnalyzer/2.2",
        "Accept": "application/json",
    }
    token = os.getenv("MELI_ACCESS_TOKEN", "").strip()
    if include_auth and token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get(url, params=None, include_auth=True):
    response = requests.get(
        url,
        params=params,
        headers=_headers(include_auth),
        timeout=20,
    )
    if response.status_code == 401:
        raise RuntimeError("Mercado Libre rechazó MELI_ACCESS_TOKEN")
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


def fetch_mercadolibre(query, limit=20):
    # La búsqueda de listados se hace sin el token de seller para evitar que
    # un token vencido o con scopes insuficientes bloquee todo el catálogo.
    data = _get(
        MELI_SEARCH,
        {"q": query, "limit": min(limit, 50)},
        include_auth=False,
    )
    products = []

    for item in data.get("results", []):
        item_id = item.get("id")
        title = item.get("title")
        if not item_id or not title:
            continue

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
