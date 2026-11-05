"""Fail-closed network guard for Amazon and AliExpress traffic."""
from functools import wraps
from urllib.parse import urlsplit

import requests

AMAZON_DOMAINS = (
    "amazon.com", "amazon.ca", "amazon.com.mx", "amazon.co.uk", "amazon.de",
    "amazon.es", "amazon.fr", "amazon.it", "amazon.co.jp", "amazon.com.au",
    "amazon.in", "amazon.nl", "amazon.se", "amazon.pl", "amazon.ae",
    "amazon.sg", "amazon.com.tr", "amazon.com.br", "creatorsapi.amazon",
    "media-amazon.com", "ssl-images-amazon.com", "images-amazon.com",
    "amzn.to", "amzn.eu", "amzn.asia", "amzn.com", "amzn.ca", "amzn.de",
    "amzn.co.uk", "a.co", "link.amazon",
)
ALIEXPRESS_DOMAINS = (
    "aliexpress.com", "aliexpress.us", "aliexpress.ru", "alii.pub",
    "taobao.com", "aliexpress-media.com", "alicdn.com",
)


class MarketplaceAccessBlocked(requests.RequestException):
    """Raised before an HTTP request can reach a protected marketplace."""


def marketplace_for_url(url):
    try:
        host = (urlsplit(str(url or "")).hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if not host:
        return None
    if any(host == domain or host.endswith("." + domain) for domain in AMAZON_DOMAINS):
        return "Amazon"
    if any(host == domain or host.endswith("." + domain) for domain in ALIEXPRESS_DOMAINS):
        return "AliExpress"
    return None


def install_marketplace_request_guard():
    original_send = requests.sessions.Session.send
    if getattr(original_send, "_marketplace_request_guard", False):
        return

    @wraps(original_send)
    def guarded_send(session, request, **kwargs):
        marketplace = marketplace_for_url(getattr(request, "url", None))
        if marketplace:
            raise MarketplaceAccessBlocked(
                f"Solicitud a {marketplace} bloqueada antes de salir de la aplicación."
            )
        return original_send(session, request, **kwargs)

    guarded_send._marketplace_request_guard = True
    requests.sessions.Session.send = guarded_send


install_marketplace_request_guard()
