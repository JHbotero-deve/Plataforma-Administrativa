import base64
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests
from cryptography.fernet import Fernet
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db import get_connection

MELI_OAUTH = "https://api.mercadolibre.com/oauth/token"
MELI_AUTH = "https://auth.mercadolibre.com.co/authorization"

router = APIRouter(prefix="/meli/oauth", tags=["mercadolibre-oauth"])
notification_router = APIRouter(prefix="/meli", tags=["mercadolibre-notifications"])


def _env(name: str) -> str:
    return os.getenv(name, "").strip()
def _redirect_uri() -> str:
    value = _env("MELI_REDIRECT_URI")
    if not value:
        raise HTTPException(503, "MELI_REDIRECT_URI no está configurado")
    return value


def _cipher() -> Fernet:
    key = _env("MELI_TOKEN_ENCRYPTION_KEY")
    if not key:
        raise HTTPException(503, "MELI_TOKEN_ENCRYPTION_KEY no está configurado")
    try:
        return Fernet(key.encode())
    except Exception as exc:
        raise HTTPException(503, "MELI_TOKEN_ENCRYPTION_KEY no es válida") from exc


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


@router.get("/start")
def start_oauth():
    client_id = _env("MELI_CLIENT_ID")
    if not client_id:
        raise HTTPException(503, "MELI_CLIENT_ID no está configurado")

    redirect_uri = _redirect_uri()
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(32)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM meli_oauth_state WHERE expires_at < NOW()")
            cur.execute(
                """
                INSERT INTO meli_oauth_state(state, code_verifier, expires_at)
                VALUES (%s, %s, %s)
                """,
                (state, verifier, datetime.now(timezone.utc) + timedelta(minutes=10)),
            )
        conn.commit()
    finally:
        conn.close()

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": "offline_access read write",
    }
    return RedirectResponse(f"{MELI_AUTH}?{urlencode(params)}", status_code=302)


@router.get("/callback")
def oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    if error:
        detail = error_description or error
        return HTMLResponse(f"<h2>Mercado Libre OAuth rechazado</h2><p>{detail}</p>", status_code=400)
    if not code or not state:
        raise HTTPException(400, "Faltan code o state")

    redirect_uri = _redirect_uri()
    client_id = _env("MELI_CLIENT_ID")
    client_secret = _env("MELI_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise HTTPException(503, "Faltan MELI_CLIENT_ID o MELI_CLIENT_SECRET")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT code_verifier
                FROM meli_oauth_state
                WHERE state=%s AND expires_at > NOW()
                """,
                (state,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(400, "state inválido o expirado")
            verifier = row["code_verifier"]
            cur.execute("DELETE FROM meli_oauth_state WHERE state=%s", (state,))
        conn.commit()
    finally:
        conn.close()

    response = requests.post(
        MELI_OAUTH,
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        },
        headers={"Accept": "application/json"},
        timeout=20,
    )
    if not response.ok:
        try:
            payload = response.json()
        except Exception:
            payload = {"error": response.text[:500]}
        raise HTTPException(response.status_code, detail=payload)

    payload = response.json()
    access_token = str(payload.get("access_token") or "").strip()
    refresh_token = str(payload.get("refresh_token") or "").strip()
    if not access_token or not refresh_token:
        raise HTTPException(502, "Mercado Libre no devolvió los tokens esperados")

    expires_in = int(payload.get("expires_in") or 0)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in) if expires_in else None
    cipher = _cipher()

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO meli_oauth_tokens
                    (id, user_id, access_token_encrypted, refresh_token_encrypted, expires_at, scope, updated_at)
                VALUES (1, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    user_id=EXCLUDED.user_id,
                    access_token_encrypted=EXCLUDED.access_token_encrypted,
                    refresh_token_encrypted=EXCLUDED.refresh_token_encrypted,
                    expires_at=EXCLUDED.expires_at,
                    scope=EXCLUDED.scope,
                    updated_at=NOW()
                """,
                (
                    payload.get("user_id"),
                    cipher.encrypt(access_token.encode()).decode(),
                    cipher.encrypt(refresh_token.encode()).decode(),
                    expires_at,
                    payload.get("scope"),
                ),
            )
        conn.commit()
    finally:
        conn.close()

    return HTMLResponse(
        "<h2>Mercado Libre conectado correctamente</h2>"
        "<p>Los tokens fueron almacenados de forma cifrada. Puedes cerrar esta ventana.</p>"
    )


@notification_router.post("/notifications")
async def meli_notifications(request: Request):
    payload = {}
    try:
        payload = await request.json()
    except Exception:
        pass

    print(f"[Mercado Libre] Notificación recibida: {payload}")
    return {"status": "ok"}


def get_meli_tokens() -> tuple[str | None, str | None]:
    # La conexión OAuth guardada en PostgreSQL prevalece sobre variables
    # de entorno que pueden conservar tokens antiguos tras una reconexión.
    key = _env("MELI_TOKEN_ENCRYPTION_KEY")
    if key:
        try:
            cipher = Fernet(key.encode())
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT access_token_encrypted, refresh_token_encrypted
                        FROM meli_oauth_tokens
                        WHERE id=1
                        """
                    )
                    row = cur.fetchone()
            finally:
                conn.close()
            if row:
                try:
                    access_token = cipher.decrypt(row["access_token_encrypted"].encode()).decode()
                    refresh_token = cipher.decrypt(row["refresh_token_encrypted"].encode()).decode()
                    if access_token and refresh_token:
                        return access_token, refresh_token
                except Exception:
                    pass
        except Exception as exc:
            print(f"[Mercado Libre] no se pudo leer la conexión OAuth guardada: {type(exc).__name__}")

    # Compatibilidad para instalaciones que aún usan tokens por entorno.
    env_access = _env("MELI_ACCESS_TOKEN")
    env_refresh = _env("MELI_REFRESH_TOKEN")
    env_expires = _env("MELI_ACCESS_TOKEN_EXPIRES_AT")
    if env_access and env_refresh and env_expires:
        try:
            env_expires_at = datetime.fromisoformat(env_expires.replace("Z", "+00:00"))
            if env_expires_at.tzinfo is None:
                env_expires_at = env_expires_at.replace(tzinfo=timezone.utc)
            if env_expires_at > datetime.now(timezone.utc):
                return env_access, env_refresh
        except ValueError:
            pass
    return None, None

def save_meli_tokens(access_token: str, refresh_token: str, expires_in: int = 0) -> None:
    cipher = _cipher()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in) if expires_in else None
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO meli_oauth_tokens
                    (id, access_token_encrypted, refresh_token_encrypted, expires_at, updated_at)
                VALUES (1, %s, %s, %s, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    access_token_encrypted=EXCLUDED.access_token_encrypted,
                    refresh_token_encrypted=EXCLUDED.refresh_token_encrypted,
                    expires_at=EXCLUDED.expires_at,
                    updated_at=NOW()
                """,
                (
                    cipher.encrypt(access_token.encode()).decode(),
                    cipher.encrypt(refresh_token.encode()).decode(),
                    expires_at,
                ),
            )
        conn.commit()
    finally:
        conn.close()
