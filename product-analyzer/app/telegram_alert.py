import html
import os
import time
from urllib.parse import urlparse

import requests

from analysis import score_product
from db import get_connection, upsert_product


POLL_INTERVAL = int(os.getenv("TELEGRAM_POLL_INTERVAL", "3"))
API_BASE = "https://api.telegram.org"
ALLOWED_PLATFORMS = {"mercadolibre", "amazon", "tiktok"}
ALLOWED_CATEGORIES = {"ropa", "calzado", "accesorios"}


def _token():
    return os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")


def _allowed_chat(chat_id):
    configured = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")
    if not configured:
        return True
    return str(chat_id) == str(configured)


def _send(token, chat_id, text):
    try:
        r = requests.post(
            f"{API_BASE}/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False},
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
        safe_url = html.escape(url, quote=True)
        lines.append(f'<a href="{safe_url}">Ver producto</a>')
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
    except Exception as exc:
        print(f"[telegram-bot] top query error: {exc}")
        return []
    finally:
        conn.close()


def _search_products(term, limit=5):
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
                ORDER BY COALESCE(s.opportunity_score, 0) DESC, p.updated_at DESC
                LIMIT %s
                """,
                (f"%{term}%", limit),
            )
            return cur.fetchall()
    except Exception as exc:
        print(f"[telegram-bot] search query error: {exc}")
        return []
    finally:
        conn.close()


def _model_product(argument):
    parts = [part.strip() for part in argument.split("|")]
    if len(parts) < 6:
        return None, "Uso: /modelar plataforma|categoría|id|título|precio|url|rating|reseñas|ventas|imagen"

    platform, category, external_id, title, price_raw, product_url = parts[:6]
    rating_raw = parts[6] if len(parts) > 6 else ""
    reviews_raw = parts[7] if len(parts) > 7 else ""
    sales_raw = parts[8] if len(parts) > 8 else ""
    image_url = parts[9] if len(parts) > 9 else ""

    platform = platform.lower()
    category = category.lower()

    if platform not in ALLOWED_PLATFORMS:
        return None, "Plataforma inválida. Usa: mercadolibre, amazon o tiktok."
    if category not in ALLOWED_CATEGORIES:
        return None, "Categoría inválida. Usa: ropa, calzado o accesorios."
    if not external_id or not title:
        return None, "El id externo y el título son obligatorios."

    try:
        price = float(price_raw.replace(",", "."))
        if price <= 0:
            raise ValueError
    except ValueError:
        return None, "El precio debe ser un número mayor que cero."

    parsed_url = urlparse(product_url)
    if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
        return None, "La URL del producto debe comenzar con http:// o https://."

    def _optional_float(value, name, minimum=0):
        if not value:
            return None
        try:
            number = float(value.replace(",", "."))
        except ValueError:
            raise ValueError(f"{name} debe ser numérico.")
        if number < minimum:
            raise ValueError(f"{name} no puede ser negativo.")
        return number

    try:
        rating = _optional_float(rating_raw, "rating")
        if rating is not None and rating > 5:
            return None, "El rating debe estar entre 0 y 5."
        reviews = int(_optional_float(reviews_raw, "reseñas") or 0)
        sales = int(_optional_float(sales_raw, "ventas") or 0)
    except ValueError as exc:
        return None, str(exc)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT AVG(p.current_price) AS category_avg
                FROM products p
                JOIN categories c ON c.id = p.category_id
                WHERE c.name = %s
                  AND p.currency = 'COP'
                  AND p.current_price IS NOT NULL
                  AND p.is_active = TRUE
                """,
                (category,),
            )
            row = cur.fetchone()
            category_avg = float(row["category_avg"]) if row and row["category_avg"] else price

        product = {
            "external_id": external_id,
            "title": title,
            "image_url": image_url or None,
            "product_url": product_url,
            "price": price,
            "currency": "COP",
            "rating": rating,
            "reviews_count": reviews,
            "sales_estimate": sales,
        }
        product_id = upsert_product(conn, platform, category, product)
        opportunity = score_product(conn, product_id, category_avg)
        return product_id, opportunity
    except Exception as exc:
        conn.rollback()
        print(f"[telegram-bot] model product error: {exc}")
        return None, "No fue posible guardar/modelar el producto. Revisa plataforma, categoría y conexión de base de datos."
    finally:
        conn.close()


def _handle(token, chat_id, text):
    command, _, argument = text.partition(" ")
    command = command.lower().strip()

    if command in ("/start", "/help"):
        return _send(token, chat_id, (
            "<b>Radar de Producto</b>\n\n"
            "/top — oportunidades con mayor score\n"
            "/buscar producto — buscar en los datos modelados\n"
            "/modelar — registrar y puntuar un producto\n"
            "/estado — estado del catálogo\n"
            "/help — ayuda\n\n"
            "<b>Formato /modelar</b>\n"
            "plataforma|categoría|id|título|precio|url|rating|reseñas|ventas|imagen"
        ))

    if command == "/top":
        products = _top_products()
        if not products:
            return _send(token, chat_id, "No hay oportunidades disponibles en la base de datos.")
        body = "<b>Top oportunidades</b>\n\n" + "\n\n".join(
            f"{i}. {_format_product(p)}" for i, p in enumerate(products, 1)
        )
        return _send(token, chat_id, body)

    if command == "/buscar":
        term = argument.strip()
        if not term:
            return _send(token, chat_id, "Uso: /buscar zapatillas")
        products = _search_products(term)
        if not products:
            return _send(token, chat_id, f"No encontré productos modelados para: {html.escape(term)}")
        body = f"<b>Resultados: {html.escape(term)}</b>\n\n" + "\n\n".join(_format_product(p) for p in products)
        return _send(token, chat_id, body)

    if command == "/modelar":
        result, error = _model_product(argument.strip())
        if result is None:
            return _send(token, chat_id, f"<b>Error de modelado</b>\n{html.escape(str(error))}")
        return _send(
            token,
            chat_id,
            f"<b>Producto modelado</b>\nID: {result}\nOportunidad: {float(error):.1f}/100\n"
            "Ya está disponible para /top y /buscar.",
        )

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
            return _send(
                token,
                chat_id,
                f"<b>Estado del catálogo</b>\nProductos activos: {total}\nProductos modelados: {modeled}",
            )
        finally:
            conn.close()

    return _send(token, chat_id, "Comando no reconocido. Usa /help.")


def run_bot():
    token = _token()
    if not token:
        print("[telegram-bot] Bot deshabilitado: falta TELEGRAM_BOT_TOKEN.")
        return

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
            updates = response.json().get("result", [])
            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message") or {}
                chat = message.get("chat") or {}
                chat_id = chat.get("id")
                text = message.get("text")
                if chat_id is None or not text or not _allowed_chat(chat_id):
                    continue
                _handle(token, chat_id, text)
        except requests.RequestException as exc:
            print(f"[telegram-bot] polling error: {exc}")
            time.sleep(POLL_INTERVAL)
        except Exception as exc:
            print(f"[telegram-bot] unexpected error: {exc}")
            time.sleep(POLL_INTERVAL)
