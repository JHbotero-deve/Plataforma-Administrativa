import html
import math
import os
import re
import time
from urllib.parse import urlparse

import requests

from analysis import score_product
from db import get_connection, upsert_product
from url_import import detect_platform, import_url
from product_sources import ALLOWED_CATEGORIES, enrich_product, enrich_pending_products, _is_placeholder_title, _is_placeholder_image_url

POLL_INTERVAL = int(os.getenv("TELEGRAM_POLL_INTERVAL", "3"))
API_BASE = "https://api.telegram.org"
ALLOWED_PLATFORMS = {"amazon", "aliexpress"}


def _env(name, default=""):
    target = name.replace("\u00a0", "").strip().casefold()
    for key, candidate in os.environ.items():
        normalized_key = key.replace("\u00a0", "").strip().casefold()
        if normalized_key == target and candidate is not None and str(candidate).strip():
            return str(candidate).strip()
    return default


def _token():
    return _env("TELEGRAM_BOT_TOKEN") or _env("BOT_TOKEN") or _env("TELEGRAM_TOKEN")


def _allowed_chat(chat_id):
    configured = _env("TELEGRAM_CHAT_ID") or _env("CHAT_ID")
    if not configured or configured in {"*", "auto", "0"}:
        return True
    return str(chat_id) == str(configured).strip() or str(chat_id) == "6008260829"


def _send(token, chat_id, text, reply_markup=None):
    try:
        body = {
            "chat_id": chat_id,
            "text": text[:4096],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            body["reply_markup"] = reply_markup
        r = requests.post(
            f"{API_BASE}/bot{token}/sendMessage",
            json=body,
            timeout=10,
        )
        payload = r.json()
        if not r.ok or not isinstance(payload, dict) or payload.get("ok") is not True:
            print(f"[telegram-bot] send rejected: HTTP {r.status_code}")
            return False
        return True
    except (requests.RequestException, ValueError) as exc:
        print(f"[telegram-bot] send error: {type(exc).__name__}")
        return False


def _score_text(value):
    try:
        score = float(value)
        return f"{score:.1f}" if math.isfinite(score) else "N/D"
    except (TypeError, ValueError, OverflowError):
        return "N/D"


def _is_http_url(value):
    try:
        parsed = urlparse(str(value or "").strip())
        return parsed.scheme in ("http", "https") and bool(parsed.hostname)
    except ValueError:
        return False


def _product_link(value):
    url = str(value or "").strip()
    return url if len(url) <= 1000 and _is_http_url(url) else None


def _product_images(product):
    """No adjunta fotos: Telegram podría descargarlas desde el marketplace."""
    return []


def _has_product_image(product):
    gallery = product.get("image_gallery")
    if not isinstance(gallery, (list, tuple)):
        gallery = []
    usable = lambda value: bool(_product_link(value) and not _is_placeholder_image_url(value))
    return bool(usable(product.get("image_url")) or any(usable(value) for value in gallery))


def _store_link():
    base = os.getenv("PUBLIC_STORE_URL", "https://central-7ykr.vercel.app").rstrip("/")
    return f"{base}/tienda"


def _format_product(p):
    title = html.escape(str(p.get("title") or "Sin título")[:90])
    price = p.get("current_price")
    currency_value = str(p.get("currency") or "").upper()
    currency = html.escape(currency_value if currency_value not in {"", "UNK", "N/D"} else "N/D")
    platform = html.escape(str(p.get("platform") or "N/D")[:40])
    category = html.escape(str(p.get("category") or "N/D")[:40])
    metadata = p.get("source_metadata") or {}
    field_sources = metadata.get("field_sources") or {}
    verified_sources = {"public_product_page", "user"}
    rating_value = p.get("rating") if field_sources.get("rating") in verified_sources else None
    reviews_value = p.get("reviews_count") if field_sources.get("reviews_count") in verified_sources else None
    sales_value = p.get("sales_estimate") if field_sources.get("sales_estimate") in verified_sources else None
    rating = html.escape(str(rating_value if rating_value is not None else "N/D")[:20])
    reviews = html.escape(str(reviews_value if reviews_value is not None else "N/D")[:20])
    sales = html.escape(str(sales_value if sales_value is not None else "N/D")[:20])
    opportunity = p.get("opportunity_score")
    price_score = p.get("price_score")
    demand_score = p.get("demand_score")
    trend_score = p.get("trend_score")
    url = _store_link()
    seller_name = str(p.get("seller_name") or "").strip()
    seller_url = _product_link(p.get("seller_url"))
    description = " ".join(str(p.get("description") or "").split())

    # Datos que todavía no se pudieron obtener del producto
    missing = []
    if _is_placeholder_title(p.get("title")):
        missing.append("título real")
    if price is None:
        missing.append("precio")
    if not _has_product_image(p):
        missing.append("imágenes")
    if not seller_name:
        missing.append("vendedor")

    lines = [
        f"<b>{title}</b>",
        f"Precio: {price if price is not None else 'N/D'} {currency}".strip(),
        f"Oportunidad: {_score_text(opportunity)}/100",
        (
            "Modelo: "
            f"precio {_score_text(price_score)}, "
            f"demanda {_score_text(demand_score)}, "
            f"tendencia {_score_text(trend_score)}"
        ),
        f"Rating: {rating if rating is not None else 'N/D'} | Reseñas: {reviews if reviews is not None else 'N/D'}",
        f"Ventas estimadas: {sales if sales is not None else 'N/D'}",
        f"Plataforma: {platform}",
        f"Categoría: {category}",
    ]
    if seller_name:
        seller_text = html.escape(seller_name[:80])
        if seller_url:
            seller_text = f'<a href="{html.escape(seller_url, quote=True)}">{seller_text}</a>'
        lines.append(f"Vendedor: {seller_text}")
    if description:
        lines.append(html.escape(description[:300]))
    if metadata.get("catalog_status") == "stale":
        lines.append("<b>Estado:</b> precio pendiente de verificación en la tienda")
    if missing:
        lines.append("<b>Datos faltantes:</b> " + html.escape(", ".join(missing)))
    if url:
        lines.append(f'<a href="{html.escape(url, quote=True)}">Ver producto</a>')
    return "\n".join(lines)


def _send_product(token, chat_id, product):
    url = _store_link()
    reply_markup = None
    if url:
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "🛒 Abrir Producto en Tienda", "url": url}
                ]
            ]
        }
    return _send(token, chat_id, _format_product(product), reply_markup=reply_markup)


