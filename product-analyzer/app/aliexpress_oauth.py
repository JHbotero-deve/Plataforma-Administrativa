"""AliExpress Dropshipping OAuth callback and encrypted token storage."""
import hashlib
import hmac
import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

import requests
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from admin_auth import require_admin
from db import get_connection


router = APIRouter(prefix="/aliexpress/oauth", tags=["aliexpress-oauth"])
logger = logging.getLogger(__name__)
API_BASE_URL = "https://api-sg.aliexpress.com"
CALLBACK_STATE_TTL = timedelta(minutes=10)


def _environment(name):
    return os.getenv(name, "").strip()


def _configured_callback_url():
    callback_url = _environment("ALIEXPRESS_CALLBACK_URL")
    try:
        parsed = urlsplit(callback_url)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and parsed.query == ""
            and parsed.fragment == ""
        )
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(
            status_code=503,
            detail="Configura una ALIEXPRESS_CALLBACK_URL HTTPS válida.",
        )
    return callback_url


def _fernet():
    key = _environment("ALIEXPRESS_TOKEN_ENCRYPTION_KEY")
    if not key:
        raise HTTPException(
            status_code=503,
            detail="El almacenamiento cifrado de tokens AliExpress no está configurado.",
        )
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        raise HTTPException(
            status_code=503,
            detail="La clave de cifrado de tokens AliExpress no es válida.",
        ) from None


