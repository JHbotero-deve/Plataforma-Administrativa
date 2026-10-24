import base64
import html
import io
import json
import os
import re
import threading
import time
from urllib.parse import quote, urlparse

import requests
from PIL import Image, ImageOps, UnidentifiedImageError

from analysis import score_product
from catalog_rules import discount_percentage
from central_agents import audit_catalog, build_ad_copy
from db import get_connection, upsert_product
from product_search import search_external_products
from url_import import detect_platform, import_url

POLL_INTERVAL = int(os.getenv("TELEGRAM_POLL_INTERVAL", "3"))
API_BASE = "https://api.telegram.org"
CENTRAL_API_BASE = (os.getenv("CENTRAL_API_BASE") or "https://gracious-renewal-production-76df.up.railway.app").rstrip("/")
ALLOWED_PLATFORMS = {"amazon", "tiktok", "aliexpress", "personal"}
ALLOWED_CATEGORIES = {"ropa", "calzado", "accesorios", "electronica", "hogar", "fitness", "otros"}
_CATALOG_INGESTION_LOCK = threading.Lock()


def _token():
    return (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or "").strip()


def _allowed_chat(chat_id):
    configured = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")
    if not configured:
        print("[telegram-bot] Bot bloqueado: TELEGRAM_CHAT_ID no está configurado.")
        return False
    return str(chat_id) == str(configured).strip()


def _send(token, chat_id, text):
    try:
        r = requests.post(
            f"{API_BASE}/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text[:4096],
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=10,
        )
        payload = r.json()
        if not r.ok or not payload.get("ok"):
            print(f"[telegram-bot] send rejected: {payload.get('description', 'respuesta inválida')}")
            return False
        return True
    except (requests.RequestException, ValueError) as exc:
        print(f"[telegram-bot] send error: {type(exc).__name__}")
        return False


def _format_product(p):
    title = html.escape(str(p.get("title") or "Sin título")[:90])
    price = p.get("current_price", p.get("price"))
    discount = discount_percentage(price, p.get("previous_price"))
    currency = html.escape(str(p.get("currency") or ""))
    platform = html.escape(str(p.get("platform") or "N/D"))
    category = html.escape(str(p.get("category") or "N/D"))
    rating = p.get("rating")
    reviews = p.get("reviews_count")
    sales = p.get("sales_estimate")
    opportunity = p.get("opportunity_score")
    price_score = p.get("price_score")
    demand_score = p.get("demand_score")
    trend_score = p.get("trend_score")
    url = str(p.get("affiliate_url") or p.get("product_url") or "").strip()

    lines = [
        f"<b>{title}</b>",
        *(
            [f"ID: {int(p['product_id'])}", f"Publicar: /publicar {int(p['product_id'])}"]
            if p.get("product_id") is not None
            else []
        ),
        f"Precio: {price if price is not None else 'N/D'} {currency}".strip(),
        *([f"Descuento verificado: {discount:.1f}%"] if discount is not None else []),
        f"Oportunidad: {round(float(opportunity), 1) if opportunity is not None else 'N/D'}/100",
        (
            "Modelo: "
            f"precio {round(float(price_score), 1) if price_score is not None else 'N/D'}, "
            f"demanda {round(float(demand_score), 1) if demand_score is not None else 'N/D'}, "
            f"tendencia {round(float(trend_score), 1) if trend_score is not None else 'N/D'}"
        ),
        f"Rating: {rating if rating is not None else 'N/D'} | Reseñas: {reviews if reviews is not None else 'N/D'}",
        f"Ventas estimadas: {sales if sales is not None else 'N/D'}",
        f"Plataforma: {platform}",
        f"Categoría: {category}",
    ]
    parsed = urlparse(url)
    if parsed.scheme in ("http", "https") and parsed.netloc:
        lines.append(f'<a href="{html.escape(url, quote=True)}">Ver producto</a>')
    return "\n".join(lines)


