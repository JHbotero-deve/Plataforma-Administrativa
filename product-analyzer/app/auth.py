import base64
import hashlib
import hmac
import os
import secrets
import smtplib
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from db import get_connection

router = APIRouter(prefix="/auth", tags=["authentication"])
COOKIE = "central_admin_session"
TTL = 604800
RESET_TTL = 600
RESET_MAX_ATTEMPTS = 5
RESET_MAX_REQUESTS = 3
ITERATIONS = 310000


class Credentials(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class ResetRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)


class ResetPassword(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    code: str = Field(pattern="^[0-9]{6}$")
    new_password: str = Field(min_length=12, max_length=256)


def _secret():
    secret = os.getenv("CENTRAL_AUTH_SECRET", "")
    if len(secret) < 32:
        raise HTTPException(503, "Autenticación no configurada")
    return secret.encode()


def _table(cur):
    cur.execute(
        "CREATE TABLE IF NOT EXISTS central_admin_auth "
        "(id SMALLINT PRIMARY KEY CHECK(id=1), email TEXT NOT NULL, "
        "password_hash TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
    )


def _hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    enc = lambda value: base64.urlsafe_b64encode(value).decode().rstrip("=")
    return "pbkdf2_sha256$" + str(ITERATIONS) + "$" + enc(salt) + "$" + enc(digest)


def _verify(password, encoded):
    try:
        algorithm, iterations, salt, digest = encoded.split("$", 3)
        dec = lambda value: base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), dec(salt), int(iterations))
        return algorithm == "pbkdf2_sha256" and hmac.compare_digest(candidate, dec(digest))
    except Exception:
        return False


def _record():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _table(cur)
            cur.execute("SELECT email,password_hash FROM central_admin_auth WHERE id=1")
            row = cur.fetchone()
            if not row:
                email = os.getenv("CENTRAL_ADMIN_EMAIL", "").strip().lower()
                password = os.getenv("CENTRAL_ADMIN_PASSWORD", "")
                if email and len(password) >= 12:
                    cur.execute("SELECT pg_advisory_xact_lock(92483922)")
                    cur.execute("SELECT email,password_hash FROM central_admin_auth WHERE id=1")
                    row = cur.fetchone()
                    if not row:
                        cur.execute(
                            "INSERT INTO central_admin_auth(id,email,password_hash) VALUES(1,%s,%s)",
                            (email, _hash(password)),
                        )
                        cur.execute("SELECT email,password_hash FROM central_admin_auth WHERE id=1")
                        row = cur.fetchone()
        conn.commit()
        return row
    finally:
        conn.close()



def _reset_table(cur):
    cur.execute(
        "CREATE TABLE IF NOT EXISTS central_admin_password_resets ("
        "id BIGSERIAL PRIMARY KEY, email TEXT NOT NULL, code_hash TEXT NOT NULL, "
        "expires_at TIMESTAMPTZ NOT NULL, attempts SMALLINT NOT NULL DEFAULT 0, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), consumed_at TIMESTAMPTZ)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_central_admin_password_resets_email_created "
        "ON central_admin_password_resets(email, created_at DESC)"
    )


def _reset_digest(email, code):
    message = (email.strip().lower() + "|" + code).encode()
    return hmac.new(_secret(), message, hashlib.sha256).hexdigest()


def _send_reset_email(email, code):
    subject = "Código para recuperar tu acceso a Central"
    body = (
        "Solicitaste recuperar la contraseña de Central.\n\n"
        f"Tu código de verificación es: {code}\n\n"
        "El código vence en 10 minutos y solo se puede utilizar una vez. "
        "Si no solicitaste este cambio, ignora este mensaje.\n"
    )

    # Preferir Resend por HTTPS para evitar conexiones SMTP salientes que expiran en Railway.
    resend_key = os.getenv("RESEND_API_KEY", "").strip()
    resend_from = os.getenv("RESEND_FROM_EMAIL", "").strip()
    if resend_key:
        if not resend_from:
            raise HTTPException(503, "Falta configurar RESEND_FROM_EMAIL con un remitente verificado en Resend")
        try:
            import requests
            response = requests.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
                json={"from": resend_from, "to": [email], "subject": subject, "text": body},
                timeout=12,
            )
            if not response.ok:
                raise HTTPException(503, "Resend rechazó el envío. Revisa el remitente verificado y la configuración de Resend")
            return
        except HTTPException:
            raise
        except requests.RequestException as exc:
            raise HTTPException(503, "No fue posible conectar con el servicio de correo. Inténtalo más tarde") from exc

    host = os.getenv("SMTP_HOST", "").strip()
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    sender = os.getenv("SMTP_FROM", "").strip() or user
    try:
        port = int(os.getenv("SMTP_PORT", "587"))
    except ValueError as exc:
        raise HTTPException(503, "El servicio de correo no está configurado correctamente") from exc
    if not host or not user or not password or not sender:
        raise HTTPException(503, "Configura RESEND_API_KEY y RESEND_FROM_EMAIL, o las variables SMTP")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = email
    message.set_content(body)
    try:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.ehlo()
            if os.getenv("SMTP_STARTTLS", "true").strip().lower() not in {"0", "false", "no"}:
                smtp.starttls()
                smtp.ehlo()
            smtp.login(user, password)
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise HTTPException(503, "No fue posible enviar el correo de recuperación. Inténtalo más tarde") from exc

def is_authenticated(request: Request):
    try:
        payload, signature = request.cookies[COOKIE].rsplit(".", 1)
        expected = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return False
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
        email, expires = raw.rsplit("|", 1)
        record = _record()
        return (
            int(expires) >= int(time.time())
            and bool(record)
            and hmac.compare_digest(email.lower(), record["email"].lower())
        )
    except Exception:
        return False


