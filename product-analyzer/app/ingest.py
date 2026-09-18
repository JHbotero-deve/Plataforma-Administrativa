import requests

from amazon_creators import search_items as amazon_search_items
from tiktok_shop import search_products as tiktok_search_products

MELI_URL = "https://api.mercadolibre.com/sites/MCO/search"


def fetch_mercadolibre(query: str, limit: int = 20) -> list[dict]:
    """
    Mercado Libre permite consultar el catálogo público por búsqueda.
    No usamos client_credentials aquí: ese flujo no sustituye el OAuth
    de seller que requiere acceso a recursos privados.
    """
    headers = {
        "User-Agent": "RadarProducto/1.0",
        "Accept": "application/json",
    }
    response = requests.get(
        MELI_URL,
        params={"q": query, "limit": min(limit, 50)},
        headers=headers,
        timeout=20,
    )
    response.raise_for_status()

    products = []
    for item in response.json().get("results", []):
        products.append(
            {
                "external_id": item.get("id"),
                "title": item.get("title"),
                "image_url": (item.get("thumbnail") or "").replace("-I.", "-O."),
                "product_url": item.get("permalink"),
                "price": item.get("price"),
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