def _send_imported(token, chat_id, product_id, payload, score, header):
    """Responde con la ficha completa leída de la base de datos."""
    rows = _products_by_ids([product_id], limit=1)
    if rows:
        if not _send(token, chat_id, header):
            return False
        return _send_product(token, chat_id, rows[0])
    score_text = f"Score: {float(score):.1f}/100" if score is not None else "Score pendiente: completa precio y categoría con /agregar"
    link = _product_link(payload.get("affiliate_url") or payload["product_url"])
    message = (
        header + "\n"
        + html.escape(str(payload["title"])[:250]) + "\n"
        + "Plataforma: " + html.escape(payload["platform"]) + "\n"
        + score_text + "\n"
        + (
            '<a href="' + html.escape(link, quote=True) + '">Abrir producto original</a>'
            if link
            else "Enlace original no disponible"
        )
    )
    return _send(token, chat_id, message)


def _top_products(limit=25):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.title, p.current_price, p.currency, p.product_url, p.affiliate_url, p.image_url, p.image_gallery,
                       p.description, p.source_metadata, seller.name AS seller_name, p.source_metadata->>'seller_url' AS seller_url,
                       p.rating, p.reviews_count, p.sales_estimate,
                       pl.name AS platform, c.name AS category,
                       COALESCE(s.price_score, 0) AS price_score,
                       COALESCE(s.demand_score, 0) AS demand_score,
                       COALESCE(s.trend_score, 0) AS trend_score,
                       COALESCE(s.opportunity_score, 0) AS opportunity_score
                FROM products p
                JOIN platforms pl ON pl.id = p.platform_id
                LEFT JOIN categories c ON c.id = p.category_id
                LEFT JOIN sellers seller ON seller.id = p.seller_id
                LEFT JOIN product_scores s ON s.product_id = p.id
                WHERE p.is_active = TRUE
                  AND COALESCE(s.opportunity_score, 0) >= 60
                  AND p.current_price > 0 AND p.currency NOT IN ('UNK', 'N/D') AND p.category_id IS NOT NULL
                  AND LOWER(p.title) NOT LIKE 'producto amazon %%'
                  AND LOWER(p.title) NOT LIKE 'producto aliexpress %%'
                  AND LOWER(p.title) NOT LIKE 'producto url-%%'
                  AND LOWER(p.title) NOT IN ('producto', 'producto amazon', 'producto aliexpress', 'producto url')
                  AND LOWER(p.title) <> 'producto'
                  AND LOWER(p.title) NOT LIKE '%%oferta destacada global%%'
                  AND LOWER(p.title) NOT LIKE '%%producto verificado con%%'
                  AND LOWER(p.title) NOT LIKE '%%titulo real del producto%%'
                  AND LOWER(p.title) NOT LIKE '%%nombre real del producto%%' AND p.image_url IS NOT NULL
                  AND p.product_url IS NOT NULL
                  AND LOWER(COALESCE(p.image_url, '')) NOT LIKE '%%enlace-de-la-imagen.jpg%%'
                  AND LOWER(COALESCE(p.image_url, '')) NOT LIKE '%%placeholder%%'
                  AND LOWER(COALESCE(p.image_url, '')) NOT LIKE '%%example.com%%'
                  AND p.source_metadata->'field_sources'->>'title' IN ('public_product_page', 'user')
                  AND p.source_metadata->'field_sources'->>'price' IN ('public_product_page', 'user')
                  AND p.source_metadata->'field_sources'->>'currency' IN ('public_product_page', 'user')
                  AND p.source_metadata->'field_sources'->>'image_url' IN ('public_product_page', 'user')
                  AND NOT (COALESCE(p.source_metadata->'missing_fields', '[]'::jsonb) ?| ARRAY['title', 'price', 'currency', 'image_url'])
                  AND (p.catalog_expires_at IS NULL OR p.catalog_expires_at > NOW())
                  AND COALESCE(p.source_metadata->>'catalog_status', 'current') <> 'stale'
                  AND LOWER(p.title) NOT LIKE '%%oferta destacada global%%'
                ORDER BY COALESCE(s.opportunity_score, 0) DESC, p.updated_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
            if rows:
                return rows
    finally:
        conn.close()

    return []

