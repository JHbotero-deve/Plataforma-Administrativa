"""Signed-cookie authentication for Central's administrative endpoints."""

import base64
import binascii
import hashlib
import hmac
import os
import secrets
import time
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response


ADMIN_COOKIE_NAME = "central_admin_session"
SESSION_MAX_AGE = 12 * 60 * 60


def _settings():
    email = os.getenv("CENTRAL_ADMIN_EMAIL", "").strip().casefold()
    password = os.getenv("CENTRAL_ADMIN_PASSWORD", "")
    secret = os.getenv("CENTRAL_AUTH_SECRET", "").strip()
    if not email or not password or len(secret) < 32:
        raise HTTPException(
            status_code=503,
            detail="La autenticación administrativa no está configurada.",
        )
    return email, password, secret


def authenticate_admin(email: str, password: str) -> str:
    configured_email, configured_password, _ = _settings()
    valid_email = hmac.compare_digest(
        str(email or "").strip().casefold(), configured_email
    )
    valid_password = hmac.compare_digest(str(password or ""), configured_password)
    if not (valid_email and valid_password):
        raise HTTPException(status_code=401, detail="Credenciales inválidas.")
    return configured_email


def create_session_token(email: str) -> str:
    configured_email, _, secret = _settings()
    subject = str(email or "").strip().casefold()
    if not hmac.compare_digest(subject, configured_email):
        raise HTTPException(status_code=401, detail="Credenciales inválidas.")
    expires_at = int(time.time()) + SESSION_MAX_AGE
    payload = f"{subject}|{expires_at}|{secrets.token_urlsafe(16)}".encode()
    signature = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    encoded_payload = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    return f"{encoded_payload}.{signature}"


def verify_session_token(token: str) -> bool:
    try:
        encoded_payload, signature = token.split(".", 1)
        padding = "=" * (-len(encoded_payload) % 4)
        payload = base64.urlsafe_b64decode(encoded_payload + padding)
        email, expires_at, nonce = payload.decode().split("|", 2)
        expiry = int(expires_at)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return False

    configured_email, _, secret = _settings()
    expected_signature = hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return (
        hmac.compare_digest(signature, expected_signature)
        and hmac.compare_digest(email, configured_email)
        and bool(nonce)
        and expiry > int(time.time())
    )


def _normalize_origin(value: str) -> str | None:
    try:
        parsed = urlsplit(str(value or "").strip())
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return None
        host = parsed.hostname.lower().rstrip(".")
        port = parsed.port
        if (parsed.scheme == "https" and port == 443) or (parsed.scheme == "http" and port == 80):
            port = None
        if ":" in host:
            host = f"[{host}]"
        return f"{parsed.scheme}://{host}" + (f":{port}" if port else "")
    except ValueError:
        return None


def _admin_origin_allowed(origin: str) -> bool:
    normalized = _normalize_origin(origin)
    if not normalized:
        return False
    configured_origins = os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:8000,"
        "https://central-five-pied.vercel.app,"
        "https://central-jorgedevop27-9650.vercel.app,"
        "https://central-git-main-jorgedevop27-9650.vercel.app",
    )
    allowed = {
        value
        for item in configured_origins.split(",")
        if (value := _normalize_origin(item))
    }
    store_origin = _normalize_origin(
        os.getenv("PUBLIC_STORE_URL", "https://central-7ykr.vercel.app")
    )
    if store_origin:
        allowed.add(store_origin)
    return normalized in allowed


def require_admin(request: Request) -> str:
    token = request.cookies.get(ADMIN_COOKIE_NAME, "")
    if not token or not verify_session_token(token):
        raise HTTPException(
            status_code=401,
            detail="Se requiere iniciar sesión como administrador.",
        )
    if request.method.upper() not in {"GET", "HEAD", "OPTIONS"} and not _admin_origin_allowed(
        request.headers.get("origin", "")
    ):
        raise HTTPException(status_code=403, detail="Origen de solicitud no permitido.")
    return _settings()[0]


def set_admin_cookie(response: Response, email: str) -> None:
    response.set_cookie(
        key=ADMIN_COOKIE_NAME,
        value=create_session_token(email),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=os.getenv("CENTRAL_ADMIN_COOKIE_SECURE", "true").strip().lower()
        not in {"0", "false", "no"},
        samesite="strict",
        path="/api/v1",
    )


def clear_admin_cookie(response: Response) -> None:
    response.delete_cookie(
        key=ADMIN_COOKIE_NAME,
        httponly=True,
        secure=os.getenv("CENTRAL_ADMIN_COOKIE_SECURE", "true").strip().lower()
        not in {"0", "false", "no"},
        samesite="strict",
        path="/api/v1",
    )
