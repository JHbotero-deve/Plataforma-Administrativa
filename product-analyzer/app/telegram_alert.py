import os
import time
import requests

from db import get_connection


POLL_INTERVAL = int(os.getenv("TELEGRAM_POLL_INTERVAL", "3"))
API_BASE = "https://api.telegram.org"


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
        return r.ok
    except requests.RequestException as exc:
        print(f"[telegram-bot] send error: {exc}")
        return False


def _format_product(p):
    title = str(p.get("title") or "Sin título")[:90]
    price = p.get("current_price")
    score = p.get("opportunity_score")
    platform = p.get("platform") or "N/D"
    category = p.get("category") or "N/D"
    url = p.get("product_url")
    lines = [
        f"<b>{title}</b>",
        f"Precio: {price if price is not None else 'N/D'} {p.get('currency') or ''}".strip(),
        f"Score: {round(float(score), 1) if score is not None else 'N/D'}/100",
        f"Plataforma: {platform}",
        f"Categoría: {category}",
    ]
    if url:
        lines.append(f'<a href="{url}">Ver producto</a>')
    return "\n".join(lines)


def _top_products(limit=5):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.title, p.current_price, p.currency, p.product_url,
                       pl.name AS platform, c.name AS category,
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
                       pl.name AS platform, c.name AS category,
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


def _handle(token, chat_id, text):
    command, _, argument = text.partition(" ")
    command = command.lower().strip()

    if command in ("/start", "/help"):
        return _send(token, chat_id, (
            "<b>Radar de Producto</b>\n\n"
            "/top — oportunidades con mayor score\n"
            "/buscar producto — buscar en los datos modelados\n"
            "/estado — estado básico del catálogo\n"
            "/help — ayuda"
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
            return _send(token, chat_id, f"No encontré productos modelados para: {term}")
        body = f"<b>Resultados: {term}</b>\n\n" + "\n\n".join(_format_product(p) for p in products)
        return _send(token, chat_id, body)

    if command == "/estado":
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS total FROM products WHERE is_active = TRUE")
                total = cur.fetchone()["total"]
            return _send(token, chat_id, f"<b>Estado del catálogo</b>\nProductos activos: {total}")
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
