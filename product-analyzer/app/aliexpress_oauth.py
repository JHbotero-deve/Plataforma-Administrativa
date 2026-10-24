"""Secure OAuth callback and token storage for AliExpress Open Platform."""
from __future__ import annotations

import hashlib
import hmac
import html
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode, urlparse

import requests
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from auth import require_admin
from db import get_connection

router = APIRouter(tags=["AliExpress OAuth"])

DEFAULT_CALLBACK_URL = (
    "https://central-7ykr.vercel.app/api/v1/aliexpress/oauth/callback"
)
AUTHORIZATION_URL = "https://api-sg.aliexpress.com/oauth/authorize"
TOKEN_API_URL = "https://api-sg.aliexpress.com/rest/auth/token/create"
TOKEN_API_PATH = "/auth/token/create"
STATE_TTL_MINUTES = 25


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, default) or "").strip()


def _encryption_key() -> str:
    # Reuse Central's existing Fernet key when a dedicated key has not been set.
    return _env("ALIEXPRESS_TOKEN_ENCRYPTION_KEY") or _env("MELI_TOKEN_ENCRYPTION_KEY")


def _cipher() -> Fernet:
    key = _encryption_key()
    if not key:
        raise RuntimeError(
            "Falta ALIEXPRESS_TOKEN_ENCRYPTION_KEY o MELI_TOKEN_ENCRYPTION_KEY "
            "para guardar los tokens de forma cifrada."
        )
    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise RuntimeError("La clave de cifrado de tokens no es válida.") from exc


def _settings(*, require_credentials: bool = True) -> dict[str, str]:
    callback = _env("ALIEXPRESS_CALLBACK_URL", DEFAULT_CALLBACK_URL)
    parsed = urlparse(callback)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise RuntimeError(
            "ALIEXPRESS_CALLBACK_URL debe ser una URL HTTPS completa sin query ni fragmento."
        )
    key = _env("ALIEXPRESS_APP_KEY") or _env("ALIEXPRESS_APPKEY")
    secret = _env("ALIEXPRESS_APP_SECRET") or _env("ALIEXPRESS_APPKEY_SECRET")
    if require_credentials and (not key or not secret):
        raise RuntimeError(
            "Faltan ALIEXPRESS_APP_KEY y ALIEXPRESS_APP_SECRET. "
            "Crea la aplicación en AliExpress Open Platform y configura ambas variables en Railway."
        )
    if require_credentials:
        _cipher()
    return {"app_key": key, "app_secret": secret, "callback_url": callback}


