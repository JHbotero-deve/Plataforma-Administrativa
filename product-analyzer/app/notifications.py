import html
import os
from urllib.parse import urlparse

import requests


def _config():
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")
    return token, chat_id


def send_telegram_alert(product_info):
    token, chat_id = _config()
    if not token or not chat_id:
        print("[telegram] Variables TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID no configuradas.")
        return False

    title = html.escape(str(product_info.get("title", "Producto"))[:120])
    price = html.escape(str(product_info.get("price", product_info.get("current_price", "N/D"))))
    score = html.escape(str(product_info.get("score", product_info.get("opportunity_score", "N/D"))))
    category = html.escape(str(product_info.get("category", "N/D")))
    url = product_info.get("url") or product_info.get("product_url")

    lines = [
        "<b>Oportunidad detectada</b>",
        f"<b>Producto:</b> {title}",
        f"<b>Precio:</b> {price}",
        f"<b>Score:</b> {score}/100",
        "<b>Plataforma:</b> Mercado Libre",
        f"<b>Categoría:</b> {category}",
    ]
    if url:
        safe_url = html.escape(str(url), quote=True)
        lines.append(f'<a href="{safe_url}">Ver producto</a>')

    return _send(token, chat_id, "\n".join(lines))


def _send(token, chat_id, message):
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=10,
        )
        if response.ok:
            return True
        print(f"[telegram] API error: {response.status_code}")
        return False
    except requests.RequestException as exc:
        # Do not log exception URLs because they can contain the bot token.
        print(f"[telegram] connection error: {type(exc).__name__}")
        return False


def _send_publication_text(token, chat_id, caption):
    """Deliver the product even when Telegram cannot render its image."""
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": caption,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=15,
    )
    response.raise_for_status()
    return True


def send_telegram_publication(publication):
    token, chat_id = _config()
    if not token or not chat_id:
        raise RuntimeError("Configura TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID")

    title = html.escape(str(publication.get("title") or "Producto")[:180])
    subtitle = html.escape(str(publication.get("subtitle") or "")[:300])
    price = html.escape(str(publication.get("price_display") or "Consultar"))
    url = publication.get("store_url") or publication.get("product_url")
    lines = [f"<b>{title}</b>"]
    if subtitle:
        lines.append(subtitle)
    lines.append(f"<b>Precio:</b> {price}")
    if url:
        lines.append(f'<a href="{html.escape(str(url), quote=True)}">Ver producto</a>')
    caption = "\n".join(lines)

    image_url = str(publication.get("image_url") or "").strip()
    parsed_image = urlparse(image_url)
    # Internal references (e.g. mongo://...) are not publicly fetchable by Telegram.
    usable_image = parsed_image.scheme in ("http", "https") and bool(parsed_image.netloc)

    if usable_image:
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendPhoto",
                json={
                    "chat_id": chat_id,
                    "photo": image_url,
                    "caption": caption[:1024],
                    "parse_mode": "HTML",
                },
                timeout=15,
            )
            response.raise_for_status()
            return True
        except requests.RequestException as exc:
            # Telegram rejects some valid-looking URLs/dimensions. Preserve the
            # product recommendation as a text message instead of dropping it.
            print(f"[telegram] sendPhoto falló ({type(exc).__name__}); se envía como texto.")
            try:
                return _send_publication_text(token, chat_id, caption)
            except requests.RequestException as fallback_exc:
                print(f"[telegram] sendMessage fallback falló ({type(fallback_exc).__name__}).")
                raise RuntimeError("Telegram no pudo enviar la publicación ni como foto ni como texto.") from None

    try:
        return _send_publication_text(token, chat_id, caption)
    except requests.RequestException as exc:
        print(f"[telegram] sendMessage falló ({type(exc).__name__}).")
        raise RuntimeError("Telegram no pudo enviar la publicación como texto.") from None
