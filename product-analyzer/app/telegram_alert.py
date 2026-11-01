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

POLL_INTERVAL = int(os.getenv("TELEGRAM_POLL_INTERVAL", "3"))
API_BASE = "https://api.telegram.org"
ALLOWED_PLATFORMS = {"amazon", "aliexpress"}
ALLOWED_CATEGORIES = {"ropa", "calzado", "accesorios"}


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


def _format_product(p):
    title = html.escape(str(p.get("title") or "Sin título")[:90])
    price = p.get("current_price")
    currency = html.escape(str(p.get("currency") or "")[:10])
    platform = html.escape(str(p.get("platform") or "N/D")[:40])
    category = html.escape(str(p.get("category") or "N/D")[:40])
    rating = html.escape(str(p.get("rating") if p.get("rating") is not None else "N/D")[:20])
    reviews = html.escape(str(p.get("reviews_count") if p.get("reviews_count") is not None else "N/D")[:20])
    sales = html.escape(str(p.get("sales_estimate") if p.get("sales_estimate") is not None else "N/D")[:20])
    opportunity = p.get("opportunity_score")
    price_score = p.get("price_score")
    demand_score = p.get("demand_score")
    trend_score = p.get("trend_score")
    url = _product_link(p.get("affiliate_url") or p.get("product_url"))

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
    if url:
        lines.append(f'<a href="{html.escape(url, quote=True)}">Ver producto</a>')
    return "\n".join(lines)


def _send_product(token, chat_id, product):
    image_url = str(product.get("image_url") or "").strip()
    text = _format_product(product)
    if _is_http_url(image_url) and len(text) <= 1024:
        try:
            r = requests.post(
                f"{API_BASE}/bot{token}/sendPhoto",
                json={
                    "chat_id": chat_id,
                    "photo": image_url,
                    "caption": text[:1024],
                    "parse_mode": "HTML",
                },
                timeout=15,
            )
            payload = r.json()
            if r.ok and isinstance(payload, dict) and payload.get("ok") is True:
                return True
            print(f"[telegram-bot] sendPhoto rejected: HTTP {r.status_code}")
        except (requests.RequestException, ValueError) as exc:
            print(f"[telegram-bot] sendPhoto error: {type(exc).__name__}")
    return _send(token, chat_id, text)


def _top_products(limit=25):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.title, p.current_price, p.currency, p.product_url, p.affiliate_url, p.image_url,
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
                  AND COALESCE(s.opportunity_score, 0) >= 60
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
                SELECT p.title, p.current_price, p.currency, p.product_url, p.affiliate_url, p.image_url,
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
                  AND p.title ILIKE %s
                ORDER BY COALESCE(s.opportunity_score, 0) DESC
                LIMIT %s
                """,
                (f"%{term}%", limit),
            )
            rows = cur.fetchall()
            if rows:
                return rows
    finally:
        conn.close()

    return []

def _add_url(argument):
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
        category = "accesorios"
        url = parts[0]
        details = parts[1:]

    if category not in ALLOWED_CATEGORIES:
        return None, "Categoría no válida.", None

    try:
        price = float(details[0]) if details and details[0] else None
        if price is not None and (not math.isfinite(price) or price <= 0):
            return None, "El precio debe ser un número mayor que cero.", None
        product = import_url(
            url,
            category,
            title=details[1] if len(details) > 1 and details[1] else None,
            price=price,
            image_url=details[2] if len(details) > 2 and details[2] else None,
            currency=details[3] if len(details) > 3 and details[3] else "USD",
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


def _handle(token, chat_id, text):
    command, _, argument = text.partition(" ")
    command = command.split("@", 1)[0].lower().strip()

    if not command.startswith("/"):
        for candidate in text.split():
            candidate = candidate.strip().rstrip(".,;)")
            try:
                detect_platform(candidate)
            except ValueError:
                continue
            result, payload, score = _add_url("accesorios|" + candidate)
            if result is None:
                return _send(token, chat_id, "<b>No pude importar el enlace</b>\n" + html.escape(str(payload)))
            score_text = f"Score: {float(score):.1f}/100" if score is not None else "Score pendiente: envía precio con /agregar"
            message = (
                "<b>URL recibida e importada</b>\n"
                + html.escape(str(payload["title"])[:250]) + "\n"
                + "Plataforma: " + html.escape(payload["platform"]) + "\n"
                + score_text + "\n"
                + (
                    '<a href="' + html.escape(link, quote=True) + '">Abrir producto original</a>'
                    if (link := _product_link(payload.get("affiliate_url") or payload["product_url"]))
                    else "Enlace original no disponible"
                )
            )
            return _send(token, chat_id, message)
        return _send(token, chat_id, "Envía una URL válida de Amazon o AliExpress, o usa /help.")

    if command in ("/start", "/help"):
        return _send(token, chat_id, (
            "<b>Radar de Producto</b>\n\n"
            "/top — oportunidades con mayor score\n"
            "/buscar producto — buscar en los datos modelados\n"
            "/modelar plataforma|categoría|id|título|precio|url|rating|reseñas|ventas|imagen\n"
            "/agregar URL — importar Amazon o AliExpress (categoría predeterminada: accesorios)\n"
            "/agregar categoría|URL|precio|título|imagen|moneda\n"
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
            return _send(token, chat_id, "Uso: /buscar zapatillas")
        term = term[:100]
        products = _search_products(term)
        if not products:
            return _send(
                token,
                chat_id,
                f"No encontré productos modelados para: {html.escape(term)}\n"
                "Puedes agregar uno con /agregar URL.",
            )
        if not _send(token, chat_id, f"<b>Resultados: {html.escape(term)}</b>"):
            return False
        for product in products:
            if not _send_product(token, chat_id, product):
                return False
        return True

    if command == "/agregar":
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
        score_text = f"Score: {float(score):.1f}/100" if score is not None else "Score: pendiente (sin precio)"
        link = _product_link(payload.get("affiliate_url") or payload["product_url"])
        message = (
            f"<b>Producto agregado</b>\n{html.escape(str(payload['title'])[:250])}\n"
            f"Plataforma: {html.escape(payload['platform'])}\n{score_text}\n"
            + (
                f'<a href="{html.escape(link, quote=True)}">Abrir producto original</a>'
                if link
                else "Enlace original no disponible"
            )
        )
        if not link:
            message += "\nAfiliación pendiente: revisa las credenciales de la tienda."
        image = _product_link(payload.get("image_url"))
        if image:
            message += f'\nImagen: <a href="{html.escape(image, quote=True)}">ver imagen</a>'
        return _send(token, chat_id, message)

    if command == "/modelar":
        if not argument.strip():
            return _send(
                token,
                chat_id,
                "Uso: /modelar plataforma|categoría|id|título|precio|url|rating|reseñas|ventas|imagen",
            )
        result, error = _model_product(argument.strip())
        if result is None:
            return _send(token, chat_id, f"<b>Error de modelado</b>\n{html.escape(str(error))}")
        return _send(token, chat_id, f"<b>Producto modelado</b>\nID: {result}\nOportunidad: {float(error):.1f}/100")

    if command == "/estado":
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS total FROM products WHERE is_active = TRUE")
                total = cur.fetchone()["total"]
                cur.execute(
                    """
                    SELECT COUNT(*) AS modeled
                    FROM products p
                    JOIN product_scores s ON s.product_id = p.id
                    WHERE p.is_active = TRUE
                    """
                )
                modeled = cur.fetchone()["modeled"]
            message = (
                f"<b>Estado del catálogo</b>\nProductos activos: {total}\n"
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