def _ensure_schema(cur) -> None:
    """Keep the callback deployable even before the standalone migration is run."""
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS aliexpress_oauth_state (
            state_hash CHAR(64) PRIMARY KEY,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS aliexpress_oauth_tokens (
            id SMALLINT PRIMARY KEY CHECK (id = 1),
            user_id TEXT,
            seller_id TEXT,
            account TEXT,
            account_platform TEXT,
            access_token_encrypted TEXT NOT NULL,
            refresh_token_encrypted TEXT,
            expires_at TIMESTAMPTZ,
            refresh_expires_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def _state_digest(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def _sign_api_request(params: dict[str, str], app_secret: str, api_path: str = TOKEN_API_PATH) -> str:
    """AliExpress Open Platform HMAC-SHA256 signature (uppercase hexadecimal)."""
    normalized = {str(k): str(v) for k, v in params.items() if v is not None}
    message = api_path + "".join(
        key + normalized[key] for key in sorted(normalized)
    )
    digest = hmac.new(
        app_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return digest.upper()


def _find_token_payload(value: Any) -> dict[str, Any] | None:
    """Find the token object across the response wrappers used by IOP gateways."""
    if isinstance(value, dict):
        if value.get("access_token") or value.get("accessToken"):
            return value
        for nested in value.values():
            found = _find_token_payload(nested)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_token_payload(nested)
            if found is not None:
                return found
    return None


def _find_provider_error(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("sub_msg", "subMsg", "error_description", "message", "msg", "error"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()[:500]
        for item in value.values():
            message = _find_provider_error(item)
            if message:
                return message
    elif isinstance(value, list):
        for item in value:
            message = _find_provider_error(item)
            if message:
                return message
    return "AliExpress no entregó un token válido."


def _html_page(title: str, message: str, *, status_code: int = 200) -> HTMLResponse:
    safe_title = html.escape(title)
    safe_message = html.escape(message)
    content = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{safe_title} · Central</title>
<style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:22px;box-sizing:border-box;
background:#f6f4ee;color:#30372f;font:16px/1.55 system-ui,sans-serif}}
main{{max-width:620px;padding:28px;border:1px solid #e4e1d7;border-radius:18px;background:#fffefa}}
h1{{margin:0 0 10px;font-size:24px}}p{{color:#62695e}}
a{{display:inline-block;margin-top:10px;padding:10px 14px;border-radius:999px;
background:#e8eee4;color:#3d5639;text-decoration:none;font-weight:700}}
</style></head><body><main><h1>{safe_title}</h1>
<p>{safe_message}</p><a href="/">Volver a Central</a></main></body></html>"""
    return HTMLResponse(content=content, status_code=status_code)


@router.get("/aliexpress/oauth/status")
def aliexpress_oauth_status(_: dict = Depends(require_admin)):
    """Admin-only configuration status; never returns tokens or App Secret."""
    settings_ready = False
    config_message = "Configura App Key y App Secret cuando AliExpress te los asigne."
    try:
        _settings(require_credentials=True)
        settings_ready = True
        config_message = "La aplicación está configurada para iniciar autorización."
    except RuntimeError as exc:
        config_message = str(exc)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            cur.execute(
                """
                SELECT account, account_platform, expires_at, refresh_expires_at, updated_at
                FROM aliexpress_oauth_tokens WHERE id=1
                """
            )
            token = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    return {
        "configured": settings_ready,
        "callback_url": _env("ALIEXPRESS_CALLBACK_URL", DEFAULT_CALLBACK_URL),
        "authorized": bool(token),
        "account": (token or {}).get("account"),
        "account_platform": (token or {}).get("account_platform"),
        "expires_at": (token or {}).get("expires_at"),
        "refresh_expires_at": (token or {}).get("refresh_expires_at"),
        "updated_at": (token or {}).get("updated_at"),
        "message": config_message,
    }


@router.get("/aliexpress/oauth/start")
def start_aliexpress_oauth(_: dict = Depends(require_admin)):
    """Create a single-use state and redirect the logged-in admin to AliExpress."""
    try:
        settings = _settings(require_credentials=True)
    except RuntimeError as exc:
        return _html_page("Falta configurar AliExpress", str(exc), status_code=503)

    state = secrets.token_urlsafe(32)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            cur.execute("DELETE FROM aliexpress_oauth_state WHERE expires_at <= NOW()")
            cur.execute(
                """
                INSERT INTO aliexpress_oauth_state(state_hash, expires_at)
                VALUES (%s, %s)
                """,
                (
                    _state_digest(state),
                    datetime.now(timezone.utc) + timedelta(minutes=STATE_TTL_MINUTES),
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    query = urlencode(
        {
            "response_type": "code",
            "force_auth": "true",
            "redirect_uri": settings["callback_url"],
            "client_id": settings["app_key"],
            "state": state,
        }
    )
    return RedirectResponse(f"{AUTHORIZATION_URL}?{query}", status_code=302)


@router.get("/aliexpress/oauth/callback", response_class=HTMLResponse)
def aliexpress_oauth_callback(
    request: Request,
    code: str | None = Query(default=None, max_length=4096),
    state: str | None = Query(default=None, max_length=256),
    error: str | None = Query(default=None, max_length=256),
):
    """Public AliExpress redirect URL; codes are exchanged server-side and never displayed."""
    # The portal may open/check this URL while registering the app.
    if not code and not state and not error:
        return _html_page(
            "Callback de AliExpress listo",
            "La ruta de retorno de Central está activa. Para autorizar la aplicación, "
            "inicia el flujo desde la sesión administrativa de Central."
        )

    if error:
        return _html_page(
            "Autorización no completada",
            "AliExpress no completó la autorización. Vuelve a Central e inténtalo de nuevo.",
            status_code=400,
        )
    if not code or not state:
        return _html_page(
            "Respuesta incompleta",
            "AliExpress no devolvió el código y el estado requeridos. Inicia una nueva autorización.",
            status_code=400,
        )

    try:
        settings = _settings(require_credentials=True)
        cipher = _cipher()
    except RuntimeError as exc:
        return _html_page("Falta configurar AliExpress", str(exc), status_code=503)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _ensure_schema(cur)
            cur.execute(
                """
                DELETE FROM aliexpress_oauth_state
                WHERE state_hash=%s AND expires_at > NOW()
                RETURNING state_hash
                """,
                (_state_digest(state),),
            )
            valid_state = cur.fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if not valid_state:
        return _html_page(
            "Autorización vencida o inválida",
            "El estado de autorización no existe, ya se usó o venció. Inicia de nuevo desde Central.",
            status_code=400,
        )

    params = {
        "app_key": settings["app_key"],
        "sign_method": "sha256",
        "timestamp": str(int(time.time() * 1000)),
        "code": code,
    }
    params["sign"] = _sign_api_request(
        {key: value for key, value in params.items() if key != "sign"},
        settings["app_secret"],
    )
    try:
        response = requests.post(TOKEN_API_URL, data=params, timeout=20)
        try:
            response_body = response.json()
        except ValueError:
            response_body = {}
        if not response.ok:
            message = _find_provider_error(response_body)
            return _html_page(
                "No se pudo canjear la autorización",
                "AliExpress rechazó el canje del código. " + message +
                " Vuelve a Central e inicia una nueva autorización.",
                status_code=502,
            )
        token_payload = _find_token_payload(response_body)
        if not token_payload:
            message = _find_provider_error(response_body)
            return _html_page(
                "AliExpress no entregó el token",
                message + " Revisa los permisos de la aplicación y vuelve a autorizar.",
                status_code=502,
            )

        access_token = str(token_payload.get("access_token") or token_payload.get("accessToken") or "")
        refresh_token = str(token_payload.get("refresh_token") or token_payload.get("refreshToken") or "")
        if not access_token:
            return _html_page(
                "AliExpress no entregó el token",
                "La respuesta no contiene un access token. Comprueba permisos y vuelve a autorizar.",
                status_code=502,
            )

        now = datetime.now(timezone.utc)
        expires_in = max(0, int(token_payload.get("expires_in") or 0))
        refresh_expires_in = max(0, int(token_payload.get("refresh_expires_in") or 0))
        expires_at = now + timedelta(seconds=expires_in) if expires_in else None
        refresh_expires_at = now + timedelta(seconds=refresh_expires_in) if refresh_expires_in else None

        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _ensure_schema(cur)
                cur.execute(
                    """
                    INSERT INTO aliexpress_oauth_tokens
                        (id, user_id, seller_id, account, account_platform,
                         access_token_encrypted, refresh_token_encrypted,
                         expires_at, refresh_expires_at, updated_at)
                    VALUES (1, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (id) DO UPDATE SET
                        user_id=EXCLUDED.user_id,
                        seller_id=EXCLUDED.seller_id,
                        account=EXCLUDED.account,
                        account_platform=EXCLUDED.account_platform,
                        access_token_encrypted=EXCLUDED.access_token_encrypted,
                        refresh_token_encrypted=EXCLUDED.refresh_token_encrypted,
                        expires_at=EXCLUDED.expires_at,
                        refresh_expires_at=EXCLUDED.refresh_expires_at,
                        updated_at=NOW()
                    """,
                    (
                        str(token_payload.get("user_id") or token_payload.get("user_Id") or "") or None,
                        str(token_payload.get("seller_id") or "") or None,
                        str(token_payload.get("account") or "") or None,
                        str(token_payload.get("account_platform") or "") or None,
                        cipher.encrypt(access_token.encode("utf-8")).decode("ascii"),
                        cipher.encrypt(refresh_token.encode("utf-8")).decode("ascii") if refresh_token else None,
                        expires_at,
                        refresh_expires_at,
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    except requests.RequestException:
        return _html_page(
            "No se pudo conectar con AliExpress",
            "Hubo un problema de red al canjear la autorización. Como el código es temporal, "
            "vuelve a Central e inicia una nueva autorización.",
            status_code=502,
        )
    except (TypeError, ValueError):
        return _html_page(
            "Respuesta inválida de AliExpress",
            "No se pudieron interpretar los datos de autorización. Inténtalo de nuevo.",
            status_code=502,
        )
    except InvalidToken:
        return _html_page(
            "Error al proteger las credenciales",
            "Central no pudo cifrar las credenciales. Revisa la clave de cifrado en Railway.",
            status_code=503,
        )

    return _html_page(
        "AliExpress conectado",
        "La autorización se completó y las credenciales se guardaron cifradas en Central. "
        "No se muestra ningún token en esta página."
    )
