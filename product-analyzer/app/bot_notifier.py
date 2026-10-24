import html
import os
from urllib.parse import urlparse

import requests


def _config():
    token = (
        os.getenv("TELEGRAM_BOT_TOKEN")
        or os.getenv("BOT_TOKEN")
        or os.getenv("TELEGRAM_TOKEN")
        or ""
    )
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID") or ""
    return token.strip(), str(chat_id).strip()


def send_telegram_deal(product):
    """Envía una alerta de oferta por Telegram usando HTML escapado.

    Evita inyección de Markdown/HTML: todo dato del producto se escapa.
    Compatible con las mismas variables de entorno que notifications.py
    y telegram_alert.py.
    """
    token, chat_id = _config()
    if not token or not chat_id:
        return False

    title = html.escape(str(product.get("title") or "Producto")[:180])
    price = html.escape(str(product.get("price") if product.get("price") is not None else "N/D"))
    original = product.get("original_price")
    original_text = html.escape(str(original)) if original is not None else None
    platform = html.escape(str(product.get("platform") or "").upper() or "N/D")
    try:
        discount = int(product.get("discount_percentage", 50))
    except (TypeError, ValueError):
        discount = 50
    discount = max(0, min(discount, 100))

    lines = [
        f"<b>Oferta detectada (-{discount}% OFF)</b>",
        f"<b>{title}</b>",
        f"Precio oferta: {price}",
    ]
    if original_text is not None:
        lines.append(f"Precio anterior: {original_text}")
    lines.append(f"Plataforma: {platform}")

    url = product.get("affiliate_url") or product.get("product_url") or ""
    parsed = urlparse(str(url).strip())
    if parsed.scheme in ("http", "https") and parsed.netloc:
        safe_url = html.escape(str(url).strip(), quote=True)
        lines.append(f'<a href="{safe_url}">Ver producto</a>')

    message = "\n".join(lines)

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message[:4096],
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=10,
        )
        if response.ok:
            return True
        print(f"[telegram] bot_notifier API error: {response.status_code}")
        return False
    except requests.RequestException as exc:
        print(f"[telegram] bot_notifier connection error: {type(exc).__name__}")
        return False
