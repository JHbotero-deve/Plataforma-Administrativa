from meli_oauth import get_meli_tokens, save_meli_tokens
import os
import json
import re
import time
from urllib.parse import quote_plus
import requests

MELI_SEARCH = "https://api.mercadolibre.com/sites/MCO/search"
MELI_ITEM = "https://api.mercadolibre.com/items/{id}"
MELI_PRICES = "https://api.mercadolibre.com/items/{id}/prices"
MELI_SALE_PRICE = "https://api.mercadolibre.com/items/{id}/sale_price"
MELI_OAUTH = "https://api.mercadolibre.com/oauth/token"
MELI_PRODUCTS_SEARCH = "https://api.mercadolibre.com/products/search"
MELI_PRODUCT = "https://api.mercadolibre.com/products/{id}"


class MercadoLibreAccessDenied(RuntimeError):
    """Error no recuperable durante el ciclo actual: Mercado Libre responde 403."""

    fatal = True


class MercadoLibreRateLimited(RuntimeError):
    """Pausa la sincronización cuando Mercado Libre responde 429."""

    fatal = True


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


_REFRESH_RETRY_AFTER = 0.0


def _refresh_access_token():
    global _REFRESH_RETRY_AFTER
    now = time.monotonic()
    if now < _REFRESH_RETRY_AFTER:
        return None

    _, refresh_token = get_meli_tokens()
    client_id = os.getenv("MELI_CLIENT_ID", "").strip()
    client_secret = os.getenv("MELI_CLIENT_SECRET", "").strip()
    if not (refresh_token and client_id and client_secret):
        _REFRESH_RETRY_AFTER = now + 300
        print("[Mercado Libre] renovación pausada: faltan el refresh token o las credenciales de cliente.")
        return None

    response = requests.post(MELI_OAUTH, data={
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }, timeout=20)
    if not response.ok:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        error_code = str(payload.get("error") or payload.get("code") or "unknown")[:80]
        _REFRESH_RETRY_AFTER = time.monotonic() + 300
        print(
            f"[Mercado Libre] renovación rechazada: HTTP {response.status_code}; "
            f"código={error_code}; reintento en 300 s."
        )
        return None

    payload = response.json()
    token = str(payload.get("access_token") or "").strip()
    new_refresh = str(payload.get("refresh_token") or "").strip()
    if token and new_refresh:
        save_meli_tokens(token, new_refresh, int(payload.get("expires_in") or 0))
        _REFRESH_RETRY_AFTER = 0.0
        print("[Mercado Libre] access token renovado.")
        return token

    _REFRESH_RETRY_AFTER = time.monotonic() + 300
    print("[Mercado Libre] renovación sin los tokens esperados; reintento en 300 s.")
    return None


def _get(url, params=None, include_auth=True, retry_auth=True):
    response = requests.get(
        url,
        params=params,
        headers=_headers(include_auth),
        timeout=20,
    )
    if response.status_code == 401 and include_auth and retry_auth:
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
        try:
            payload = response.json()
            reason = payload.get("message") or payload.get("error") or payload.get("code")
        except ValueError:
            reason = response.text[:180].strip()
        suffix = f": {reason}" if reason else ""
        resource = url.split("api.mercadolibre.com", 1)[-1].split("?", 1)[0]
        request_id = response.headers.get("x-request-id") or response.headers.get("x-trace-id")
        trace = f"; request_id={request_id}" if request_id else ""
        raise MercadoLibreAccessDenied(
            f"Mercado Libre bloqueó el recurso {resource} (403){suffix}{trace}"
        )
    if response.status_code == 429:
        resource = url.split("api.mercadolibre.com", 1)[-1].split("?", 1)[0]
        retry_after = response.headers.get("Retry-After")
        suffix = f"; retry_after={retry_after}s" if retry_after else ""
        raise MercadoLibreRateLimited(
            f"Mercado Libre limitó el recurso {resource} (429){suffix}"
        )
    response.raise_for_status()
    return response.json()


def _details(item_id):
    try:
        return _get(MELI_ITEM.format(id=item_id))
    except MercadoLibreAccessDenied:
        raise
    except (requests.RequestException, RuntimeError) as exc:
        print(f"[Mercado Libre] detalle {item_id}: {exc}")
        return {}