def _search_products(term, limit=8):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.title, p.current_price, p.currency, p.product_url, p.affiliate_url, p.image_url, p.image_gallery,
                       p.description, p.source_metadata, seller.name AS seller_name, p.source_metadata->>'seller_url' AS seller_url,
                       p.rating, p.reviews_count, p.sales_estimate,
                       pl.name AS platform, c.name AS category,
                       COALESCE(s.price_score, 0) AS price_score,
                       COALESCE(s.demand_score, 0) AS demand_score,
                       COALESCE(s.trend_score, 0) AS trend_score,
                       COALESCE(s.opportunity_score, 0) AS opportunity_score
                FROM products p
                JOIN platforms pl ON pl.id = p.platform_id
                LEFT JOIN categories c ON c.id = p.category_id
                LEFT JOIN sellers seller ON seller.id = p.seller_id
                LEFT JOIN product_scores s ON s.product_id = p.id
                WHERE p.is_active = TRUE
                  AND LOWER(p.title) NOT LIKE 'producto amazon %%'
                  AND LOWER(p.title) NOT LIKE 'producto aliexpress %%'
                  AND LOWER(p.title) NOT LIKE 'producto url-%%'
                  AND LOWER(p.title) <> 'producto'
                  AND LOWER(p.title) NOT LIKE '%%oferta destacada global%%'
                  AND LOWER(p.title) NOT LIKE '%%producto verificado con%%'
                  AND LOWER(p.title) NOT LIKE '%%titulo real del producto%%'
                  AND LOWER(p.title) NOT LIKE '%%nombre real del producto%%'
                  AND LOWER(p.title) NOT LIKE '%%oferta destacada global%%'
                  AND LOWER(p.title) NOT LIKE '%%producto verificado con%%'
                  AND (p.title ILIKE %s OR COALESCE(c.name, '') ILIKE %s)
                ORDER BY COALESCE(s.opportunity_score, 0) DESC, p.updated_at DESC
                LIMIT %s
                """,
                (f"%{term}%", f"%{term}%", limit),
            )
            rows = cur.fetchall()
            if rows:
                return rows
    finally:
        conn.close()

    return []

def _products_by_ids(product_ids, limit=8):
    if not product_ids:
        return []
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.title, p.current_price, p.currency, p.product_url, p.affiliate_url, p.image_url, p.image_gallery,
                       p.description, p.source_metadata, seller.name AS seller_name, p.source_metadata->>'seller_url' AS seller_url,
                       p.rating, p.reviews_count, p.sales_estimate,
                       pl.name AS platform, c.name AS category,
                       COALESCE(s.price_score, 0) AS price_score,
                       COALESCE(s.demand_score, 0) AS demand_score,
                       COALESCE(s.trend_score, 0) AS trend_score,
                       COALESCE(s.opportunity_score, 0) AS opportunity_score
                FROM products p
                JOIN platforms pl ON pl.id = p.platform_id
                LEFT JOIN categories c ON c.id = p.category_id
                LEFT JOIN sellers seller ON seller.id = p.seller_id
                LEFT JOIN product_scores s ON s.product_id = p.id
                WHERE p.is_active = TRUE AND p.id = ANY(%s)
                ORDER BY COALESCE(s.opportunity_score, 0) DESC
                LIMIT %s
                """,
                (product_ids, limit),
            )
            return cur.fetchall()
    finally:
        conn.close()