def require_admin(
    request: Request,
    x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key"),
):
    if is_authenticated(request):
        return {"admin": True}
    expected = os.getenv("ADMIN_API_KEY", "")
    if expected and x_admin_key and hmac.compare_digest(expected, x_admin_key):
        return {"admin": True}
    raise HTTPException(401, "Inicia sesión para administrar Central")


def _session(response, email):
    expiry = int(time.time()) + TTL
    payload = base64.urlsafe_b64encode((email.lower() + "|" + str(expiry)).encode()).decode().rstrip("=")
    signature = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    response.set_cookie(
        COOKIE,
        payload + "." + signature,
        max_age=TTL,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


@router.get("/status")
def status(request: Request):
    record = _record()
    authenticated = bool(record) and is_authenticated(request)
    return {
        "configured": bool(record),
        "setupRequired": False,
        "authenticated": authenticated,
        "email": record["email"] if authenticated else None,
    }


@router.post("/login")
def login(data: Credentials, response: Response):
    record = _record()
    if not record:
        raise HTTPException(
            503,
            "Acceso administrativo no configurado. Verifica CENTRAL_ADMIN_EMAIL y CENTRAL_ADMIN_PASSWORD en Railway.",
        )
    if (
        not hmac.compare_digest(data.email.strip().lower(), record["email"].lower())
        or not _verify(data.password, record["password_hash"])
    ):
        raise HTTPException(401, "Correo o contraseña incorrectos")
    _session(response, record["email"])
    return {"ok": True}



@router.post("/password/forgot")
def forgot_password(data: ResetRequest):
    email = data.email.strip().lower()
    # Permite Resend por HTTPS o SMTP; no depende exclusivamente de las variables SMTP.
    resend_ready = bool(os.getenv("RESEND_API_KEY", "").strip() and os.getenv("RESEND_FROM_EMAIL", "").strip())
    smtp_ready = all(os.getenv(k, "").strip() for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"))
    if not resend_ready and not smtp_ready:
        raise HTTPException(503, "Configura RESEND_API_KEY y RESEND_FROM_EMAIL, o las variables SMTP")
    record = _record()
    generic = {"ok": True, "message": "Si el correo corresponde a una cuenta, recibirás un código válido durante 10 minutos."}
    if not record or not hmac.compare_digest(email, record["email"].lower()):
        return generic

    code = f"{secrets.randbelow(1000000):06d}"
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _reset_table(cur)
            cur.execute(
                "SELECT COUNT(*) AS total FROM central_admin_password_resets "
                "WHERE email=%s AND created_at > NOW() - INTERVAL '15 minutes'",
                (email,),
            )
            if int(cur.fetchone()["total"]) >= RESET_MAX_REQUESTS:
                conn.commit()
                return generic
            cur.execute(
                "UPDATE central_admin_password_resets SET consumed_at=NOW() "
                "WHERE email=%s AND consumed_at IS NULL",
                (email,),
            )
            cur.execute(
                "INSERT INTO central_admin_password_resets(email,code_hash,expires_at) "
                "VALUES(%s,%s,NOW() + INTERVAL '10 minutes')",
                (email, _reset_digest(email, code)),
            )
        conn.commit()
    finally:
        conn.close()

    try:
        _send_reset_email(email, code)
    except HTTPException:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _reset_table(cur)
                cur.execute(
                    "UPDATE central_admin_password_resets SET consumed_at=NOW() "
                    "WHERE email=%s AND consumed_at IS NULL",
                    (email,),
                )
            conn.commit()
        finally:
            conn.close()
        raise
    return generic


@router.post("/password/reset")
def reset_password(data: ResetPassword, response: Response):
    email = data.email.strip().lower()
    code = data.code.strip()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _reset_table(cur)
            cur.execute(
                "SELECT id,code_hash,expires_at,attempts FROM central_admin_password_resets "
                "WHERE email=%s AND consumed_at IS NULL ORDER BY created_at DESC LIMIT 1 FOR UPDATE",
                (email,),
            )
            reset = cur.fetchone()
            if not reset:
                conn.commit()
                raise HTTPException(400, "Código inválido o vencido. Solicita uno nuevo.")
            if int(reset["attempts"]) >= RESET_MAX_ATTEMPTS:
                cur.execute("UPDATE central_admin_password_resets SET consumed_at=NOW() WHERE id=%s", (reset["id"],))
                conn.commit()
                raise HTTPException(400, "Se agotaron los intentos. Solicita un código nuevo.")
            if reset["expires_at"] <= datetime.now(timezone.utc):
                cur.execute("UPDATE central_admin_password_resets SET consumed_at=NOW() WHERE id=%s", (reset["id"],))
                conn.commit()
                raise HTTPException(400, "El código venció. Solicita uno nuevo.")
            if not hmac.compare_digest(_reset_digest(email, code), reset["code_hash"]):
                cur.execute("UPDATE central_admin_password_resets SET attempts=attempts+1 WHERE id=%s", (reset["id"],))
                conn.commit()
                raise HTTPException(400, "Código inválido o vencido.")
            cur.execute(
                "UPDATE central_admin_auth SET password_hash=%s WHERE id=1 AND email=%s",
                (_hash(data.new_password), email),
            )
            cur.execute(
                "UPDATE central_admin_password_resets SET consumed_at=NOW() "
                "WHERE email=%s AND consumed_at IS NULL",
                (email,),
            )
        conn.commit()
    finally:
        conn.close()
    _session(response, email)
    return {"ok": True, "message": "Contraseña actualizada. Ya tienes la sesión iniciada."}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    return {"ok": True}
