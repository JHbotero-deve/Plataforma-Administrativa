import html
import os
from urllib.parse import urlsplit

import requests


def _env_value(*names):
    for name in names:
        normalized_name = name.replace("\u00a0", "").strip().casefold()
        for key, value in os.environ.items():
            normalized_key = key.replace("\u00a0", "").strip().casefold()
            if normalized_key == normalized_name and value and value.strip():
                return value.strip()
    return ""


def _config():
    return (
        _env_value("TELEGRAM_BOT_TOKEN", "BOT_TOKEN", "TELEGRAM_TOKEN"),
        _env_value("TELEGRAM_CHAT_ID", "CHAT_ID"),
    )


def _http_url(value):
    url = str(value or "").strip()
    try:
        parsed = urlsplit(url)
        if parsed.scheme in ("http", "https") and parsed.hostname:
            return url
    except ValueError:
        pass
    return None


def _post_telegram(token, method, payload, timeout):
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/{method}",
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        print(f"[telegram] connection error: {type(exc).__name__}")
        return False

    try:
        result = response.json()
    except ValueError:
        result = {}

    if not response.ok or not isinstance(result, dict) or result.get("ok") is not True:
        print(f"[telegram] {method} rejected: HTTP {response.status_code}")
        return False
    return True


def send_telegram_alert(product_info):
    token, chat_id = _config()
    if not token or not chat_id:
        print("[telegram] Variables TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID no configuradas.")
        return False

    title = html.escape(str(product_info.get("title") or "Producto")[:120])
    price = html.escape(str(product_info.get("price", product_info.get("current_price", "N/D")))[:80])
    score = html.escape(str(product_info.get("score", product_info.get("opportunity_score", "N/D")))[:40])
    category = html.escape(str(product_info.get("category") or "N/D")[:100])
    platform = html.escape(str(product_info.get("platform") or "Central")[:80])
    url = _http_url(product_info.get("url") or product_info.get("product_url"))

    lines = [
        "<b>Oportunidad detectada</b>",
        f"<b>Producto:</b> {title}",
        f"<b>Precio:</b> {price}",
        f"<b>Score:</b> {score}/100",
        f"<b>Plataforma:</b> {platform}",
        f"<b>Categoría:</b> {category}",
    ]
    if product_info.get("affiliate_link_generated") is False:
        lines.append("<b>Afiliación:</b> pendiente de configurar o generar.")
    if url:
        lines.append(f'<a href="{html.escape(url, quote=True)}">Ver producto</a>')

    return _post_telegram(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": "\n".join(lines),
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=10,
    )


def send_telegram_publication(publication):
    token, chat_id = _config()
    if not token or not chat_id:
        raise RuntimeError("Configura TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID")

    title = html.escape(str(publication.get("title") or "Producto")[:180])
    subtitle = html.escape(str(publication.get("subtitle") or "")[:300])
    price = html.escape(str(publication.get("price_display") or "Consultar")[:100])
    url = _http_url(publication.get("store_url") or publication.get("product_url"))
    lines = [f"<b>{title}</b>"]
    if subtitle:
        lines.append(subtitle)
    lines.append(f"<b>Precio:</b> {price}")
    if url:
        lines.append(f'<a href="{html.escape(url, quote=True)}">Ver producto</a>')
    caption = "\n".join(lines)

    image_url = _http_url(publication.get("image_url"))
    method = "sendPhoto" if image_url else "sendMessage"
    payload = {
        "chat_id": chat_id,
        "parse_mode": "HTML",
    }
    if image_url:
        payload.update({"photo": image_url, "caption": caption})
    else:
        payload.update({"text": caption, "disable_web_page_preview": False})

    if not _post_telegram(token, method, payload, timeout=15):
        raise RuntimeError("Telegram no pudo enviar la publicación.")


def send_store_sale_notification(order, invoice, items):
    token, chat_id = _config()
    if not token or not chat_id:
        return False

    currency = html.escape(str(order.get("currency") or "COP"))
    total = html.escape(str(order.get("total_amount") or "0"))
    profit = html.escape(str(order.get("estimated_profit") or "0"))
    lines = [
        "<b>Venta aprobada en Central</b>",
        f"<b>Pedido:</b> {html.escape(str(order.get('reference') or '—'))}",
        f"<b>Total:</b> {currency} {total}",
        f"<b>Utilidad bruta estimada:</b> {currency} {profit}",
        "<b>Productos:</b>",
    ]
    for item in items[:10]:
        lines.append(
            f"• {html.escape(str(item.get('product_title') or 'Producto'))} "
            f"× {int(item.get('quantity') or 0)}"
        )
    if len(items) > 10:
        lines.append(f"… y {len(items) - 10} productos más")
    lines.append(
        "Utilidad estimada antes de comisiones, impuestos, envíos y devoluciones."
    )
    return _post_telegram(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": "\n".join(lines),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=15,
    )