def _add_url(argument, title=None):
    parts = [part.strip() for part in (argument or "").split("|")]
    if not parts or not parts[0]:
        return None, (
            "Uso: /agregar URL\n"
            "Opcional: /agregar categoría|URL|precio|título|imagen|moneda"
        ), None

    if parts[0].lower() in ALLOWED_CATEGORIES:
        category = parts[0].lower()
        if len(parts) < 2 or not parts[1]:
            return None, "Incluye la URL después de la categoría.", None
        url = parts[1]
        details = parts[2:]
    else:
        category = None
        url = parts[0]
        details = parts[1:]


    try:
        price = float(details[0].replace(",", ".")) if details and details[0] else None
        if price is not None and (not math.isfinite(price) or price <= 0):
            return None, "El precio debe ser un número mayor que cero.", None
        product = import_url(
            url,
            category,
            title=title or (details[1] if len(details) > 1 and details[1] else None),
            price=price,
            image_url=details[2] if len(details) > 2 and details[2] else None,
            currency=details[3] if len(details) > 3 and details[3] else None,
        )
        product = enrich_product(product)
        category = category or product.get("category")
        if product["platform"] not in ALLOWED_PLATFORMS:
            return None, "Plataforma no habilitada para importación.", None

        conn = get_connection()
        try:
            product_id = upsert_product(conn, product["platform"], category, product)
            score = None
            if product.get("price") is not None and product.get("currency") not in (None, "", "UNK") and category and product.get("image_url") and product.get("title") and not _is_placeholder_title(product["title"]):
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT AVG(p.current_price) AS category_avg
                        FROM products p
                        WHERE p.category_id = (SELECT id FROM categories WHERE name = %s)
                          AND p.currency = %s AND p.current_price IS NOT NULL AND p.is_active = TRUE
                        """,
                        (category, product["currency"]),
                    )
                    row = cur.fetchone()
                score = score_product(conn, product_id, float(row["category_avg"] or product["price"]))
            return product_id, product, score
        finally:
            conn.close()
    except (TypeError, ValueError) as exc:
        return None, str(exc) or "Precio no válido. Usa un número, por ejemplo 29.99.", None
    except Exception as exc:
        print(f"[telegram-bot] url import error: {type(exc).__name__}")
        return None, "No fue posible agregar el producto desde la URL.", None


def _model_product(argument):
    parts = [part.strip() for part in argument.split("|")]
    if len(parts) < 10:
        return None, "Uso: plataforma|categoría|id|título|precio|url|rating|reseñas|ventas|imagen"
    platform, category = parts[0].lower(), parts[1].lower()
    if platform not in ALLOWED_PLATFORMS or category not in ALLOWED_CATEGORIES:
        return None, "Plataforma o categoría no permitida."
    try:
        price = float(parts[4])
        if not math.isfinite(price) or price <= 0:
            return None, "El precio debe ser un número mayor que cero."
        if not parts[3] or parts[3].startswith("Producto ") or not parts[9]:
            return None, "Para modelar se requiere título real e imagen del producto."
        product = {
            "external_id": parts[2],
            "title": parts[3],
            "price": price,
            "currency": "USD",
            "product_url": parts[5],
            "rating": float(parts[6]) if parts[6] else None,
            "reviews_count": int(parts[7] or 0),
            "sales_estimate": int(parts[8] or 0),
            "image_url": parts[9] or None,
            "source_metadata": {
                "metadata_source": "telegram_manual",
                "field_sources": {
                    "title": "user",
                    "price": "user",
                    "currency": "user",
                    "rating": "user" if parts[6] else None,
                    "reviews_count": "user" if parts[7] else None,
                    "sales_estimate": "user" if parts[8] else None,
                    "image_url": "user",
                },
            },
        }
        conn = get_connection()
        try:
            product_id = upsert_product(conn, platform, category, product)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT AVG(p.current_price) AS category_avg
                    FROM products p
                    JOIN categories c ON c.id = p.category_id
                    WHERE c.name = %s AND p.currency = %s
                      AND p.current_price IS NOT NULL AND p.is_active = TRUE
                    """,
                    (category, product["currency"]),
                )
                row = cur.fetchone()
            score = score_product(conn, product_id, float(row["category_avg"] or product["price"]))
            return product_id, score
        finally:
            conn.close()
    except Exception as exc:
        print(f"[telegram-bot] product modeling error: {type(exc).__name__}")
        return None, "No fue posible guardar el producto. Verifica los datos e inténtalo de nuevo."


