"""Búsqueda de marketplaces oficiales con verificación pública de cada URL."""


def search_and_publish(query):
    from marketplace_ingestion import search_and_import

    result = search_and_import(query, limit=8)
    products = result.get("products") or []
    return {
        "status": "completed" if products else "empty",
        "query": str(query or "")[:120],
        "found": len(products),
        "verified": sum(1 for product in products if product.get("verified")),
        "pending_verification": sum(1 for product in products if not product.get("verified")),
        "published": 0,
        "products": products,
        "errors": result.get("errors") or [],
    }


def process_marketplace_search_requests(limit=2):
    return 0
