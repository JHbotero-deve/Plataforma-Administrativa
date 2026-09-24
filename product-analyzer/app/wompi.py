"""Integración Wompi Colombia para Web Checkout y webhooks."""

import hashlib
import hmac
import os
import secrets
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Header, HTTPException, Request
from psycopg2.extras import Json
from pydantic import BaseModel

from db import get_connection

router = APIRouter(prefix="/wompi", tags=["wompi"])


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise HTTPException(status_code=503, detail=f"Integración Wompi no configurada: {name}")
    return value


def _amount_to_cents(amount: Any) -> int:
    cents = int(round(float(amount) * 100))
    if cents <= 0:
        raise HTTPException(status_code=400, detail="El monto debe ser mayor que cero")
    return cents


def _integrity_signature(reference: str, amount_in_cents: int, currency: str) -> str:
    secret = _required("WOMPI_INTEGRITY_SECRET")
    payload = f"{reference}{amount_in_cents}{currency}{secret}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _get_path(data: dict[str, Any], path: str) -> Any:
    value: Any = data
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(path)
        value = value[part]
    return value


def _event_checksum(event: dict[str, Any]) -> str:
    properties = event.get("signature", {}).get("properties") or []
    timestamp = event.get("timestamp")
    secret = _required("WOMPI_EVENTS_SECRET")
    if timestamp is None or not properties:
        raise HTTPException(status_code=400, detail="Evento Wompi sin firma completa")
    try:
        values = "".join(str(_get_path(event.get("data", {}), prop)) for prop in properties)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Propiedad de firma ausente: {exc.args[0]}")
    return hashlib.sha256(f"{values}{timestamp}{secret}".encode("utf-8")).hexdigest()


def _checkout_base_url() -> str:
    return os.getenv("WOMPI_CHECKOUT_URL", "https://checkout.wompi.co/p/").rstrip("/") + "/"


class SubscriptionCheckout(BaseModel):
    plan_name: str
    customer_email: str
    redirect_url: str | None = None


@router.post("/checkout/subscription")
def create_subscription_checkout(req: SubscriptionCheckout):
    public_key = _required("WOMPI_PUBLIC_KEY")
    currency = os.getenv("WOMPI_CURRENCY", "COP").strip().upper()
    environment = os.getenv("WOMPI_ENVIRONMENT", "prod").strip().lower()
    if currency != "COP":
        raise HTTPException(status_code=500, detail="Wompi Colombia requiere moneda COP")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, price FROM subscription_plans WHERE name = %s", (req.plan_name,))
            plan = cur.fetchone()
            if not plan:
                raise HTTPException(status_code=404, detail="Plan no encontrado")
            if float(plan["price"]) <= 0:
                raise HTTPException(status_code=400, detail="El plan gratuito no requiere pago")

            amount_in_cents = _amount_to_cents(plan["price"])
            reference = f"SUB-{secrets.token_hex(12).upper()}"
            signature = _integrity_signature(reference, amount_in_cents, currency)
            redirect_url = req.redirect_url or os.getenv("WOMPI_REDIRECT_URL", "").strip() or None

            cur.execute(
                """INSERT INTO payment_transactions
                   (reference, provider, plan_id, customer_email, amount_in_cents, currency, status, environment)
                   VALUES (%s, 'wompi', %s, %s, %s, %s, 'PENDING', %s)
                   RETURNING id, reference""",
                (reference, plan["id"], req.customer_email.strip().lower(), amount_in_cents, currency, environment),
            )
            payment = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    params = [
        ("public-key", public_key),
        ("currency", currency),
        ("amount-in-cents", str(amount_in_cents)),
        ("reference", reference),
        ("signature:integrity", signature),
    ]
    if redirect_url:
        params.append(("redirect-url", redirect_url))

    return {
        "payment_id": payment["id"],
        "reference": reference,
        "amount_in_cents": amount_in_cents,
        "currency": currency,
        "checkout_url": f"{_checkout_base_url()}?{urlencode(params)}",
    }


@router.get("/payments/{reference}")
def get_payment(reference: str):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, reference, transaction_id, provider, customer_email,
                          amount_in_cents, currency, status, payment_method_type,
                          status_message, environment, created_at, updated_at, paid_at
                   FROM payment_transactions WHERE reference = %s""",
                (reference,),
            )
            payment = cur.fetchone()
    finally:
        conn.close()
    if not payment:
        raise HTTPException(status_code=404, detail="Pago no encontrado")
    return payment


@router.post("/events")
async def wompi_events(
    request: Request,
    x_event_checksum: str | None = Header(default=None, alias="X-Event-Checksum"),
):
    event = await request.json()
    if event.get("event") != "transaction.updated":
        return {"received": True, "processed": False}

    calculated = _event_checksum(event)
    supplied = (x_event_checksum or event.get("signature", {}).get("checksum") or "").strip()
    if not supplied or not hmac.compare_digest(calculated.lower(), supplied.lower()):
        raise HTTPException(status_code=401, detail="Firma de evento Wompi inválida")

    transaction = event.get("data", {}).get("transaction", {})
    reference = transaction.get("reference")
    transaction_id = transaction.get("id")
    status = transaction.get("status")
    if not reference or not transaction_id or not status:
        raise HTTPException(status_code=400, detail="Evento de transacción incompleto")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO payment_events
                   (provider, event_type, transaction_id, checksum, payload)
                   VALUES ('wompi', %s, %s, %s, %s)
                   ON CONFLICT (provider, event_type, transaction_id, checksum) DO NOTHING""",
                (event["event"], transaction_id, supplied, event),
            )
            cur.execute(
                """UPDATE payment_transactions
                   SET transaction_id=%s, status=%s, payment_method_type=%s,
                       status_message=%s, event_checksum=%s, raw_event=%s,
                       updated_at=NOW(),
                       paid_at=CASE WHEN %s='APPROVED' AND paid_at IS NULL THEN NOW() ELSE paid_at END
                   WHERE reference=%s
                   RETURNING id, plan_id, customer_email""",
                (
                    transaction_id, status, transaction.get("payment_method_type"),
                    transaction.get("status_message"), supplied, event, status, reference,
                ),
            )
            payment = cur.fetchone()

            if payment and status == "APPROVED" and payment["plan_id"]:
                cur.execute("SELECT id FROM users WHERE LOWER(email)=LOWER(%s)", (payment["customer_email"],))
                user = cur.fetchone()
                if user:
                    cur.execute(
                        """INSERT INTO subscriptions (user_id, plan_id, renews_at)
                           VALUES (%s, %s, NOW()+INTERVAL '30 days')
                           ON CONFLICT (user_id) DO UPDATE SET
                             plan_id=EXCLUDED.plan_id, status='active',
                             started_at=NOW(), renews_at=NOW()+INTERVAL '30 days'""",
                        (user["id"], payment["plan_id"]),
                    )
        conn.commit()
    finally:
        conn.close()

    return {"received": True, "processed": payment is not None, "status": status}