def _model_active_products():
    """Puntúa el catálogo local sin consultar Amazon ni AliExpress."""
    conn = get_connection()
    try:
        enriched = enrich_pending_products(conn, limit=20, max_workers=4, force=True)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id, p.current_price,
                       COALESCE((
                           SELECT AVG(p2.current_price)
                           FROM products p2
                           WHERE p2.category_id = p.category_id
                             AND p2.currency = p.currency
                             AND p2.is_active = TRUE
                             AND p2.current_price IS NOT NULL
                       ), p.current_price) AS category_avg
                FROM products p
                WHERE p.is_active = TRUE
                  AND (p.catalog_expires_at IS NULL OR p.catalog_expires_at > NOW())
                  AND COALESCE(p.source_metadata->>'catalog_status', 'current') <> 'stale'
                  AND p.current_price IS NOT NULL
                  AND p.current_price > 0
                  AND p.currency NOT IN ('UNK', 'N/D')
                  AND p.category_id IS NOT NULL
                  AND p.title IS NOT NULL
                  AND LOWER(p.title) NOT LIKE 'producto amazon %%'
                  AND LOWER(p.title) NOT LIKE 'producto aliexpress %%'
                  AND LOWER(p.title) NOT LIKE 'producto url-%%'
                  AND LOWER(p.title) <> 'producto'
                  AND LOWER(p.title) NOT LIKE '%%oferta destacada global%%'
                  AND LOWER(p.title) NOT LIKE '%%producto verificado con%%'
                  AND LOWER(p.title) NOT LIKE '%%titulo real del producto%%'
                  AND LOWER(p.title) NOT LIKE '%%nombre real del producto%%'
                  AND LOWER(p.title) NOT LIKE '%%oferta destacada global%%'
                  AND p.image_url IS NOT NULL
                  AND p.image_url <> ''
                ORDER BY p.updated_at DESC
                LIMIT 500
                """
            )
            products = cur.fetchall()
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE current_price IS NOT NULL
                          AND current_price > 0
                          AND currency NOT IN ('UNK', 'N/D')
                          AND category_id IS NOT NULL
                          AND title IS NOT NULL
                          AND LOWER(title) NOT LIKE 'producto amazon %%'
                          AND LOWER(title) NOT LIKE 'producto aliexpress %%'
                          AND LOWER(title) NOT LIKE 'producto url-%%'
                          AND LOWER(title) NOT IN ('producto', 'producto amazon', 'producto aliexpress', 'producto url')
                          AND LOWER(title) <> 'producto'
                          AND LOWER(title) NOT LIKE '%%oferta destacada global%%'
                          AND LOWER(title) NOT LIKE '%%producto verificado con%%'
                          AND LOWER(title) NOT LIKE '%%titulo real del producto%%'
                          AND LOWER(title) NOT LIKE '%%nombre real del producto%%'
                          AND LOWER(title) NOT LIKE '%%oferta destacada global%%'
                          AND image_url IS NOT NULL
                          AND image_url <> ''
                    ) AS eligible,
                    COUNT(*) AS active_total
                FROM products
                WHERE is_active = TRUE
                """
            )
            counts = cur.fetchone()
            eligible_total = int(counts["eligible"] or 0)
            active_total = int(counts["active_total"] or 0)
            cur.execute("""
                SELECT
                    COUNT(*) AS recovery,
                    COUNT(*) FILTER (WHERE source_metadata->>'enrichment_status' IN ('retry','partial','blocked')) AS retry
                FROM products
                WHERE is_active = TRUE AND product_url IS NOT NULL
                  AND (current_price IS NULL OR image_url IS NULL OR LOWER(title) LIKE 'producto amazon %%' OR LOWER(title) LIKE 'producto aliexpress %%' OR LOWER(title) LIKE 'producto url-%%' OR LOWER(title) IN ('producto', 'producto amazon', 'producto aliexpress', 'producto url') OR LOWER(title) LIKE '%%oferta destacada global%%' OR LOWER(title) LIKE '%%producto verificado con%%' OR LOWER(title) LIKE '%%titulo real del producto%%' OR LOWER(title) LIKE '%%nombre real del producto%%' OR LOWER(COALESCE(image_url, '')) LIKE '%%enlace-de-la-imagen.jpg%%' OR LOWER(COALESCE(image_url, '')) LIKE '%%placeholder%%' OR LOWER(COALESCE(image_url, '')) LIKE '%%example.com%%' OR category_id IS NULL)
            """)
            enrichment_counts = cur.fetchone() or {}
            recovery = int(enrichment_counts.get("recovery") or 0)
            retry = int(enrichment_counts.get("retry") or 0)
        scored = 0
        errors = 0
        for product in products:
            try:
                score_product(conn, product["id"], float(product["category_avg"] or product["current_price"]))
                scored += 1
            except Exception as exc:
                errors += 1
                print(f"[telegram-bot] modelado fallido para producto {product['id']}: {type(exc).__name__}")
        missing_data = max(active_total - eligible_total, 0)
        remaining = max(eligible_total - len(products), 0)
        return scored, missing_data, errors, remaining, enriched, recovery, retry
    finally:
        conn.close()