def _send_product(token, chat_id, product):
    image_url = str(product.get("image_url") or "").strip()
    text = _format_product(product)
    if image_url.startswith("mongo://"):
        image_url = CENTRAL_API_BASE + "/api/v1/media/image?url=" + quote(image_url, safe="")
    if image_url.startswith(("http://", "https://")):
        try:
            image_response = requests.get(
                image_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; CentralTelegram/1.0)",
                    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                },
                timeout=15,
                allow_redirects=True,
            )
            image_response.raise_for_status()
            data = image_response.content
            if data and len(data) <= 10 * 1024 * 1024:
                with Image.open(io.BytesIO(data)) as source:
                    source.seek(0)
                    source.load()
                    # Telegram puede rechazar fotos extremas aunque estén en JPEG.
                    # Respetar orientación EXIF y encajar el producto en un lienzo cuadrado.
                    image = ImageOps.exif_transpose(source)
                    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
                        rgba = image.convert("RGBA")
                        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                        background.alpha_composite(rgba)
                        image = background.convert("RGB")
                    else:
                        image = image.convert("RGB")
                    image.thumbnail((1000, 1000), Image.Resampling.LANCZOS)
                    canvas = Image.new("RGB", (1024, 1024), "white")
                    canvas.paste(image, ((1024 - image.width) // 2, (1024 - image.height) // 2))
                    output = io.BytesIO()
                    canvas.save(output, format="JPEG", quality=88, optimize=True)
                    normalized = output.getvalue()
                r = requests.post(
                    f"{API_BASE}/bot{token}/sendPhoto",
                    data={"chat_id": str(chat_id), "caption": text[:1024], "parse_mode": "HTML"},
                    files={"photo": ("product.jpg", normalized, "image/jpeg")},
                    timeout=25,
                )
                payload = r.json()
                if r.ok and payload.get("ok"):
                    return True
                print(f"[telegram-bot] sendPhoto upload rejected: {payload.get('description', 'respuesta inválida')}")
            else:
                print("[telegram-bot] imagen omitida: vacía o supera 10 MB")
        except (requests.RequestException, ValueError, OSError, UnidentifiedImageError) as exc:
            print(f"[telegram-bot] image upload error: {type(exc).__name__}")
    return _send(token, chat_id, text)


def _top_products(limit=5):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id AS product_id, p.title, p.current_price, p.previous_price, p.currency, p.product_url, p.affiliate_url, p.image_url,
                       p.rating, p.reviews_count, p.sales_estimate,
                       pl.name AS platform, c.name AS category,
                       COALESCE(s.price_score, 0) AS price_score,
                       COALESCE(s.demand_score, 0) AS demand_score,
                       COALESCE(s.trend_score, 0) AS trend_score,
                       COALESCE(s.opportunity_score, 0) AS opportunity_score
                FROM products p
                JOIN platforms pl ON pl.id = p.platform_id
                LEFT JOIN categories c ON c.id = p.category_id
                LEFT JOIN product_scores s ON s.product_id = p.id
                WHERE p.is_active = TRUE
                  AND p.is_blocked = FALSE
                  AND p.current_price > 0
                  AND (p.catalog_expires_at IS NULL OR p.catalog_expires_at > NOW())
                  AND pl.name IN ('personal', 'amazon', 'tiktok', 'aliexpress')
                ORDER BY COALESCE(s.opportunity_score, 0) DESC, p.updated_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def _catalog_products(limit=20, offset=0):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id AS product_id, p.title, p.current_price, p.previous_price, p.currency, p.product_url, p.affiliate_url, p.image_url,
                       p.rating, p.reviews_count, p.sales_estimate,
                       pl.name AS platform, c.name AS category,
                       COALESCE(s.price_score, 0) AS price_score,
                       COALESCE(s.demand_score, 0) AS demand_score,
                       COALESCE(s.trend_score, 0) AS trend_score,
                       COALESCE(s.opportunity_score, 0) AS opportunity_score
                FROM products p
                JOIN platforms pl ON pl.id = p.platform_id
                LEFT JOIN categories c ON c.id = p.category_id
                LEFT JOIN product_scores s ON s.product_id = p.id
                WHERE p.is_active = TRUE
                  AND p.is_blocked = FALSE
                  AND p.current_price > 0
                  AND (p.catalog_expires_at IS NULL OR p.catalog_expires_at > NOW())
                  AND pl.name IN ('personal', 'amazon', 'tiktok', 'aliexpress')
                ORDER BY p.updated_at DESC NULLS LAST, p.id DESC
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            )
            return cur.fetchall()
    finally:
        conn.close()


def _search_products(term, limit=8):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id AS product_id, p.title, p.current_price, p.previous_price, p.currency, p.product_url, p.affiliate_url, p.image_url,
                       p.rating, p.reviews_count, p.sales_estimate,
                       pl.name AS platform, c.name AS category,
                       COALESCE(s.price_score, 0) AS price_score,
                       COALESCE(s.demand_score, 0) AS demand_score,
                       COALESCE(s.trend_score, 0) AS trend_score,
                       COALESCE(s.opportunity_score, 0) AS opportunity_score
                FROM products p
                JOIN platforms pl ON pl.id = p.platform_id
                LEFT JOIN categories c ON c.id = p.category_id
                LEFT JOIN product_scores s ON s.product_id = p.id
                WHERE p.is_active = TRUE
                  AND p.is_blocked = FALSE
                  AND p.current_price > 0
                  AND (p.catalog_expires_at IS NULL OR p.catalog_expires_at > NOW())
                  AND p.title ILIKE %s
                ORDER BY COALESCE(s.opportunity_score, 0) DESC
                LIMIT %s
                """,
                (f"%{term}%", limit),
            )
            return cur.fetchall()
    finally:
        conn.close()


def _add_url(argument):
    parts = [part.strip() for part in argument.split("|")]
    if len(parts) < 2:
        return None, "Uso: /agregar categoría|URL|PRECIO|TÍTULO|IMAGEN_URL|MONEDA", None

    category, url = parts[0].lower(), parts[1]
    if category not in ALLOWED_CATEGORIES:
        return None, "Categoría no válida.", None

    try:
        price = float(parts[2]) if len(parts) > 2 and parts[2] else None
        platform = detect_platform(url)
        default_currency = "COP" if platform == "aliexpress" else "USD"
        currency = parts[5].upper() if len(parts) > 5 and parts[5] else default_currency
        product = import_url(
            url,
            category,
            title=parts[3] if len(parts) > 3 and parts[3] else None,
            price=price,
            currency=currency,
            image_url=parts[4] if len(parts) > 4 and parts[4] else None,
        )
        if product["platform"] not in ALLOWED_PLATFORMS:
            return None, "Plataforma no habilitada para importación.", None

        conn = get_connection()
        try:
            product_id = upsert_product(conn, product["platform"], category, product)
            score = None
            if product.get("price") is not None:
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
            return product_id, product, score
        finally:
            conn.close()
    except Exception as exc:
        detail = str(exc).strip()
        print(f"[telegram-bot] url import error: {type(exc).__name__}: {detail[:220]}")
        return None, (
            "No fue posible agregar el producto. "
            + html.escape(detail[:220])
            + " Uso: /agregar categoría|URL|PRECIO|TÍTULO|IMAGEN_URL|MONEDA."
        ), None


def _extract_urls(text):
    urls = re.findall(r"https?://[^\s<>]+", text or "")
    cleaned = (url.rstrip(".,;:!?)\\]}") for url in urls)
    return list(dict.fromkeys(url for url in cleaned if url))


def _category_from_text(text):
    value = (text or "").lower()
    groups = {
        "calzado": ("tenis", "zapatilla", "zapato", "sneaker"),
        "ropa": ("camiseta", "camisa", "pantalon", "jean", "pijama", "chaqueta"),
        "electronica": ("celular", "audifono", "audífono", "smartwatch", "mouse", "teclado", "monitor", "ssd", "webcam", "consola"),
        "hogar": ("lampara", "lámpara", "organizador", "aspiradora", "cocina"),
        "fitness": ("gimnasio", "fitness", "banda resistencia", "reloj deportivo"),
        "accesorios": ("cargador", "power bank", "soporte celular", "mochila"),
    }
    for category, terms in groups.items():
        if any(term in value for term in terms):
            return category
    return "otros"


def _context_title(context):
    """Extrae el título compartido, quitando texto promocional y precio."""
    skipped = (
        "mejores recomendaciones", "productos en oferta", "ahora precio",
        "precio:", "precio actual", "haz clic", "clic y compra", "compra:",
        "enlace:", "link:", "oferta del día", "oferta del dia",
    )
    for line in (context or "").splitlines():
        value = re.sub(r"https?://[^\s<>]+", "", line.strip(" \t•-"), flags=re.I)
        value = re.sub(r"(?i)^/?agregar\s+", "", value).strip(" \t•-:|")
        value = re.sub(r"(?i)^mira lo que encontré[!,:\s-]*", "", value)
        value = re.sub(r"(?i)\s*(?:[-–—]\s*)?\$\s*[0-9][0-9.,]*\s*(?:COP|USD|EUR)?", "", value)
        value = re.sub(r"(?i)\s*(?:en Amazon|en TikTok Shop|en AliExpress)?\.?\s*(?:Entra aquí|Ver aquí|Compra aquí)\s*:?\s*$", "", value)
        value = re.sub(r"\s+", " ", value).strip(" \t•-:|.")
        if len(value) < 5 or len(value) > 500:
            continue
        lowered = value.lower()
        if any(term in lowered for term in skipped):
            continue
        if not re.search(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", value):
            continue
        return value
    return None


def _price_from_context(context):
    text = context or ""
    match = re.search(
        r"\b(COP|COL\$|USD|US\$|EUR|CNY|RMB)\s*[:：]?\s*\$?\s*([0-9][0-9.,]*)",
        text,
        re.I,
    )
    if match:
        currency, raw = match.group(1).upper(), match.group(2)
    else:
        match = re.search(r"\$\s*([0-9][0-9.,]*)\s*(COP|USD|EUR|CNY)\b", text, re.I)
        if not match:
            return None, None
        raw, currency = match.group(1), match.group(2).upper()
    currency = {"COL$": "COP", "US$": "USD", "RMB": "CNY"}.get(currency, currency)
    if "." in raw and "," in raw:
        raw = raw.replace(".", "").replace(",", ".") if raw.rfind(",") > raw.rfind(".") else raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".") if len(raw.rsplit(",", 1)[-1]) <= 2 else raw.replace(",", "")
    elif "." in raw and len(raw.rsplit(".", 1)[-1]) == 3:
        raw = raw.replace(".", "")
    try:
        amount = float(raw)
    except ValueError:
        return None, None
    return (amount, currency) if amount > 0 else (None, None)


def _telegram_photo_data_url(token, message):
    """Recupera la foto adjunta a un mensaje para guardarla con su producto."""
    photos = message.get("photo") or []
    if not photos:
        return None
    try:
        photo = max(photos, key=lambda item: (int(item.get("width") or 0) * int(item.get("height") or 0), int(item.get("file_size") or 0)))
        info = requests.get(
            f"{API_BASE}/bot{token}/getFile",
            params={"file_id": photo["file_id"]},
            timeout=10,
        )
        info.raise_for_status()
        payload = info.json()
        if not payload.get("ok"):
            return None
        file_path = payload.get("result", {}).get("file_path")
        if not file_path:
            return None
        response = requests.get(f"{API_BASE}/file/bot{token}/{file_path}", timeout=20)
        response.raise_for_status()
        data = response.content
        content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
        if content_type not in {"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"}:
            extension = str(file_path).lower().rsplit(".", 1)[-1] if "." in str(file_path) else ""
            content_type = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                            "webp": "image/webp", "gif": "image/gif", "avif": "image/avif"}.get(extension, "")
        if not data or len(data) > 8 * 1024 * 1024 or content_type not in {
            "image/jpeg", "image/png", "image/webp", "image/gif", "image/avif",
        }:
            print("[telegram-bot] foto adjunta omitida: formato no permitido o supera 8 MB")
            return None
        return f"data:{content_type};base64," + base64.b64encode(data).decode("ascii")
    except (KeyError, requests.RequestException, ValueError) as exc:
        print(f"[telegram-bot] no se pudo leer la foto adjunta: {type(exc).__name__}")
        return None


def _ingest_telegram_url(url, context="", image_url=None):
    category = _category_from_text(context)
    title = _context_title(context)
    price, currency = _price_from_context(context)
    try:
        platform = detect_platform(url)
        if currency is None:
            currency = "USD" if platform in {"amazon", "aliexpress"} else "COP"
        product = import_url(
            url,
            category,
            title=title,
            price=price,
            currency=currency,
            image_url=image_url,
            require_image=False,
        )
        if product["platform"] not in {"amazon", "tiktok", "aliexpress", "personal"}:
            return None, "Fuente no habilitada."
        conn = get_connection()
        try:
            product_id = upsert_product(conn, product["platform"], category, product)
            score = None
            if product.get("price") is not None:
                score = score_product(conn, product_id, float(product["price"]))
        finally:
            conn.close()
        missing = []
        if not product.get("price"):
            missing.append("precio")
        if not product.get("image_url"):
            missing.append("imagen")
        if missing:
            next_step = (
                "<b>Borrador guardado, no publicado.</b> Completa "
                + " y ".join(missing)
                + " en el administrador de Central antes de publicar."
            )
        else:
            next_step = f"<b>Listo para publicar:</b> /publicar {product_id}"
        price_display = (
            f"{float(product['price']):,.0f} {html.escape(product['currency'])}"
            if product.get("price") is not None
            else "Pendiente de completar"
        )
        score_display = f"{float(score):.1f}/100" if score is not None else "Pendiente (borrador)"
        message = (
            f"<b>Producto capturado</b> · ID {product_id}\n"
            f"{html.escape(product['title'])}\n"
            f"Fuente: {html.escape(product['platform'])}\n"
            f"Precio: {price_display}\n"
            f"Score: {score_display}\n"
            f"{next_step}"
        )
        return product_id, message
    except Exception as exc:
        detail = str(exc).strip()
        print(f"[telegram-bot] captura URL fallida: {type(exc).__name__}: {detail[:220]}")
        return None, (
            "No se pudo importar el producto automáticamente. "
            + html.escape(detail[:220])
            + " Si falta información, envía la URL junto al nombre, precio con moneda y una foto del producto, "
              "o usa /agregar categoría|URL|PRECIO|TÍTULO|IMAGEN_URL|MONEDA."
        )


def _publish_product(product_id):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id,p.title,p.current_price,p.currency,p.previous_price,p.image_url,
                       p.image_gallery,p.product_url,p.affiliate_url,
                       pl.name AS platform,s.opportunity_score
                FROM products p
                JOIN platforms pl ON pl.id=p.platform_id
                LEFT JOIN product_scores s ON s.product_id=p.id
                WHERE p.id=%s AND p.is_active=TRUE AND p.is_blocked=FALSE
                  AND p.current_price>0
                  AND (p.catalog_expires_at IS NULL OR p.catalog_expires_at>NOW())
                """,
                (product_id,),
            )
            product = cur.fetchone()
            if not product:
                return None, "Producto activo no encontrado."
            image_url = product["image_url"] or ((product["image_gallery"] or [None])[0])
            product_url = product["affiliate_url"] or product["product_url"]
            if not image_url or not product_url:
                return None, "El producto necesita imagen y enlace de origen."
            discount = 0.0
            if product["previous_price"] and product["current_price"] and product["previous_price"] > product["current_price"]:
                discount = (product["previous_price"] - product["current_price"]) / product["previous_price"] * 100
            if str(product["platform"]).lower() != "personal" and discount < 1 and float(product["opportunity_score"] or 0) < 50:
                return None, "No hay una señal suficiente de oportunidad para publicar."
            price_display = f"{product['current_price']:,.0f} {product['currency'] or 'COP'}" if product["current_price"] is not None else "Consultar"
            cur.execute(
                """
                INSERT INTO published_cards (
                    product_id,title,subtitle,price_display,image_url,product_url,
                    sale_price,cost_price,profit_amount,profit_margin_pct,
                    opportunity_score,footer,accent,is_published,published_at,updated_at
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0,0,%s,%s,%s,TRUE,NOW(),NOW())
                ON CONFLICT(product_id) DO UPDATE SET
                    title=EXCLUDED.title,subtitle=EXCLUDED.subtitle,
                    price_display=EXCLUDED.price_display,image_url=EXCLUDED.image_url,
                    product_url=EXCLUDED.product_url,sale_price=EXCLUDED.sale_price,
                    cost_price=EXCLUDED.cost_price,opportunity_score=EXCLUDED.opportunity_score,
                    footer=EXCLUDED.footer,accent=EXCLUDED.accent,
                    is_published=TRUE,updated_at=NOW()
                RETURNING id
                """,
                (
                    product["id"],product["title"],
                    f"{product['platform']} · producto verificado",
                    price_display,image_url,product_url,
                    product["current_price"],product["current_price"],
                    float(product["opportunity_score"] or 0),
                    "Producto real · fuente verificada","#b6f23a"
                ),
            )
            publication_id=cur.fetchone()["id"]
        conn.commit()
        return publication_id, f"Publicado correctamente. Tarjeta {publication_id}."
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _model_product(argument):
    parts = [part.strip() for part in argument.split("|")]
    if len(parts) < 10:
        return None, "Uso: plataforma|categoría|id|título|precio|url|rating|reseñas|ventas|imagen"
    platform, category = parts[0].lower(), parts[1].lower()
    if platform not in ALLOWED_PLATFORMS or category not in ALLOWED_CATEGORIES:
        return None, "Plataforma o categoría no permitida."
    try:
        product = {
            "external_id": parts[2],
            "title": parts[3],
            "price": float(parts[4]),
            "currency": "COP" if platform in {"mercadolibre", "aliexpress"} else "USD",
            "product_url": parts[5],
            "rating": float(parts[6]) if parts[6] else None,
            "reviews_count": int(parts[7] or 0),
            "sales_estimate": int(parts[8] or 0),
            "image_url": parts[9] or None,
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
        return None, str(exc)


def _handle(token, chat_id, text):
    command_line = (text or "").splitlines()[0].strip() if (text or "").strip() else ""
    command, _, argument = command_line.partition(" ")
    command = command.lower().strip()

    if command in ("/start", "/help"):
        return _send(token, chat_id, (
            "<b>Central Radar</b>\n\n"
            "/productos — catálogo de productos activos\n"
            "/top — oportunidades activas de Amazon, TikTok Shop y AliExpress\n"
            "/buscar producto — buscar en Amazon y AliExpress\n"
            "/inyectar (alias: /inyeccion o /inyección) — buscar ofertas verificables con las APIs configuradas\n"
            "/modelar plataforma|categoría|id|título|precio|url|rating|reseñas|ventas|imagen — registrar producto con datos completos\n"
            "/agregar — iniciar importación manual; después envía la URL\n"
            "/publicar ID — publicar un producto con precio, imagen y enlace\n"
            "También puedes enviar una URL directamente. Si la tienda bloquea la lectura, Central guarda un borrador para completar.\n"
            "/auditar — revisar calidad del catálogo sin modificar productos\n"
            "/publicidad ID — generar borrador publicitario con datos guardados\n"
            "/agentes — ver herramientas CLI disponibles\n"
            "/estado — estado del catálogo"
        ))

    if command in ("/productos", "/catalogo"):
        try:
            page = max(1, min(int(argument.strip() or "1"), 500))
        except ValueError:
            return _send(token, chat_id, "Uso: /productos 1 (cada página muestra 20 productos).")
        page_size = 20
        products = _catalog_products(page_size, (page - 1) * page_size)
        if not products:
            return _send(token, chat_id, "No hay productos activos con precio válido en Amazon, TikTok Shop o AliExpress. Envía una URL de producto para crear un borrador o ejecuta /buscar audífonos bluetooth.")
        _send(token, chat_id, f"<b>Catálogo Central · página {page}</b>\\nProductos mostrados: {len(products)}\\nSiguiente página: /productos {page + 1}")
        for i, product in enumerate(products, (page - 1) * page_size + 1):
            product["title"] = f"{i}. {product['title']}"
            _send_product(token, chat_id, product)
        return True

    if command == "/top":
        products = _top_products()
        if not products:
            return _send(token, chat_id, "No hay oportunidades activas de Amazon, TikTok Shop o AliExpress. Envía una URL para importar un borrador o revisa la configuración de las APIs.")
        _send(token, chat_id, "<b>Top oportunidades</b>")
        for i, product in enumerate(products, 1):
            product["title"] = f"{i}. {product['title']}"
            _send_product(token, chat_id, product)
        return True

    if command == "/buscar":
        term = argument.strip()
        if not term:
            return _send(token, chat_id, "Uso: /buscar audífonos bluetooth")
        products = search_external_products(term, limit=8)
        if not products:
            return _send(
                token,
                chat_id,
                f"No llegaron resultados de Amazon ni AliExpress para: {html.escape(term)}. La búsqueda automática depende del acceso disponible en esas plataformas; puedes enviar una URL directa para importar el producto.",
            )

        _send(token, chat_id, f"<b>Resultados de Amazon y AliExpress para {html.escape(term)}</b> · {len(products)} productos")
        for product in products:
            _send_product(token, chat_id, product)
        return True

    if command in ("/inyectar", "/inyeccion", "/inyección"):
        if argument.strip():
            return _send(token, chat_id, "Uso: /inyectar")
        if not _CATALOG_INGESTION_LOCK.acquire(blocking=False):
            return _send(token, chat_id, "Ya hay una ingesta de ofertas en curso.")

        def run_ingestion():
            try:
                from api import _sync_store_catalog

                result = _sync_store_catalog()
                if result.get("status") == "already_running":
                    _send(token, chat_id, "Ya hay una ingesta de catálogo en curso en otro servicio.")
                    return
                imported = result.get("imported") or {}
                lines = [
                    "<b>Ingesta de catálogo finalizada</b>",
                    f"Importados: {sum(imported.values())}/{result.get('limit', 200)}",
                    f"Publicados en la tienda: {result.get('published', 0)}",
                    f"Descuento mínimo: {result.get('discount_threshold_pct', 40)}%",
                    f"Vigencia: {result.get('expiry_days', 7)} días",
                ]
                for platform in ("amazon", "aliexpress"):
                    lines.append(f"{html.escape(platform)}: {imported.get(platform, 0)}")
                rejected = result.get("rejected") or {}
                if rejected.get("missing_reference_price"):
                    lines.append(
                        "Omitidos sin precio original verificable: "
                        f"{rejected['missing_reference_price']}"
                    )
                errors = result.get("errors") or []
                if errors:
                    lines.append("Avisos: " + html.escape(" | ".join(errors[:3]))[:900])
                _send(token, chat_id, "\n".join(lines))
            except Exception as exc:
                print(f"[telegram-bot] catalog ingestion failed: {type(exc).__name__}: {str(exc)[:180]}", flush=True)
                _send(token, chat_id, "No se pudo completar la ingesta. Revisa los logs del Worker/API.")
            finally:
                _CATALOG_INGESTION_LOCK.release()

        try:
            threading.Thread(target=run_ingestion, name="telegram-catalog-ingestion", daemon=True).start()
        except RuntimeError:
            _CATALOG_INGESTION_LOCK.release()
            raise
        return _send(token, chat_id, "Inicié la búsqueda de ofertas reales. Te enviaré el resumen cuando termine.")

    if command == "/agregar":
        if not argument.strip():
            return _send(
                token,
                chat_id,
                "Envíame la URL de Amazon, TikTok Shop o AliExpress en el siguiente mensaje. "
                "Central intentará capturar los datos y guardará un borrador si faltan precio o imagen."
            )
        full_text = text or ""
        urls = _extract_urls(full_text)
        if "|" not in argument and urls:
            url = urls[0]
            context = re.sub(r"(?m)^\s*/agregar\b", "", full_text, count=1).strip()
            result, payload = _ingest_telegram_url(url, context)
            if result is None:
                return _send(token, chat_id, f"<b>Error de importación</b>\n{html.escape(str(payload))}")
            return _send(token, chat_id, payload)
        if "|" not in argument:
            title = _context_title(argument)
            if title:
                return _send(
                    token,
                    chat_id,
                    "Recibí el nombre del producto, pero falta su URL de compra. "
                    "Envía el enlace de Amazon, TikTok Shop o AliExpress en el siguiente mensaje; "
                    "también puedes enviarlo directamente sin escribir un comando."
                )

        result, payload, score = _add_url(argument.strip())
        if result is None:
            return _send(token, chat_id, f"<b>Error de importación</b>\n{html.escape(str(payload))}")
        score_text = f"Score: {float(score):.1f}/100" if score is not None else "Score: pendiente (sin precio)"
        message = (
            f"<b>Producto agregado</b>\n{html.escape(payload['title'])}\n"
            f"Plataforma: {html.escape(payload['platform'])}\n{score_text}\n"
            f'<a href="{html.escape(payload.get("affiliate_url") or payload["product_url"], quote=True)}">Abrir enlace de compra</a>'
        )
        if payload.get("image_url"):
            message += f'\nImagen: <a href="{html.escape(payload["image_url"], quote=True)}">ver imagen</a>'
        return _send(token, chat_id, message)

    if command == "/modelar":
        if not argument.strip():
            return _send(token, chat_id, "Para importar sin completar campos manualmente, usa /agregar y envía la URL. /modelar es el modo avanzado y requiere los 10 campos indicados en /help.")
        result, error = _model_product(argument.strip())
        if result is None:
            return _send(token, chat_id, f"<b>Error de modelado</b>\n{html.escape(str(error))}")
        return _send(token, chat_id, f"<b>Producto modelado</b>\nID: {result}\nOportunidad: {float(error):.1f}/100")

    if command == "/publicar":
        try:
            product_id = int(argument.strip())
        except ValueError:
            return _send(token, chat_id, "Indica el ID del producto: /publicar 123. Consulta /productos para ver los productos disponibles; solo se publican productos con precio, imagen y enlace válidos.")
        result, message = _publish_product(product_id)
        if result is None:
            return _send(token, chat_id, f"<b>No publicado</b>\n{html.escape(message)}")
        return _send(token, chat_id, f"<b>{html.escape(message)}</b>")

    if command == "/agentes":
        return _send(token, chat_id, (
            "<b>Agentes CLI de Central</b>\\n"
            "<code>python central_agents.py search \"término\"</code> — búsqueda en fuentes configuradas\\n"
            "<code>python central_agents.py audit</code> — auditoría de catálogo\\n"
            "<code>python central_agents.py ad-copy ID</code> — borrador publicitario\\n"
            "La búsqueda respeta límites de los proveedores; no evade bloqueos ni inventa resultados."
        ))

    if command == "/auditar":
        conn = get_connection()
        try:
            report = audit_catalog(conn)
        finally:
            conn.close()
        counts = report.get("issue_counts") or {}
        lines = [
            "<b>Auditoría de catálogo Central</b>",
            f"Productos revisados: {report['audited']}",
            f"Sin incidencias detectadas: {report['clean']}",
            f"Con incidencias: {report['with_issues']}",
        ]
        for key, count in sorted(counts.items()):
            lines.append(f"• {html.escape(key)}: {count}")
        if report.get("truncated"):
            lines.append("Se muestran como máximo 100 productos con incidencias.")
        lines.append("Auditoría de solo lectura; no se modificó ni publicó ningún producto.")
        return _send(token, chat_id, "\\n".join(lines))

    if command == "/publicidad":
        try:
            product_id = int(argument.strip())
        except ValueError:
            return _send(token, chat_id, "Uso: /publicidad ID. Genera un borrador; no publica automáticamente.")
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT p.id, p.title, p.description, p.current_price, p.previous_price,
                           p.currency, p.product_url, p.affiliate_url, p.category, pl.name AS platform
                    FROM products p JOIN platforms pl ON pl.id = p.platform_id
                    WHERE p.id=%s AND p.is_active=TRUE AND p.is_blocked=FALSE
                    """,
                    (product_id,),
                )
                product = cur.fetchone()
        finally:
            conn.close()
        if not product:
            return _send(token, chat_id, "Producto activo no encontrado. Consulta /productos.")
        draft = build_ad_copy(product)
        # Persistimos el borrador en los metadatos compartidos del producto para
        # que el flujo administrativo pueda recuperarlo; no se publica aquí.
        save_conn = get_connection()
        try:
            with save_conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE products
                    SET source_metadata = COALESCE(source_metadata, '{}'::jsonb)
                        || jsonb_build_object(
                            'marketing_draft', %s::jsonb,
                            'marketing_draft_updated_at', NOW()::text
                        ),
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (json.dumps(draft, ensure_ascii=False), product_id),
                )
            save_conn.commit()
        except Exception:
            save_conn.rollback()
            raise
        finally:
            save_conn.close()
        lines = [
            "<b>Borrador publicitario · requiere aprobación</b>",
            f"<b>{html.escape(draft['headline'])}</b>",
            html.escape(draft["body"]),
            html.escape(draft["cta"]),
            "<b>SEO title</b>: " + html.escape(draft["seo_title"]),
            "<b>Meta description</b>: " + html.escape(draft["meta_description"]),
            "<b>Palabras clave</b>: " + html.escape(", ".join(draft["keywords"])),
            "<b>Texto social</b>: " + html.escape(draft["social_caption"]),
            html.escape(draft["disclaimer"]),
            f"ID: {product_id} · Fuente: {html.escape(draft['platform'])}",
            "No se publicó automáticamente.",
        ]
        return _send(token, chat_id, "\\n".join(lines))

    if command == "/estado":
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        COUNT(*) FILTER (
                            WHERE p.current_price > 0
                              AND NULLIF(BTRIM(p.image_url), '') IS NOT NULL
                              AND (p.catalog_expires_at IS NULL OR p.catalog_expires_at > NOW())
                        ) AS active,
                        COUNT(*) FILTER (
                            WHERE (
                                p.current_price IS NULL
                                OR p.current_price <= 0
                                OR NULLIF(BTRIM(p.image_url), '') IS NULL
                            )
                              AND (p.catalog_expires_at IS NULL OR p.catalog_expires_at > NOW())
                        ) AS drafts
                    FROM products p
                    JOIN platforms pl ON pl.id = p.platform_id
                    WHERE p.is_active = TRUE
                      AND p.is_blocked = FALSE
                      AND pl.name IN ('personal', 'amazon', 'tiktok', 'aliexpress')
                    """
                )
                counts = cur.fetchone()
                cur.execute(
                    """
                    SELECT COUNT(*) AS modeled
                    FROM products p
                    JOIN platforms pl ON pl.id = p.platform_id
                    JOIN product_scores s ON s.product_id = p.id
                    WHERE p.is_active = TRUE
                      AND p.is_blocked = FALSE
                      AND p.current_price > 0
                      AND (p.catalog_expires_at IS NULL OR p.catalog_expires_at > NOW())
                      AND pl.name IN ('personal', 'amazon', 'tiktok', 'aliexpress')
                    """
                )
                modeled = cur.fetchone()["modeled"]
            return _send(
                token,
                chat_id,
                f"<b>Estado del catálogo Central</b>\nOfertas activas con precio: {counts['active']}\nBorradores pendientes de completar: {counts['drafts']}\nProductos modelados: {modeled}\nFuentes activas: Amazon, TikTok Shop y AliExpress",
            )
        finally:
            conn.close()

    # Acepta URLs enviadas solas o con una barra inicial accidental.
    urls = _extract_urls(text or "")
    if urls and (not command.startswith("/") or command.startswith("/http")):
        result, payload = _ingest_telegram_url(urls[0], (text or "").strip())
        if result is None:
            return _send(token, chat_id, f"<b>Error de importación</b>\n{html.escape(str(payload))}")
        return _send(token, chat_id, payload)

    return _send(token, chat_id, "Comando no reconocido. Usa /help.")


