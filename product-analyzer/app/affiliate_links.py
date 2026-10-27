"""Generación de enlaces afiliados usando las APIs oficiales de las plataformas."""
import hashlib
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests

def generate_aliexpress_affiliate_url(source_url: str) -> str | None:
    """Devuelve el enlace promocional oficial; None si no hay credenciales."""
    parsed = urlparse(source_url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host in {"s.click.aliexpress.com", "a.aliexpress.com"}:
        return source_url
    app_key = os.getenv("ALIEXPRESS_APP_KEY", "").strip()
    app_secret = os.getenv("ALIEXPRESS_APP_SECRET", "").strip()
    tracking_id = (os.getenv("ALIEXPRESS_TRACKING_ID") or os.getenv("ALIEXPRESS_AFFILIATE_TRACKING_ID") or "").strip()
    if not (app_key and app_secret and tracking_id):
        return None

    params = {
        "app_key": app_key,
        "method": "aliexpress.affiliate.link.generate",
        "sign_method": "md5",
        "timestamp": datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S"),
        "format": "json",
        "v": "2.0",
        "promotion_link_type": "0",
        "source_values": source_url,
        "tracking_id": tracking_id,
    }
    app_signature = os.getenv("ALIEXPRESS_APP_SIGNATURE", "").strip()
    if app_signature:
        params["app_signature"] = app_signature
    canonical = app_secret + "".join(key + str(params[key]) for key in sorted(params)) + app_secret
    params["sign"] = hashlib.md5(canonical.encode("utf-8")).hexdigest().upper()
    response = requests.post(
        "https://eco.taobao.com/router/rest",
        data=params,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()

    def find_link(value):
        if isinstance(value, str) and value.startswith(("https://", "http://")):
            return value
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"promotion_link", "promotionLink"} and isinstance(child, str) and child.startswith(("https://", "http://")):
                    return child
                found = find_link(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = find_link(child)
                if found:
                    return found
        return None

    link = find_link(payload)
    if not link:
        raise RuntimeError("AliExpress no devolvió enlace promocional; revisa credenciales, tracking ID y elegibilidad.")
    return link
