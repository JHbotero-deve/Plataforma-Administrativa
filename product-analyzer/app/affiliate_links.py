"""Generación de enlaces afiliados usando las APIs oficiales de las plataformas."""
import hashlib
import hmac
import os
import time
from urllib.parse import urlparse

import requests


ALIEXPRESS_API_URL = "https://api-sg.aliexpress.com/sync"


def aliexpress_api_request(method: str, parameters: dict) -> dict:
    """Call an AliExpress affiliate API using its signed Open Platform protocol."""
    app_key = os.getenv("ALIEXPRESS_APP_KEY", "").strip()
    app_secret = os.getenv("ALIEXPRESS_APP_SECRET", "").strip()
    if not app_key or not app_secret:
        raise RuntimeError("AliExpress Affiliate API requiere App Key y App Secret.")

    params = {
        "app_key": app_key,
        "method": method,
        "sign_method": "sha256",
        "timestamp": str(int(time.time() * 1000)),
        "format": "json",
        "v": "2.0",
        **parameters,
    }
    app_signature = os.getenv("ALIEXPRESS_APP_SIGNATURE", "").strip()
    if app_signature:
        params["app_signature"] = app_signature
    canonical = "".join(
        key + str(params[key]) for key in sorted(params)
    )
    params["sign"] = hmac.new(
        app_secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest().upper()

    response = requests.post(
        ALIEXPRESS_API_URL,
        data=params,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("AliExpress devolvió una respuesta API no válida.")
    error = payload.get("error_response")
    if error:
        if isinstance(error, dict):
            code = error.get("code") or error.get("sub_code") or "unknown"
            raise RuntimeError(f"AliExpress rechazó la solicitud ({code}).")
        raise RuntimeError("AliExpress rechazó la solicitud.")
    return payload


def generate_aliexpress_affiliate_url(source_url: str) -> str | None:
    """Devuelve el enlace promocional oficial; None si no hay credenciales."""
    parsed = urlparse(source_url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme == "https"
        and not parsed.username
        and not parsed.password
        and host in {"s.click.aliexpress.com", "a.aliexpress.com"}
    ):
        return source_url
    app_key = os.getenv("ALIEXPRESS_APP_KEY", "").strip()
    app_secret = os.getenv("ALIEXPRESS_APP_SECRET", "").strip()
    tracking_id = (os.getenv("ALIEXPRESS_TRACKING_ID") or os.getenv("ALIEXPRESS_AFFILIATE_TRACKING_ID") or "").strip()
    if not (app_key and app_secret and tracking_id):
        return None

    payload = aliexpress_api_request(
        "aliexpress.affiliate.link.generate",
        {
            "promotion_link_type": "0",
            "source_values": source_url,
            "tracking_id": tracking_id,
        },
    )

    def find_link(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if (
                    key in {"promotion_link", "promotionLink"}
                    and isinstance(child, str)
                ):
                    try:
                        parsed_link = urlparse(child)
                        host = (parsed_link.hostname or "").lower().rstrip(".")
                        port = parsed_link.port
                    except ValueError:
                        continue
                    if (
                        parsed_link.scheme in {"http", "https"}
                        and not parsed_link.username
                        and not parsed_link.password
                        and port in {None, 443}
                        and (
                            host == "aliexpress.com"
                            or host.endswith(".aliexpress.com")
                        )
                    ):
                        return parsed_link._replace(scheme="https").geturl()
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