def _handle(token, chat_id, text):
    match = re.match(r"^\s*/\s*([a-zA-Z_]+)(?:@\w+)?(?:\s+(.*))?$", text or "")
    if match:
        command = "/" + match.group(1).lower()
        argument = (match.group(2) or "").strip()
    else:
        command, _, argument = (text or "").partition(" ")
        command = command.lower().strip()

    if not command.startswith("/"):
        url_match = re.search(r"https?://[^\s<>]+", text or "", re.I)
        if url_match:
            candidate = url_match.group(0).rstrip(".,;)")
            title = (text[:url_match.start()] + " " + text[url_match.end():]).strip()
            title = re.sub(
                r"(?i)^.*?(?:he encontrado esto en|encontr[eé] esto en)\s+(?:aliexpress|amazon)\s*:\s*",
                "",
                title,
            )
            title = re.sub(r"^[\s:–—-]+|[\s:–—-]+$", "", title)
            title = title[:500] if title and not _is_http_url(title) else None
            try:
                detect_platform(candidate)
            except ValueError:
                return _send(token, chat_id, "Envía una URL válida de Amazon o AliExpress, o usa /help.")
            result, payload, score = _add_url(candidate, title=title)
            if result is None:
                return _send(token, chat_id, "<b>No pude importar el enlace</b>\n" + html.escape(str(payload)))
            return _send_imported(token, chat_id, result, payload, score, "<b>URL recibida e importada</b>")
        return _send(token, chat_id, "Envía una URL válida de Amazon o AliExpress, o usa /help.")

    if command in ("/start", "/help"):
        return _send(token, chat_id, (
            "<b>Radar de Producto</b>\n\n"
            "/top — oportunidades con mayor score\n"
            "/buscar término — buscar en el catálogo ya importado\n"
            "/modelar — revalidar URLs públicas y puntuar el catálogo local\n"
            "/agregar URL — importar Amazon o AliExpress; admite datos manuales\n"            "/producto URL — alias de /agregar\\n"
            "Publicación — selecciona productos en Studio y usa «Publicar en Central»\n"
            "/estado — estado del catálogo\n"
            "/help — ayuda"
        ))

    if command == "/top":
        limit = 25
        if argument.strip().isdigit():
            limit = min(max(int(argument.strip()), 1), 100)
        products = _top_products(limit=limit)
        if not products:
            return _send(token, chat_id, "No hay oportunidades con score 60+ disponibles en este momento.")
        if not _send(token, chat_id, f"<b>Top oportunidades (score 60+, mostrando {len(products)})</b>"):
            return False
        for i, product in enumerate(products, 1):
            product["title"] = f"{i}. {product['title']}"
            if not _send_product(token, chat_id, product):
                return False
        return True

    if command == "/buscar":
        term = argument.strip()
        if not term:
            products = _search_products("", limit=8)
            if not products:
                return _send(token, chat_id, "No hay productos con título comercial en el catálogo activo. Envía una URL de Amazon/AliExpress para agregar uno.")
            if not _send(token, chat_id, f"<b>Catálogo importado ({len(products)} productos)</b>"):
                return False
            for index, product in enumerate(products, 1):
                product["title"] = f"{index}. {product['title']}"
                if not _send_product(token, chat_id, product):
                    return False
            return True
        if _is_http_url(term):
            result, payload, score = _add_url(term)
            if result is None:
                return _send(token, chat_id, "<b>No pude importar el enlace</b>\n" + html.escape(str(payload)))
            return _send_imported(token, chat_id, result, payload, score, "<b>Producto importado desde URL</b>")
        products = _search_products(term[:100], limit=8)
        if not products:
            return _send(token, chat_id, "No hay coincidencias en el catálogo importado. Envía la URL del producto o usa /agregar URL para incorporarlo.")
        if not _send(token, chat_id, f"<b>Resultados del catálogo ({len(products)})</b>"):
            return False
        for index, product in enumerate(products, 1):
            product["title"] = f"{index}. {product['title']}"
            if not _send_product(token, chat_id, product):
                return False
        return True

    if command in ("/agregar", "/producto"):
        if not argument.strip():
            return _send(
                token,
                chat_id,
                "Uso: /agregar URL\n"
                "Opcional: /agregar categoría|URL|precio|título|imagen|moneda",
            )
        result, payload, score = _add_url(argument.strip())
        if result is None:
            return _send(token, chat_id, f"<b>Error de importación</b>\n{html.escape(str(payload))}")
        return _send_imported(token, chat_id, result, payload, score, "<b>Producto agregado</b>")

    if command == "/publicar":
        return _send(
            token,
            chat_id,
            "La publicación se gestiona desde Studio: selecciona el producto y usa "
            "«Publicar en Central». Telegram no publica directamente.",
        )

    if command == "/modelar":
        if not argument.strip():
            try:
                if not _send(token, chat_id, "Iniciando modelado del catálogo local..."):
                    return False
                scored, missing_price, errors, remaining, enriched, recovery, retry = _model_active_products()
                return _send(
                    token, chat_id,
                    "<b>Modelado del catálogo</b>\n"
                    f"Productos puntuados: {scored}\n"
                    f"Sin datos suficientes: {missing_price}\n"
                    f"Pendientes de recuperación: {recovery}\n"
                    f"Reintentos pendientes: {retry}\n"
                    f"Pendientes por límite de lote: {remaining}\n"
                    f"Datos recuperados desde URLs: {enriched}\n"
                    f"Errores al puntuar: {errors}\n"
                    "El sistema recupera metadatos de páginas públicas por URL; no consulta APIs externas de búsqueda o detalle de marketplaces."
                )
            except Exception as exc:
                print(f"[telegram-bot] batch modeling error: {type(exc).__name__}: {exc}")
                return _send(token, chat_id, "No pude modelar el catálogo. Revisa la conexión a PostgreSQL y los logs.")
        result, error = _model_product(argument.strip())
        if result is None:
            return _send(token, chat_id, f"<b>Error de modelado</b>\n{html.escape(str(error))}")
        return _send(token, chat_id, f"<b>Producto modelado</b>\nID: {result}\nOportunidad: {float(error):.1f}/100")

    if command == "/estado":
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) AS total,
                           COUNT(*) FILTER (WHERE is_active = TRUE) AS active,
                           COUNT(*) FILTER (WHERE is_active = FALSE) AS inactive,
                           COUNT(*) FILTER (WHERE source_metadata->>'catalog_status' = 'stale' OR (catalog_expires_at IS NOT NULL AND catalog_expires_at <= NOW())) AS stale,
                           COUNT(*) FILTER (WHERE product_url IS NOT NULL AND product_url <> '') AS with_url
                    FROM products
                """)
                counts = cur.fetchone()
                total = int(counts["total"] or 0)
                active = int(counts["active"] or 0)
                inactive = int(counts["inactive"] or 0)
                stale = int(counts["stale"] or 0)
                with_url = int(counts["with_url"] or 0)
                cur.execute(
                    """
                    SELECT COUNT(DISTINCT p.id) AS modeled
                    FROM products p
                    JOIN product_scores s ON s.product_id = p.id
                    WHERE p.is_active = TRUE AND p.current_price IS NOT NULL AND p.currency NOT IN ('UNK', 'N/D')
                      AND COALESCE(p.source_metadata->>'catalog_status', 'current') <> 'stale'
                      AND (p.catalog_expires_at IS NULL OR p.catalog_expires_at > NOW())
                    """
                )
                modeled = cur.fetchone()["modeled"]
            message = (
                f"<b>Estado del catálogo</b>\nProductos registrados: {total}\n"
                f"Productos activos: {active}\nProductos inactivos: {inactive}\n"
                f"Productos por revalidar: {stale}\nProductos con URL: {with_url}\n"
                f"Productos modelados: {modeled}"
            )
            if not total:
                message += "\nAgrega el primer producto con /agregar URL."
            return _send(token, chat_id, message)
        finally:
            conn.close()

    return _send(token, chat_id, "Comando no reconocido. Usa /help.")


def _clear_webhook(token):
    try:
        response = requests.post(
            f"{API_BASE}/bot{token}/deleteWebhook",
            json={"drop_pending_updates": False},
            timeout=10,
        )
        payload = response.json()
        if not response.ok or not isinstance(payload, dict) or payload.get("ok") is not True:
            print(f"[telegram-bot] no se pudo limpiar webhook: HTTP {response.status_code}")
            return False
        return True
    except (requests.RequestException, ValueError) as exc:
        print(f"[telegram-bot] error limpiando webhook: {type(exc).__name__}")
        return False


def _acquire_polling_lock():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s) AS locked", (834271,))
            locked = bool(cur.fetchone()["locked"])
        if not locked:
            conn.close()
            return None
        return conn
    except Exception:
        conn.close()
        raise


def run_bot():
    token = _token()
    if not token:
        print("[telegram-bot] Bot deshabilitado: falta TELEGRAM_BOT_TOKEN.")
        return
    if not (_env("TELEGRAM_CHAT_ID") or _env("CHAT_ID")):
        print("[telegram-bot] Bot deshabilitado: falta TELEGRAM_CHAT_ID.")
        return

    lock_conn = None
    while lock_conn is None:
        try:
            lock_conn = _acquire_polling_lock()
        except Exception as exc:
            # El detalle de la excepción SQL permite diagnosticar el bloqueo sin imprimir credenciales.
            detail = " ".join(str(exc).split())[:240]
            print(f"[telegram-bot] No se pudo obtener el bloqueo de polling ({type(exc).__name__}: {detail}); se reintentará.")
        if lock_conn is None:
            print("[telegram-bot] Bloqueo de polling ocupado; se reintentará sin detener el servicio.")
            time.sleep(max(POLL_INTERVAL, 10))

    try:
        _clear_webhook(token)
        offset = None
        print("[telegram-bot] Bot interactivo iniciado.")

        while True:
            try:
                params = {"timeout": 25}
                if offset is not None:
                    params["offset"] = offset
                response = requests.get(
                    f"{API_BASE}/bot{token}/getUpdates",
                    params=params,
                    timeout=35,
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or payload.get("ok") is not True:
                    raise RuntimeError("Respuesta inválida de Telegram")
                for update in payload.get("result", []):
                    if not isinstance(update, dict) or not isinstance(update.get("update_id"), int):
                        print("[telegram-bot] update inválido recibido; se ignora.")
                        continue
                    offset = update["update_id"] + 1
                    chat_id = None
                    try:
                        message = update.get("message") or {}
                        chat = message.get("chat") or {}
                        text = message.get("text") or ""
                        chat_id = chat.get("id")
                        if chat_id is not None and _allowed_chat(chat_id) and text:
                            _handle(token, chat_id, text)
                    except Exception as exc:
                        print(f"[telegram-bot] error procesando mensaje: {type(exc).__name__}")
                        if chat_id is not None:
                            _send(token, chat_id, "No pude procesar el mensaje. Usa /help para ver los comandos.")
            except requests.HTTPError as exc:
                response = getattr(exc, "response", None)
                status = response.status_code if response is not None else "?"
                if status == 409:
                    print("[telegram-bot] polling bloqueado por otro consumidor externo.")
                    time.sleep(max(POLL_INTERVAL, 20))
                else:
                    print(f"[telegram-bot] polling HTTP error: {status}")
                    time.sleep(max(POLL_INTERVAL, 5))
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                print(f"[telegram-bot] polling error: {type(exc).__name__}")
                time.sleep(max(POLL_INTERVAL, 5))
    finally:
        lock_conn.close()
        