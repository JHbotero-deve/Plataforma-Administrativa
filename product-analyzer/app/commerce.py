"""Ciclo comercial: seguimiento de pedidos, leads, entrega y devoluciones."""
import html
import hmac
import os
from typing import Literal

import requests
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from db import get_connection
from auth import require_admin

router = APIRouter(prefix="/store", tags=["commerce"])


class OrderLookup(BaseModel):
    reference: str = Field(min_length=8, max_length=255)
    email: str = Field(min_length=5, max_length=255)


class ReturnPayload(BaseModel):
    reference: str = Field(min_length=8, max_length=255)
    email: str = Field(min_length=5, max_length=255)
    reason: str = Field(min_length=5, max_length=1000)


class DeliveryUpdate(BaseModel):
    status: Literal["PROCESSING", "SHIPPED", "IN_TRANSIT", "DELIVERED", "CANCELLED"]
    carrier: str | None = Field(default=None, max_length=100)
    tracking_number: str | None = Field(default=None, max_length=150)
    note: str | None = Field(default=None, max_length=1000)


def _order_snapshot(reference: str, email: str):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT o.id,o.reference,o.customer_name,o.customer_email,o.customer_phone,
                          o.total_amount,o.currency,o.status,o.payment_status,o.created_at,o.paid_at,
                          o.delivery_status,o.address_line,o.city,o.department,o.carrier,o.tracking_number,
                          o.delivered_at,i.invoice_number,i.status AS invoice_status,i.issued_at
                   FROM store_orders o
                   LEFT JOIN store_invoices i ON i.order_id=o.id
                   WHERE o.reference=%s AND LOWER(o.customer_email)=LOWER(%s)""",
                (reference.strip(), email.strip()),
            )
            order = cur.fetchone()
            if not order:
                raise HTTPException(404, "Pedido no encontrado")
            cur.execute(
                """SELECT product_id,product_title,quantity,unit_price,line_total
                   FROM store_order_items WHERE order_id=%s ORDER BY id""",
                (order["id"],),
            )
            order["items"] = cur.fetchall()
            cur.execute(
                """SELECT id,reason,status,requested_amount_in_cents,refunded_amount_in_cents,
                          wompi_refund_id,requested_at,resolved_at,resolution_note
                   FROM store_returns WHERE order_id=%s""",
                (order["id"],),
            )
            order["returns"] = cur.fetchall()
            order.pop("customer_email", None)
            return order
    finally:
        conn.close()


@router.post("/orders/lookup")
def lookup_order(payload: OrderLookup):
    return _order_snapshot(payload.reference, payload.email)


@router.post("/orders/return")
def request_return(payload: ReturnPayload):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id,total_amount,status,payment_status,customer_email
                   FROM store_orders WHERE reference=%s AND LOWER(customer_email)=LOWER(%s)""",
                (payload.reference.strip(), payload.email.strip()),
            )
            order = cur.fetchone()
            if not order:
                raise HTTPException(404, "Pedido no encontrado")
            if order["payment_status"] != "PAID" or order["status"] not in {"PAID", "PROCESSING", "SHIPPED", "IN_TRANSIT", "DELIVERED"}:
                raise HTTPException(409, "El pedido aún no puede solicitar devolución")
            cur.execute(
                """INSERT INTO store_returns(order_id,reason,requested_amount_in_cents)
                   VALUES(%s,%s,%s)
                   ON CONFLICT(order_id) DO UPDATE SET reason=EXCLUDED.reason,status='REQUESTED',
                     requested_amount_in_cents=EXCLUDED.requested_amount_in_cents,updated_at=NOW()
                   RETURNING id,status,requested_amount_in_cents,requested_at""",
                (order["id"], payload.reason.strip(), int(round(float(order["total_amount"]) * 100))),
            )
            result = cur.fetchone()
        conn.commit()
        return {"return_id": result["id"], "status": result["status"], "amount_in_cents": result["requested_amount_in_cents"], "reference": payload.reference}
    finally:
        conn.close()


@router.get("/deliveries")
def list_deliveries(_: dict = Depends(require_admin)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id,reference,customer_name,customer_phone,city,department,
                          delivery_status,carrier,tracking_number,paid_at,delivered_at
                   FROM store_orders
                   WHERE payment_status='PAID'
                   ORDER BY created_at DESC LIMIT 200"""
            )
            return cur.fetchall()
    finally:
        conn.close()


@router.patch("/orders/{reference}/delivery")
def update_delivery(reference: str, payload: DeliveryUpdate, _: dict = Depends(require_admin)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE store_orders
                   SET delivery_status=%s,carrier=%s,tracking_number=%s,
                       delivered_at=CASE WHEN %s='DELIVERED' THEN COALESCE(delivered_at,NOW()) ELSE delivered_at END,
                       status=CASE WHEN %s='DELIVERED' THEN 'DELIVERED' ELSE status END,
                       updated_at=NOW()
                   WHERE reference=%s AND payment_status='PAID'
                   RETURNING id,reference,delivery_status,carrier,tracking_number,delivered_at""",
                (payload.status,payload.carrier,payload.tracking_number,payload.status,payload.status,reference),
            )
            result = cur.fetchone()
        conn.commit()
        if not result:
            raise HTTPException(404, "Pedido pagado no encontrado")
        return result
    finally:
        conn.close()