def _clear_webhook(token):
    try:
        response = requests.post(
            f"{API_BASE}/bot{token}/deleteWebhook",
            json={"drop_pending_updates": False},
            timeout=10,
        )
        payload = response.json()
        if not response.ok or not payload.get("ok"):
            print(f"[telegram-bot] no se pudo limpiar webhook: {payload.get('description', 'respuesta inválida')}")
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


def _validate_bot_token(token):
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", token or ""):
        print("[telegram-bot] token con formato inválido; usa el token del bot emitido por @BotFather, no un token de Telegram Gateway.")
        return False, True
    try:
        response = requests.get(f"{API_BASE}/bot{token}/getMe", timeout=10)
        payload = response.json()
        if response.status_code == 401 or not payload.get("ok"):
            rejected = response.status_code == 401
            if rejected:
                print("[telegram-bot] TELEGRAM_BOT_TOKEN rechazado por Telegram (HTTP 401); actualiza la variable en Railway.")
            else:
                print("[telegram-bot] Telegram no validó el token configurado.")
            return False, rejected
        response.raise_for_status()
        username = (payload.get("result") or {}).get("username") or "bot"
        print(f"[telegram-bot] token validado para @{username}.")
        return True, False
    except (requests.RequestException, ValueError) as exc:
        print(f"[telegram-bot] validación de token no disponible: {type(exc).__name__}")
        return False, False


