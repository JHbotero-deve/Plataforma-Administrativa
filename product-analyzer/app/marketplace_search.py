"""Retired marketplace search queue; search only the locally imported catalog."""

def search_and_publish(query):
    return {"status": "disabled", "query": str(query or ""), "found": 0, "published": 0, "errors": ["External marketplace search was retired; import a product URL."]}


def process_marketplace_search_requests(limit=2):
    return 0