def _description(item_id):
    try:
        data = _get(f"https://api.mercadolibre.com/items/{item_id}/description")
        return str(data.get("plain_text") or data.get("text") or "").strip()[:5000] or None
    except MercadoLibreAccessDenied:
        raise
    except (requests.RequestException, RuntimeError) as exc:
        print(f"[Mercado Libre] descripción {item_id}: {exc}")
        return None


def _seller_details(seller_id):
    if not seller_id:
        return {}
    try:
        return _get(f"https://api.mercadolibre.com/users/{seller_id}")
    except MercadoLibreAccessDenied:
        raise
    except (requests.RequestException, RuntimeError) as exc:
        print(f"[Mercado Libre] vendedor {seller_id}: {exc}")
        return {}


def _prices(item_id):
    try:
        return _get(MELI_PRICES.format(id=item_id)).get("prices") or []
    except MercadoLibreAccessDenied:
        raise
    except (requests.RequestException, RuntimeError) as exc:
        print(f"[Mercado Libre] prices {item_id}: {exc}")
        return []


def _sale_price(item_id):
    try:
        return _get(
            MELI_SALE_PRICE.format(id=item_id),
            {"context": "channel_marketplace"},
        )
    except MercadoLibreAccessDenied:
        raise
    except (requests.RequestException, RuntimeError) as exc:
        print(f"[Mercado Libre] sale_price {item_id}: {exc}")
        return {}


def _public_search(query, limit=20):
    """Obtiene publicaciones reales de Mercado Libre mediante su página pública."""
    import unicodedata
    from html import unescape
    from urllib.parse import unquote

    slug = unicodedata.normalize("NFKD", query).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", slug).strip("-").lower()
    listing_url = f"https://listado.mercadolibre.com.co/{slug}"
    reader_url = "https://r.jina.ai/" + listing_url

    try:
        response = requests.get(
            reader_url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "text/plain"},
            timeout=30,
        )
        response.raise_for_status()
        text = response.text
    except requests.RequestException as exc:
        print(f"[Mercado Libre] lector web no disponible '{query}': {exc}")
        return {"results": [], "_source": "public_web"}

    products, seen = [], set()
    # Markdown de Jina conserva los enlaces e imágenes de las tarjetas.
    link_re = re.compile(r"\[([^\]]{8,500})\]\((https?://[^)]+mercadolibre\.com\.co/[^)]+)\)", re.I)
    image_re = re.compile(r"!\[[^\]]*\]\((https?://[^)]+)\)", re.I)
    matches = list(link_re.finditer(text))

    for idx, match in enumerate(matches):
        title = unescape(re.sub(r"\s+", " ", match.group(1))).strip()
        permalink = unquote(match.group(2))
        if "/listado/" in permalink or "/categorias/" in permalink:
            continue
        if not title or permalink in seen:
            continue

        window = text[match.end(): matches[idx + 1].start() if idx + 1 < len(matches) else min(len(text), match.end() + 1800)]
        price_match = re.search(r"\$\s*([0-9][0-9.]{2,})", window)
        if not price_match:
            continue
        amount = float(price_match.group(1).replace(".", ""))
        if amount <= 0:
            continue

        image_match = image_re.search(text[max(0, match.start()-1200):match.end()+1200])
        image = image_match.group(1) if image_match else ""

        # Si la tarjeta no trae imagen, consultamos la página del producto a través del lector.
        if not image:
            try:
                detail = requests.get(
                    "https://r.jina.ai/" + permalink,
                    headers={"User-Agent": "Mozilla/5.0", "Accept": "text/plain"},
                    timeout=20,
                )
                if detail.ok:
                    dm = image_re.search(detail.text)
                    if dm:
                        image = dm.group(1)
            except requests.RequestException:
                pass

        item_match = re.search(r"(MCO[-_]\d{6,})", permalink, re.I)
        item_id = item_match.group(1).replace("_", "-").upper() if item_match else ""
        if not item_id:
            # Las URLs de publicación pueden no incluir el ID; se genera una clave estable.
            import hashlib
            item_id = "WEB-" + hashlib.sha1(permalink.encode("utf-8")).hexdigest()[:16].upper()

        seen.add(permalink)
        products.append({
            "id": item_id,
            "title": title[:500],
            "thumbnail": image,
            "permalink": permalink,
            "price": amount,
            "currency_id": "COP",
        })
        if len(products) >= min(max(limit, 1), 20):
            break

    print(f"[Mercado Libre] página pública real '{query}': {len(products)} productos")
    return {"results": products, "_source": "public_web"}