def run_bot():
    token = _token()
    if not token:
        print("[telegram-bot] Bot deshabilitado: falta TELEGRAM_BOT_TOKEN.")
        return
    if not (os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")):
        print("[telegram-bot] Bot deshabilitado: falta TELEGRAM_CHAT_ID.")
        return

    while True:
        valid, rejected = _validate_bot_token(token)
        if valid:
            break
        time.sleep(300 if rejected else max(POLL_INTERVAL, 30))

    lock_conn = None
    while lock_conn is None:
        try:
            lock_conn = _acquire_polling_lock()
        except Exception as exc:
            print(f"[telegram-bot] no se pudo adquirir el bloqueo de polling: {type(exc).__name__}")
        if lock_conn is None:
            print("[telegram-bot] polling ocupado; se reintentará sin apagar el servicio.")
            time.sleep(max(POLL_INTERVAL, 5))

    try:
        _clear_webhook(token)
        offset = None
        print("[telegram-bot] Bot interactivo iniciado.")
        while True:
            try:
                params = {"timeout": 25}
                if offset is not None:
                    params["offset"] = offset
                response = requests.get(f"{API_BASE}/bot{token}/getUpdates", params=params, timeout=35)
                response.raise_for_status()
                payload = response.json()
                if not payload.get("ok"):
                    raise RuntimeError(payload.get("description", "respuesta inválida"))
                for update in payload.get("result", []):
                    offset = update["update_id"] + 1
                    message = update.get("message") or update.get("channel_post") or {}
                    chat = message.get("chat") or {}
                    text = message.get("text") or message.get("caption") or ""
                    chat_id = chat.get("id")
                    if chat_id is None:
                        print(f"[telegram-bot] update sin chat_id; update_id={update.get('update_id')}", flush=True)
                        continue
                    configured_chat_id = (os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID") or "").strip()
                    if str(chat_id) != configured_chat_id:
                        print(
                            f"[telegram-bot] mensaje recibido pero rechazado por chat_id; "
                            f"recibido={chat_id}, configurado={configured_chat_id}, "
                            f"update_id={update.get('update_id')}",
                            flush=True,
                        )
                        continue
                    print(
                        f"[telegram-bot] mensaje recibido; chat_id={chat_id}, "
                        f"tipo={'comando' if text.startswith('/') else 'texto'}, "
                        f"comando={(text.split()[0][:40] if text.startswith('/') else 'URL/texto')}, "
                        f"update_id={update.get('update_id')}",
                        flush=True,
                    )
                    if text.startswith("/"):
                        try:
                            _handle(token, chat_id, text)
                        except Exception as exc:
                            print(f"[telegram-bot] comando {text.split()[0][:40]} falló: {type(exc).__name__}")
                            _send(token, chat_id, "No pude completar el comando por un error interno. El servicio sigue activo; inténtalo de nuevo en unos minutos.")
                        continue
                    urls = _extract_urls(text)[:3]
                    if urls:
                        photo_data_url = _telegram_photo_data_url(token, message)
                        for url in urls:
                            clean_url = url.rstrip(".,);]}")
                            _, result = _ingest_telegram_url(clean_url, text, photo_data_url)
                            _send(token, chat_id, result)
            except requests.HTTPError as exc:
                response = getattr(exc, "response", None)
                status = response.status_code if response is not None else "?"
                if status == 409:
                    print("[telegram-bot] Telegram devolvió 409: hay otro consumidor de getUpdates. Se detiene esta instancia.")
                    return
                if status == 401:
                    print("[telegram-bot] Telegram rechazó TELEGRAM_BOT_TOKEN (HTTP 401); se revalidará en cinco minutos.")
                    time.sleep(300)
                    continue
                print(f"[telegram-bot] polling HTTP error: {status}")
                time.sleep(max(POLL_INTERVAL, 5))
            except (requests.RequestException, ValueError, RuntimeError) as exc:
                print(f"[telegram-bot] polling error: {type(exc).__name__}")
                time.sleep(max(POLL_INTERVAL, 5))
    finally:
        try:
            lock_conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    run_bot()