def _iop_signature(api_path, params, app_secret):
    canonical = api_path + "".join(
        key + str(params[key]) for key in sorted(params)
    )
    return hmac.new(
        app_secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest().upper()


def _unwrap_token_response(payload):
    if not isinstance(payload, dict):
        raise RuntimeError("AliExpress devolvió una respuesta OAuth inválida.")
    error = payload.get("error_response")
    if error:
        code = (
            error.get("code") or error.get("sub_code") or "unknown"
            if isinstance(error, dict)
            else "unknown"
        )
        raise RuntimeError(f"AliExpress rechazó el intercambio OAuth ({code}).")

    result = payload.get("aliexpress_system_oauth_token_create_response", payload)
    if isinstance(result, dict) and isinstance(result.get("resp_result"), dict):
        result = result["resp_result"]
    if isinstance(result, dict) and isinstance(result.get("result"), dict):
        result = result["result"]
    if not isinstance(result, dict):
        raise RuntimeError("AliExpress devolvió una respuesta OAuth inválida.")
    if result.get("code") not in (None, "", 0, "0"):
        raise RuntimeError("AliExpress rechazó el intercambio OAuth.")
    if not str(result.get("access_token") or "").strip():
        raise RuntimeError("AliExpress no devolvió un access token.")
    return result


def _token_expiration(data, seconds_key, absolute_key):
    seconds = data.get(seconds_key)
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        seconds = 0
    if seconds > 0:
        return datetime.now(timezone.utc) + timedelta(seconds=seconds)

    absolute = data.get(absolute_key)
    try:
        timestamp = float(absolute)
    except (TypeError, ValueError):
        return None
    if timestamp > 10_000_000_000:
        timestamp /= 1000
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _exchange_authorization_code(code):
    app_key = _environment("ALIEXPRESS_APP_KEY")
    app_secret = _environment("ALIEXPRESS_APP_SECRET")
    if not app_key or not app_secret:
        raise HTTPException(
            status_code=503,
            detail="Configura las credenciales de la aplicación AliExpress.",
        )

    params = {
        "app_key": app_key,
        "code": code,
        "sign_method": "sha256",
        "timestamp": str(int(time.time() * 1000)),
        "simplify": "true",
    }
    params["sign"] = _iop_signature("/auth/token/create", params, app_secret)
    try:
        response = requests.get(
            f"{API_BASE_URL}/rest/auth/token/create",
            params=params,
            timeout=20,
            allow_redirects=False,
        )
        if not 200 <= response.status_code < 300:
            raise RuntimeError(
                f"AliExpress token endpoint returned HTTP {response.status_code}."
            )
        payload = response.json()
    except requests.RequestException as exc:
        logger.warning("AliExpress OAuth exchange transport error: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail="No fue posible comunicarse con AliExpress para autorizar la cuenta.",
        ) from None
    except ValueError:
        raise HTTPException(
            status_code=502,
            detail="AliExpress devolvió una respuesta OAuth inválida.",
        ) from None

    try:
        return _unwrap_token_response(payload)
    except RuntimeError as exc:
        logger.warning("AliExpress OAuth exchange failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="AliExpress rechazó la autorización. Inicia el proceso de nuevo.",
        ) from None


def ensure_aliexpress_oauth_storage():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """CREATE TABLE IF NOT EXISTS aliexpress_oauth_tokens (
                       id SMALLINT PRIMARY KEY CHECK (id = 1),
                       access_token_ciphertext TEXT NOT NULL,
                       refresh_token_ciphertext TEXT,
                       access_expires_at TIMESTAMPTZ,
                       refresh_expires_at TIMESTAMPTZ,
                       updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                   )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS aliexpress_oauth_states (
                       state_hash CHAR(64) PRIMARY KEY,
                       expires_at TIMESTAMPTZ NOT NULL,
                       created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                   )"""
            )
            cur.execute(
                """CREATE INDEX IF NOT EXISTS idx_aliexpress_oauth_states_expiry
                   ON aliexpress_oauth_states(expires_at)"""
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _create_oauth_state():
    state = secrets.token_urlsafe(32)
    state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM aliexpress_oauth_states WHERE expires_at <= NOW()"
            )
            cur.execute(
                """INSERT INTO aliexpress_oauth_states(state_hash, expires_at)
                   VALUES(%s, NOW() + %s)""",
                (state_hash, CALLBACK_STATE_TTL),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return state


def _consume_oauth_state(state):
    state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """DELETE FROM aliexpress_oauth_states
                   WHERE state_hash=%s AND expires_at > NOW()
                   RETURNING state_hash""",
                (state_hash,),
            )
            consumed = cur.fetchone() is not None
        conn.commit()
        return consumed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _save_tokens(data):
    fernet = _fernet()
    access_token = data["access_token"].strip()
    refresh_token = str(data.get("refresh_token") or "").strip()
    access_ciphertext = fernet.encrypt(access_token.encode("utf-8")).decode("ascii")
    refresh_ciphertext = (
        fernet.encrypt(refresh_token.encode("utf-8")).decode("ascii")
        if refresh_token
        else None
    )
    access_expiration = _token_expiration(
        data, "expires_in", "expire_time"
    )
    refresh_expiration = _token_expiration(
        data, "refresh_expires_in", "refresh_token_valid_time"
    )

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO aliexpress_oauth_tokens(
                       id, access_token_ciphertext, refresh_token_ciphertext,
                       access_expires_at, refresh_expires_at, updated_at
                   ) VALUES (1, %s, %s, %s, %s, NOW())
                   ON CONFLICT (id) DO UPDATE SET
                       access_token_ciphertext=EXCLUDED.access_token_ciphertext,
                       refresh_token_ciphertext=EXCLUDED.refresh_token_ciphertext,
                       access_expires_at=EXCLUDED.access_expires_at,
                       refresh_expires_at=EXCLUDED.refresh_expires_at,
                       updated_at=NOW()""",
                (
                    access_ciphertext,
                    refresh_ciphertext,
                    access_expiration,
                    refresh_expiration,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@router.get("/start", include_in_schema=False)
def start_oauth(_admin=Depends(require_admin)):
    app_key = _environment("ALIEXPRESS_APP_KEY")
    if not app_key:
        raise HTTPException(status_code=503, detail="Configura ALIEXPRESS_APP_KEY.")
    callback_url = _configured_callback_url()
    state = _create_oauth_state()
    authorization_url = "https://oauth.aliexpress.com/authorize?" + urlencode(
        {
            "response_type": "code",
            "force_auth": "true",
            "client_id": app_key,
            "redirect_uri": callback_url,
            "state": state,
            "view": "web",
            "sp": "ae",
        }
    )
    return RedirectResponse(
        authorization_url,
        status_code=302,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get("/callback", include_in_schema=False)
def oauth_callback(
    request: Request,
    code: str | None = Query(default=None, max_length=512),
    state: str | None = Query(default=None, max_length=256),
    error: str | None = Query(default=None, max_length=100),
):
    request.scope["query_string"] = b""
    headers = {
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
        "X-Robots-Tag": "noindex, nofollow",
    }
    if not state or not _consume_oauth_state(state):
        return HTMLResponse(
            "<h1>Autorización no válida</h1><p>Inicia de nuevo la autorización desde Central.</p>",
            status_code=400,
            headers=headers,
        )
    if error:
        return HTMLResponse(
            "<h1>Autorización cancelada</h1><p>No se guardó ningún token. Puedes cerrar esta ventana.</p>",
            status_code=400,
            headers=headers,
        )
    if not code:
        return HTMLResponse(
            "<h1>Falta el código</h1><p>AliExpress no envió un código de autorización. Inicia de nuevo.</p>",
            status_code=400,
            headers=headers,
        )

    try:
        token_data = _exchange_authorization_code(code)
        _save_tokens(token_data)
    except HTTPException as exc:
        return HTMLResponse(
            "<h1>No se pudo completar la autorización</h1>"
            f"<p>{exc.detail}</p><p>Inicia el proceso de nuevo desde Central.</p>",
            status_code=exc.status_code,
            headers=headers,
        )
    except Exception as exc:
        logger.error("AliExpress OAuth token storage failed: %s", type(exc).__name__)
        return HTMLResponse(
            "<h1>No se pudo guardar la autorización</h1>"
            "<p>No se guardaron tokens. Revisa la configuración del servidor e inténtalo de nuevo.</p>",
            status_code=500,
            headers=headers,
        )

    return HTMLResponse(
        "<h1>AliExpress autorizado</h1>"
        "<p>La autorización se almacenó cifrada en Central. Puedes cerrar esta ventana.</p>",
        headers=headers,
    )