def _catalog_search(query, limit=20):
    """Mercado Libre catalog search: alternativa vigente al /sites/MCO/search bloqueado."""
    try:
        data = _get(MELI_PRODUCTS_SEARCH, {
            "status": "active", "site_id": "MCO", "q": query, "limit": min(limit, 20)
        }, include_auth=True)
    except MercadoLibreAccessDenied:
        raise
    except MercadoLibreRateLimited:
        raise
    except (RuntimeError, requests.RequestException) as exc:
        print(f"[Mercado Libre] catalog search '{query}' no disponible: {exc}")
        return []
    products = []
    for result in data.get("results", []):
        product_id = result.get("id")
        if not product_id:
            continue
        try:
            detail = _get(MELI_PRODUCT.format(id=product_id), include_auth=True)
        except MercadoLibreAccessDenied:
            raise
        except (RuntimeError, requests.RequestException):
            continue
        winner = detail.get("buy_box_winner") or {}
        price = winner.get("price")
        if not price:
            continue
        pictures = detail.get("pictures") or []
        gallery_urls = list(dict.fromkeys(
            str(pic.get("url") or pic.get("secure_url") or "").strip()
            for pic in pictures
            if isinstance(pic, dict) and (pic.get("url") or pic.get("secure_url"))
        ))
        image = gallery_urls[0] if gallery_urls else ""
        item_id = winner.get("item_id") or product_id
        products.append({
            "external_id": item_id,
            "title": detail.get("name") or result.get("name") or query,
            "image_url": image,
            "gallery_urls": gallery_urls,
            "product_url": winner.get("permalink") or detail.get("permalink") or "",
            "price": float(price),
            "currency": winner.get("currency_id") or "COP",
            "rating": None,
            "reviews_count": 0,
            "sales_estimate": winner.get("sold_quantity") or detail.get("sold_quantity"),
            "seller": {"external_id": str(winner.get("seller_id") or "") or None, "name": None, "reputation": None},
            "source_metadata": {"catalog_product_id": product_id, "catalog": True, "buy_box_winner": winner},
        })
    print(f"[Mercado Libre] catalogo real '{query}': {len(products)} productos")
    return products


