import os
import requests

from amazon_creators import search_items as amazon_search_items
from tiktok_shop import search_products as tiktok_search_products

MELI_URL = "https://api.mercadolibre.com/sites/MCO/search"


def _meli_headers() -> dict:
    headers = {
        "User-Agent": "RadarProducto/1.0",
        "Accept": "application/json",
    }
    token = os.getenv("MELI_ACCESS_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_mercadolibre(query: str, limit: int = 20) -> list[dict]:
    response = requests.get(
        MELI_URL,
        params={"q": query, "limit": min(limit, 50)},
        headers=_meli_headers(),
        timeout=20,
    )
    if response.status_code == 401:
        raise RuntimeError(
            "Mercado Libre rechazó la autenticación. Revisa MELI_ACCESS_TOKEN."
        )
    if response.status_code == 403:
        raise RuntimeError(
            "Mercado Libre rechazó la consulta (403). Configura un "
            "MELI_ACCESS_TOKEN válido y autorizado para MCO."
        )
    response.raise_for_status()

    products = []
    for item in response.json().get("results", []):
        external_id = item.get("id")
        title = item.get("title")
        price = item.get("price")
        if not external_id or not title or price in (None, 0):
            continue

        products.append(
            {
                "external_id": external_id,
                "title": title,
                "image_url": (item.get("thumbnail") or "").replace("-I.", "-O."),
                "product_url": item.get("permalink"),
                "price": price,
                "currency": item.get("currency_id") or "COP",
                "rating": None,
                "reviews_count": 0,
                "sales_estimate": item.get("sold_quantity"),
            }
        )

    print(f"[Mercado Libre] '{query}': {len(products)} productos reales.")
    return products


def fetch_amazon(query: str, limit: int = 10) -> list[dict]:
    return amazon_search_items(query, min(limit, 10))


def fetch_tiktok(query: str, limit: int = 20) -> list[dict]:
    return tiktok_search_products(query, min(limit, 100))
