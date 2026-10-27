import html
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
    # Railway puede conservar nombres de variables con espacios invisibles.
    # Prioriza cualquier valor no vacío cuyo nombre normalizado coincida.
    target = name.strip()
    for key, candidate in os.environ.items():
        if key.strip() == target and candidate is not None and str(candidate).strip():
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
        if not r.ok or not payload.get("ok"):
            print(f"[telegram-bot] send rejected: {payload.get('description', 'respuesta inválida')}")
            return False
        return True
    except (requests.RequestException, ValueError) as exc:
        print(f"[telegram-bot] send error: {exc}")
        return False


def _format_product(p):
    title = html.escape(str(p.get("title") or "Sin título")[:90])
    price = p.get("current_price")
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
        f"Precio: {price if price is not None else 'N/D'} {currency}".strip(),
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
    if image_url.startswith(("http://", "https://")):
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
            if r.ok and payload.get("ok"):
                return True
            print(f"[telegram-bot] sendPhoto rejected: {payload.get('description', 'respuesta inválida')}")
        except (requests.RequestException, ValueError) as exc:
            print(f"[telegram-bot] sendPhoto error: {exc}")
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
    raw = (argument or "").strip()
    if not raw:
        return None, "Uso: /agregar URL_DE_AMAZON_O_ALIEXPRESS o categoría|url|precio|título|imagen|moneda", None

    parts = [part.strip() for part in raw.split("|")]
    if parts[0].lower() in ALLOWED_CATEGORIES and len(parts) >= 2:
        category, url = parts[0].lower(), parts[1]
        extras = parts[2:]
    elif parts[0].lower().startswith(("https://", "http://")):
        category, url, extras = "accesorios", parts[0], parts[1:]
    else:
        return None, "Envía una URL válida de Amazon o AliExpress. Ejemplo: /agregar https://.../dp/ASIN", None

    if category not in ALLOWED_CATEGORIES:
        return None, "Categoría no válida. Usa ropa, calzado o accesorios.", None

    try:
        price = float(extras[0].replace(",", ".")) if len(extras) > 0 and extras[0] else None
        product = import_url(
            url, category,
            title=extras[1] if len(extras) > 1 and extras[1] else None,
            price=price,
            image_url=extras[2] if len(extras) > 2 and extras[2] else None,
            currency=extras[3] if len(extras) > 3 and extras[3] else "USD",
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
        print(f"[telegram-bot] url import error: {type(exc).__name__}: {exc}")
        return None, "No fue posible guardar el producto. Revisa los logs de Central.", None


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
        return None, str(exc)


def _model_active_products():
    """Puntúa en lote productos activos con precio y categoría."""
    conn = get_connection()
    try:
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
                  AND p.current_price IS NOT NULL
                  AND p.current_price > 0
                  AND p.category_id IS NOT NULL
                ORDER BY p.updated_at DESC
                LIMIT 500
                """
            )
            products = cur.fetchall()
            cur.execute("SELECT COUNT(*) AS total FROM products WHERE is_active = TRUE")
            active_total = int(cur.fetchone()["total"] or 0)
        scored = 0
        errors = 0
        for product in products:
            try:
                score_product(conn, product["id"], float(product["category_avg"] or product["current_price"]))
                scored += 1
            except Exception as exc:
                errors += 1
                print(f"[telegram-bot] modelado fallido para producto {product['id']}: {type(exc).__name__}")
        return scored, max(active_total - len(products), 0), errors
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
                + html.escape(payload["title"]) + "\n"
                + "Plataforma: " + html.escape(payload["platform"]) + "\n"
                + score_text + "\n"
                + '<a href="' + html.escape(payload.get("affiliate_url") or payload["product_url"], quote=True) + '">Abrir producto original</a>'
            )
            return _send(token, chat_id, message)
        return _send(token, chat_id, "Envía una URL válida de Amazon o AliExpress, o usa /help.")

    if command in ("/start", "/help"):
        return _send(token, chat_id, (
            "<b>Radar de Producto</b>\n\n"
            "/top — oportunidades con mayor score\n"
            "/buscar producto — buscar en todo el catálogo activo\n"
            "/modelar — puntuar en lote productos activos con precio; admite datos manuales\n"
            "/agregar URL — importar Amazon o AliExpress; también categoría|url|precio|título|imagen|moneda\n"
            "/estado — estado del catálogo\n"
            "/help — ayuda"
        ))

    if command == "/top":
        limit = 25
        if argument.strip().isdigit():
            limit = min(max(int(argument.strip()), 1), 100)
        products = _top_products(limit=limit)
        if not products:
            return _send(token, chat_id, "No hay productos con score de oportunidad ≥60/100. Importa productos con precio y ejecuta /modelar.")
        _send(token, chat_id, f"🔥 <b>Top oportunidades (score ≥60/100; {len(products)} resultados)</b>")
        for i, product in enumerate(products, 1):
            product["title"] = f"{i}. {product['title']}"
            _send_product(token, chat_id, product)
        return True

    if command == "/buscar":
        term = argument.strip()
        if not term:
            return _send(token, chat_id, "Uso: /buscar zapatillas")
        products = _search_products(term)
        if not products:
            return _send(token, chat_id, f"No encontré productos activos cuyo título coincida con: {html.escape(term)}. Prueba otra palabra o importa una URL con /agregar.")
        _send(token, chat_id, f"<b>Resultados: {html.escape(term)}</b>")
        for product in products:
            _send_product(token, chat_id, product)
        return True

    if command == "/agregar":
        result, payload, score = _add_url(argument.strip())
        if result is None:
            return _send(token, chat_id, f"<b>Error de importación</b>\n{html.escape(str(payload))}")
        score_text = f"Score: {float(score):.1f}/100" if score is not None else "Score pendiente: proporciona precio con /agregar URL|precio|título|imagen|moneda"
        message = (
            f"<b>Producto agregado</b>\n{html.escape(payload['title'])}\n"
            f"Plataforma: {html.escape(payload['platform'])}\n{score_text}\n"
            f'<a href="{html.escape(payload.get("affiliate_url") or payload["product_url"], quote=True)}">Abrir producto original</a>'
        )
        if not payload.get("affiliate_url"):
            message += "\nAfiliación pendiente: revisa las credenciales de la tienda."
        if payload.get("image_url"):
            message += f'\nImagen: <a href="{html.escape(payload["image_url"], quote=True)}">ver imagen</a>'
        return _send(token, chat_id, message)

    if command == "/modelar":
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
            return _send(token, chat_id, f"<b>Estado del catálogo</b>\nProductos activos: {total}\nProductos modelados: {modeled}")
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
        if not response.ok or not payload.get("ok"):
            print(f"[telegram-bot] no se pudo limpiar webhook: {payload.get('description', 'respuesta inválida')}")
            return False
        return True
    except (requests.RequestException, ValueError) as exc:
        print(f"[telegram-bot] error limpiando webhook: {exc}")
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
                if not payload.get("ok"):
                    raise RuntimeError(payload.get("description", "respuesta inválida"))
                for update in payload.get("result", []):
                    offset = update["update_id"] + 1
                    message = update.get("message") or {}
                    chat = message.get("chat") or {}
                    text = message.get("text") or ""
                    chat_id = chat.get("id")
                    if chat_id is None or not _allowed_chat(chat_id) or not text:
                        continue
                    _handle(token, chat_id, text)
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
