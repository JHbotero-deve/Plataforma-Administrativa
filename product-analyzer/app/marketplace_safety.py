"""Restricts marketplace traffic to public product-page GET requests only."""
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
AMAZON_SHORTLINKS = {
    "amzn.to", "amzn.eu", "amzn.asia", "amzn.com", "amzn.ca",
    "amzn.de", "amzn.co.uk", "a.co", "link.amazon",
}
ALIEXPRESS_SHORTLINKS = {
    "s.click.aliexpress.com", "click.aliexpress.com", "alii.pub",
}
AMAZON_PAGE_DOMAINS = {
    "amazon.com", "amazon.ca", "amazon.com.mx", "amazon.co.uk",
    "amazon.de", "amazon.es", "amazon.fr", "amazon.it", "amazon.co.jp",
    "amazon.com.au", "amazon.in", "amazon.nl", "amazon.se", "amazon.pl",
    "amazon.ae", "amazon.sg", "amazon.com.tr", "amazon.com.br",
}
ALIEXPRESS_PAGE_DOMAINS = {"aliexpress.com", "aliexpress.us", "aliexpress.ru"}
PRODUCT_PATHS = (
    "/dp/", "/gp/product/", "/gp/aw/d/",
)


class MarketplaceAccessBlocked(requests.RequestException):
    """Raised before a prohibited marketplace request can leave the application."""


def _host_matches(host, domains):
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def marketplace_for_url(url):
    try:
        host = (urlsplit(str(url or "")).hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if not host:
        return None
    if _host_matches(host, AMAZON_DOMAINS):
        return "Amazon"
    if _host_matches(host, ALIEXPRESS_DOMAINS):
        return "AliExpress"
    return None


def _is_public_product_page(request):
    if str(getattr(request, "method", "")).upper() != "GET":
        return False
    try:
        parsed = urlsplit(str(getattr(request, "url", "") or ""))
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or parsed.username or parsed.password:
            return False
        if parsed.port not in (None, 443):
            return False
    except ValueError:
        return False
    if host in AMAZON_SHORTLINKS or host in ALIEXPRESS_SHORTLINKS:
        return True
    labels = host.split(".")
    root_host = ".".join(labels[1:]) if len(labels) > 2 and labels[0] in {"www", "m", "smile"} else host
    if root_host in AMAZON_PAGE_DOMAINS:
        return any(parsed.path.startswith(path) for path in PRODUCT_PATHS)
    if root_host in ALIEXPRESS_PAGE_DOMAINS:
        return bool(__import__("re").match(r"^/item/\d+(?:\.html)?(?:/|$)", parsed.path, __import__("re").I))
    return False


def install_marketplace_request_guard():
    original_send = requests.sessions.Session.send
    if getattr(original_send, "_marketplace_request_guard", False):
        return

    @wraps(original_send)
    def guarded_send(session, request, **kwargs):
        marketplace = marketplace_for_url(getattr(request, "url", None))
        if marketplace and not _is_public_product_page(request):
            raise MarketplaceAccessBlocked(
                f"Solicitud no permitida a {marketplace}; solo se admiten GET de páginas públicas de producto y enlaces cortos oficiales."
            )
        return original_send(session, request, **kwargs)

    guarded_send._marketplace_request_guard = True
    requests.sessions.Session.send = guarded_send


install_marketplace_request_guard()