def fetch_mercadolibre(query, limit=20):
    # Si la búsqueda de publicaciones devuelve 403, validamos el token una vez.
    # No repetimos consultas anónimas ni seguimos disparando búsquedas si el
    # recurso o la aplicación están bloqueados.
    try:
        data = _get(MELI_SEARCH, {"q": query, "limit": min(limit, 50)}, include_auth=True)
        print(f"[Mercado Libre] API autenticada OK '{query}': {len(data.get('results') or [])} resultados")
    except MercadoLibreAccessDenied as search_exc:
        print(f"[Mercado Libre] API bloqueada; se intenta catálogo autenticado '{query}': {search_exc}")
        try:
            catalog_products = _catalog_search(query, limit)
        except MercadoLibreAccessDenied as catalog_exc:
            # Ambos endpoints oficiales rechazaron la aplicación. No insistir con
            # el lector público: además de no ser una fuente estable, una llamada
            # por cada consulta provoca límites 429 y amplifica el fallo.
            print(f"[Mercado Libre] búsqueda detenida por falta de permisos: {catalog_exc}")
            raise
        except MercadoLibreRateLimited as catalog_exc:
            print(f"[Mercado Libre] búsqueda detenida por límite de solicitudes: {catalog_exc}")
            raise
        except (RuntimeError, requests.RequestException) as catalog_exc:
            print(f"[Mercado Libre] catálogo API no disponible; se evalúa lector público: {catalog_exc}")
            catalog_products = []
        if catalog_products:
            print(f"[Mercado Libre] usando catálogo autenticado '{query}': {len(catalog_products)} productos")
            return catalog_products
        data = _public_search(query, limit)

    # Si ya se consultó la web pública, no repetir la misma petición cuando
    # devuelve cero resultados; evita duplicar carga y acelerar respuestas 429.
    if not data.get("results") and data.get("_source") != "public_web":
        data = _public_search(query, limit)

    products = []
    public_web = data.get("_source") == "public_web"
    for item in data.get("results", []):
        item_id = item.get("id")
        title = item.get("title")
        if not item_id or not title:
            continue

        # Priorizar los datos de búsqueda para evitar tres llamadas por producto.
        # Consultar el detalle solo si faltan campos esenciales; si la búsqueda ya
        # entrega precio, miniatura, título y enlace, conservar su precio original.
        needs_detail = not public_web and (
            not item.get("permalink")
            or not item.get("thumbnail")
            or not item.get("price")
            or not item.get("title")
        )
        detail = _details(item_id) if needs_detail and item_id else {}
        prices = []
        sale = {}
        if not public_web and not (item.get("price") or detail.get("price")) and item_id:
            prices = _prices(item_id)
            sale = _sale_price(item_id)

        price = (
            sale.get("amount")
            or detail.get("price")
            or item.get("price")
        )
        previous_price = (
            detail.get("original_price")
            or item.get("original_price")
            or detail.get("regular_price")
            or item.get("regular_price")
        )
        try:
            previous_price = float(previous_price) if previous_price is not None else None
        except (TypeError, ValueError):
            previous_price = None
        if previous_price is not None and (price is None or previous_price <= float(price)):
            previous_price = None
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
        seller = _seller_details(seller_id) if seller_id and not public_web and detail else {}
        seller_rep = seller.get("seller_reputation") or {}
        seller_transactions = seller_rep.get("transactions") or {}
        seller_ratings = seller_transactions.get("ratings") or {}
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

        item_pictures = detail.get("pictures") or item.get("pictures") or []
        gallery_urls = list(dict.fromkeys(
            str(pic.get("secure_url") or pic.get("url") or "").strip().replace("-I.", "-O.")
            for pic in item_pictures
            if isinstance(pic, dict) and (pic.get("secure_url") or pic.get("url"))
        ))
        primary_image = (
            detail.get("secure_thumbnail") or detail.get("thumbnail")
            or item.get("secure_thumbnail") or item.get("thumbnail") or ""
        ).replace("-I.", "-O.")
        if primary_image and primary_image not in gallery_urls:
            gallery_urls.insert(0, primary_image)

        products.append({
            "external_id": item_id,
            "title": title,
            "description": _description(item_id) if not public_web else None,
            "brand": next((a.get("value_name") for a in (detail.get("attributes") or []) if a.get("id") == "BRAND" and a.get("value_name")), None),
            "image_url": primary_image,
            "gallery_urls": gallery_urls,
            "product_url": detail.get("permalink") or item.get("permalink"),
            "price": float(price),
            "previous_price": previous_price,
            "currency": currency,
            "rating": None,
            "reviews_count": 0,
            "sales_estimate": detail.get("sold_quantity") or item.get("sold_quantity"),
            "seller": {
                "external_id": str(seller_id) if seller_id else None,
                "name": seller.get("nickname") or seller.get("first_name") or None,
                "reputation": seller_ratings.get("positive"),
                "level": seller_rep.get("level_id"),
                "power_seller_status": seller_rep.get("power_seller_status"),
                "permalink": seller.get("permalink"),
                "registration_date": seller.get("registration_date"),
                "city": (seller.get("address") or {}).get("city"),
                "state": (seller.get("address") or {}).get("state"),
            },
            "source_metadata": metadata,
        })

    print(f"[Mercado Libre MCO] {query}: {len(products)} productos reales completos")
    return products


def fetch_amazon(query, limit=10):
    return amazon_search_items(query, min(limit, 10))


def fetch_tiktok(query, limit=20):
    return tiktok_search_products(query, min(limit, 100))
