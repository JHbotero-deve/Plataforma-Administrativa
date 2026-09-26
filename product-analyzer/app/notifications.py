import os
import html\n\nimport requests


def _config():
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")
    return token, chat_id


def send_telegram_alert(product_info):
    token, chat_id = _config()
    if not token or not chat_id:
        print("[telegram] Variables TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID no configuradas.")
        return False

    title = str(product_info.get("title", "Producto"))[:120]
    price = product_info.get("price", product_info.get("current_price", "N/D"))
    score = product_info.get("score", product_info.get("opportunity_score", "N/D"))
    platform = str(product_info.get("platform", "N/D"))
    category = str(product_info.get("category", "N/D"))
    url = product_info.get("url") or product_info.get("product_url")

    lines = [
        "<b>Oportunidad detectada</b>",
        f"<b>Producto:</b> {title}",
        f"<b>Precio:</b> {price}",
        f"<b>Score:</b> {score}/100",
        f"<b>Plataforma:</b> {platform}",
        f"<b>Categoría:</b> {category}",
    ]
    if url:
        lines.append(f'<a href="{url}">Ver producto</a>')

    return _send(token, chat_id, "\n".join(lines))


def _send(token, chat_id, message):
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML", "disable_web_page_preview": False},
            timeout=10,
        )
        if response.ok:
            return True
        print(f"[telegram] API error: {response.status_code}")
        return False
    except requests.RequestException as exc:
        print(f"[telegram] connection error: {exc}")
        return False