@router.get("/leads")
def list_leads(_: dict = Depends(require_admin)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id,order_id,name,email,phone,status,destination,delivered_at,last_error,created_at
                   FROM store_leads ORDER BY created_at DESC LIMIT 200"""
            )
            return cur.fetchall()
    finally:
        conn.close()


def _lead_message(lead: dict, order: dict, items: list[dict]) -> str:
    lines = [
        "<b>Lead comercial · Central</b>",
        f"<b>Pedido:</b> {html.escape(str(order['reference']))}",
        f"<b>Cliente:</b> {html.escape(str(lead['name']))}",
        f"<b>Correo:</b> {html.escape(str(lead['email']))}",
    ]
    if lead.get("phone"):
        lines.append(f"<b>Teléfono:</b> {html.escape(str(lead['phone']))}")
    lines.append(f"<b>Total:</b> {html.escape(str(order['total_amount']))} {html.escape(str(order['currency']))}")
    for item in items[:10]:
        lines.append(f"• {html.escape(str(item['product_title']))} × {item['quantity']}")
    return "\n".join(lines)


def dispatch_lead(order_id: int) -> bool:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT l.*,o.reference,o.total_amount,o.currency
                   FROM store_leads l JOIN store_orders o ON o.id=l.order_id
                   WHERE l.order_id=%s""",
                (order_id,),
            )
            lead = cur.fetchone()
            if not lead:
                return False
            cur.execute(
                "SELECT product_title,quantity FROM store_order_items WHERE order_id=%s ORDER BY id",
                (order_id,),
            )
            items = cur.fetchall()
    finally:
        conn.close()

    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")
    webhook = os.getenv("LEAD_WEBHOOK_URL", "").strip()
    secret = os.getenv("LEAD_WEBHOOK_SECRET", "").strip()
    delivered = False
    errors = []

    if token and chat_id:
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": _lead_message(lead, lead, items), "parse_mode": "HTML"},
                timeout=10,
            )
            delivered = response.ok
            if not delivered:
                errors.append(f"telegram:{response.status_code}")
        except requests.RequestException as exc:
            errors.append(f"telegram:{type(exc).__name__}")

    if webhook:
        try:
            payload = {"event":"lead.created","order":lead["reference"],"lead":{"name":lead["name"],"email":lead["email"],"phone":lead["phone"]},"total":float(lead["total_amount"]),"currency":lead["currency"],"items":[dict(x) for x in items]}
            headers={"Content-Type":"application/json"}
            if secret:
                headers["X-Lead-Signature"]=hmac.new(secret.encode(),str(payload).encode(), "sha256").hexdigest()
            response=requests.post(webhook,json=payload,headers=headers,timeout=10)
            delivered = delivered or response.ok
            if not response.ok:
                errors.append(f"webhook:{response.status_code}")
        except requests.RequestException as exc:
            errors.append(f"webhook:{type(exc).__name__}")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE store_leads SET status=%s,delivered_at=CASE WHEN %s THEN NOW() ELSE delivered_at END,
                   last_error=%s,updated_at=NOW() WHERE order_id=%s""",
                ("DELIVERED" if delivered else "QUEUED", delivered, "; ".join(errors)[:500] or None, order_id),
            )
        conn.commit()
    finally:
        conn.close()
    return delivered


@router.post("/leads/{lead_id}/deliver")
def deliver_lead(lead_id: int, _: dict = Depends(require_admin)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT order_id FROM store_leads WHERE id=%s", (lead_id,))
            row = cur.fetchone()
        if not row:
            raise HTTPException(404, "Lead no encontrado")
    finally:
        conn.close()
    if not dispatch_lead(row["order_id"]):
        raise HTTPException(503, "No fue posible entregar el lead")
    return {"status": "DELIVERED", "lead_id": lead_id}


@router.get("/returns")
def list_returns(_: dict = Depends(require_admin)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT r.id,r.order_id,o.reference,o.customer_name,o.customer_email,
                          r.reason,r.status,r.requested_amount_in_cents,r.refunded_amount_in_cents,
                          r.wompi_refund_id,r.requested_at,r.resolved_at,r.resolution_note
                   FROM store_returns r JOIN store_orders o ON o.id=r.order_id
                   ORDER BY r.requested_at DESC LIMIT 200"""
            )
            return cur.fetchall()
    finally:
        conn.close()
