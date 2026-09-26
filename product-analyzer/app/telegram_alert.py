import html
import os
import time
from urllib.parse import urlparse

import requests

from analysis import score_product
from db import get_connection, upsert_product
from url_import import import_url

POLL_INTERVAL = int(os.getenv("TELEGRAM_POLL_INTERVAL", "3"))
API_BASE = "https://api.telegram.org"
ALLOWED_PLATFORMS = {"mercadolibre", "amazon"}
ALLOWED_CATEGORIES = {"ropa", "calzado", "accesorios"}


def _token():
    return os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")


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
    url = str(p.get("product_url") or "").strip()

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


def _top_products(limit=5):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.title, p.current_price, p.currency, p.product_url,
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
                ORDER BY COALESCE(s.opportunity_score, 0) DESC, p.updated_at DESC
                LIMIT %s
                """,
                (limit,),
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
                SELECT p.title, p.current_price, p.currency, p.product_url,
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
            return cur.fetchall()
    finally:
        conn.close()


def _add_url(argument):
    parts = [part.strip() for part in argument.split("|")]
    if len(parts) < 2:
        return None, "Uso: /agregar categoría|url|precio|título|imagen", None

    category, url = parts[0].lower(), parts[1]
    if category not in ALLOWED_CATEGORIES:
        return None, "Categoría no válida.", None

    try:
        price = float(parts[2]) if len(parts) > 2 and parts[2] else None
        product = import_url(
            url,
            category,
            title=parts[3] if len(parts) > 3 and parts[3] else None,
            price=price,
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
        print(f"[telegram-bot] url import error: {exc}")
        return None, "No fue posible agregar el producto desde la URL.", None


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
            "currency": "COP" if platform == "mercadolibre" else "USD",
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
    command, _, argument = text.partition(" ")
    command = command.lower().strip()

    if command in ("/start", "/help"):
        return _send(token, chat_id, (
            "<b>Radar de Producto</b>\n\n"
            "/top — oportunidades con mayor score\n"
            "/buscar producto — buscar en los datos modelados\n"
            "/modelar — registrar y puntuar un producto\n"
            "/agregar — importar un producto desde Amazon o Mercado Libre por URL\n"
            "/estado — estado del catálogo\n"
            "/help — ayuda"
        ))

    if command == "/top":
        products = _top_products()
        if not products:
            return _send(token, chat_id, "No hay oportunidades disponibles en la base de datos.")
        return _send(token, chat_id, "<b>Top oportunidades</b>\n\n" + "\n\n".join(
            f"{i}. {_format_product(p)}" for i, p in enumerate(products, 1)
        ))

    if command == "/buscar":
        term = argument.strip()
        if not term:
            return _send(token, chat_id, "Uso: /buscar zapatillas")
        products = _search_products(term)
        if not products:
            return _send(token, chat_id, f"No encontré productos modelados para: {html.escape(term)}")
        return _send(token, chat_id, f"<b>Resultados: {html.escape(term)}</b>\n\n" + "\n\n".join(
            _format_product(p) for p in products
        ))

    if command == "/agregar":
        result, payload, score = _add_url(argument.strip())
        if result is None:
            return _send(token, chat_id, f"<b>Error de importación</b>\n{html.escape(str(payload))}")
        score_text = f"Score: {float(score):.1f}/100" if score is not None else "Score: pendiente (sin precio)"
        message = (
            f"<b>Producto agregado</b>\n{html.escape(payload['title'])}\n"
            f"Plataforma: {html.escape(payload['platform'])}\n{score_text}\n"
            f'<a href="{html.escape(payload["product_url"], quote=True)}">Abrir producto original</a>'
        )
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


def run_bot():
    token = _token()
    if not token:
        print("[telegram-bot] Bot deshabilitado: falta TELEGRAM_BOT_TOKEN.")
        return
    if not (os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")):
        print("[telegram-bot] Bot deshabilitado: falta TELEGRAM_CHAT_ID.")
        return

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
                print("[telegram-bot] polling bloqueado: ya existe otro consumidor getUpdates.")
                time.sleep(max(POLL_INTERVAL, 20))
            else:
                print(f"[telegram-bot] polling HTTP error: {status}")
                time.sleep(max(POLL_INTERVAL, 5))
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            print(f"[telegram-bot] polling error: {type(exc).__name__}")
            time.sleep(max(POLL_INTERVAL, 5))
