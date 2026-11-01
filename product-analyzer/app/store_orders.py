"""Pedidos, métricas privadas y comprobantes públicos de la tienda."""
import datetime
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from admin_auth import require_admin
from db import get_connection
from store_notifications import notification_summary, process_pending_notifications

router = APIRouter(prefix="/store", tags=["store"])


def ensure_commerce_storage():
 conn = get_connection()
 try:
  with conn.cursor() as cur:
   cur.execute(
       "ALTER TABLE store_invoices ADD COLUMN IF NOT EXISTS public_token VARCHAR(64)"
   )
   cur.execute(
       """CREATE UNIQUE INDEX IF NOT EXISTS idx_store_invoices_public_token
          ON store_invoices(public_token)
          WHERE public_token IS NOT NULL AND public_token<>''"""
   )
   cur.execute(
       """CREATE TABLE IF NOT EXISTS store_notification_outbox (
            id BIGSERIAL PRIMARY KEY,
            order_id BIGINT NOT NULL REFERENCES store_orders(id) ON DELETE CASCADE,
            channel VARCHAR(20) NOT NULL,
            notification_type VARCHAR(40) NOT NULL,
            recipient VARCHAR(255) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
            attempts INTEGER NOT NULL DEFAULT 0,
            available_at TIMESTAMP NOT NULL DEFAULT NOW(),
            locked_at TIMESTAMP,
            last_error VARCHAR(120),
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
            sent_at TIMESTAMP,
            UNIQUE(order_id,channel,notification_type,recipient)
          )"""
   )
   cur.execute(
       """CREATE INDEX IF NOT EXISTS idx_store_notification_outbox_pending
          ON store_notification_outbox(status,available_at,id)"""
   )
   cur.execute(
       "SELECT id FROM store_invoices WHERE public_token IS NULL OR public_token=''"
   )
   for row in cur.fetchall():
    cur.execute(
        "UPDATE store_invoices SET public_token=%s WHERE id=%s",
        (secrets.token_urlsafe(32), row["id"]),
    )
  conn.commit()
 finally:
  conn.close()


def _ensure_invoice_tokens(cur, rows):
 for row in rows:
  if not row.get("invoice_number") or str(row.get("public_token") or "").strip():
   continue
  token = secrets.token_urlsafe(32)
  cur.execute(
      """UPDATE store_invoices SET public_token=%s
         WHERE invoice_number=%s
           AND (public_token IS NULL OR public_token='')
         RETURNING public_token""",
      (token, row["invoice_number"]),
  )
  saved = cur.fetchone()
  if saved:
   row["public_token"] = saved["public_token"]
  else:
   cur.execute(
       "SELECT public_token FROM store_invoices WHERE invoice_number=%s",
       (row["invoice_number"],),
   )
   current = cur.fetchone()
   row["public_token"] = current["public_token"] if current else None


class StoreOrderPayload(BaseModel):
 product_id: int = Field(gt=0)
 publication_id: Optional[int] = Field(default=None, gt=0)
 customer_name: str = Field(min_length=2, max_length=200)
 customer_email: str = Field(min_length=5, max_length=255)
 customer_phone: Optional[str] = Field(default=None, max_length=40)
 quantity: int = Field(default=1, ge=1, le=100)
 notes: Optional[str] = Field(default=None, max_length=2000)


@router.post("/orders")
def create_store_order(payload: StoreOrderPayload):
 conn = get_connection()
 try:
  with conn.cursor() as cur:
   cur.execute("""SELECT p.id,p.title,p.image_url,p.product_url,p.current_price,p.currency,p.stock,pl.name AS platform,
                        pc.id AS publication_id,pc.sale_price,pc.cost_price
                 FROM products p JOIN platforms pl ON pl.id=p.platform_id
                 LEFT JOIN published_cards pc ON pc.product_id=p.id AND pc.is_published=TRUE
                 WHERE p.id=%s AND p.is_active=TRUE""", (payload.product_id,))
   p = cur.fetchone()
   if not p:
    raise HTTPException(404, "Producto no encontrado")
   if payload.publication_id and p["publication_id"] != payload.publication_id:
    raise HTTPException(
        409, "La publicación no corresponde al producto solicitado")
   if p["stock"] is not None and payload.quantity > p["stock"]:
    raise HTTPException(409, "Stock insuficiente")
   sale = float(p["sale_price"] or p["current_price"] or 0)
   cost = float(p["cost_price"] if p["cost_price"]
                is not None else p["current_price"] or 0)
   if sale <= 0:
    raise HTTPException(400, "Producto sin precio de venta")
   total = sale*payload.quantity
   profit = (sale-cost)*payload.quantity
   ref = f"REQ-{datetime.datetime.utcnow():%Y%m%d%H%M%S}-{payload.product_id}-{secrets.token_hex(4).upper()}"
   cur.execute("""INSERT INTO store_orders(reference,product_id,publication_id,customer_name,customer_email,customer_phone,quantity,unit_price,cost_unit_price,total_amount,estimated_profit,currency,status,payment_status,source,product_title_snapshot,product_image_snapshot,product_url_snapshot,platform_snapshot,notes)
                 VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PENDING','PENDING','TIENDA',%s,%s,%s,%s,%s)
                 RETURNING id,reference,total_amount,estimated_profit,currency,status,payment_status""",
               (ref, payload.product_id, p["publication_id"], payload.customer_name.strip(), payload.customer_email.strip().lower(), payload.customer_phone, payload.quantity, sale, cost, total, profit, p["currency"], p["title"], p["image_url"], p["product_url"], p["platform"], payload.notes))
   out = cur.fetchone()
  conn.commit()
  return out
 finally:
  conn.close()


@router.get("/orders", dependencies=[Depends(require_admin)])
def list_store_orders(limit: int = Query(50, ge=1, le=200)):
 conn = get_connection()
 try:
  with conn.cursor() as cur:
   cur.execute(
       """SELECT o.id,o.reference,o.product_id,o.product_title_snapshot,
                 o.platform_snapshot,o.customer_name,o.customer_email,
                 o.customer_phone,o.quantity,o.total_amount,o.estimated_profit,
                 o.currency,o.status,o.payment_status,o.created_at,o.paid_at,
                 i.invoice_number,i.public_token,
                 (SELECT COUNT(*) FROM store_notification_outbox n
                   WHERE n.order_id=o.id AND n.status<>'SENT') AS pending_notifications
          FROM store_orders o
          LEFT JOIN store_invoices i ON i.order_id=o.id
          ORDER BY o.created_at DESC LIMIT %s""",
       (limit,),
   )
   rows = cur.fetchall()
   _ensure_invoice_tokens(cur, rows)
  conn.commit()
  return rows
 finally:
  conn.close()


@router.get("/metrics", dependencies=[Depends(require_admin)])
def store_metrics():
 conn = get_connection()
 try:
  with conn.cursor() as cur:
   cur.execute(
       """SELECT
            COUNT(*) FILTER(WHERE payment_status='PAID') AS paid_orders,
            COUNT(*) FILTER(WHERE payment_status='PENDING') AS pending_orders,
            COUNT(*) FILTER(WHERE payment_status IN ('DECLINED','VOIDED','ERROR')) AS failed_orders,
            COALESCE(SUM(total_amount) FILTER(WHERE payment_status='PAID'),0) AS gross_sales,
            COALESCE(SUM(total_amount-estimated_profit)
                     FILTER(WHERE payment_status='PAID'),0) AS estimated_cost,
            COALESCE(SUM(estimated_profit)
                     FILTER(WHERE payment_status='PAID'),0) AS estimated_profit,
            COALESCE(SUM(quantity) FILTER(WHERE payment_status='PAID'),0) AS units_sold,
            COALESCE(AVG(total_amount) FILTER(WHERE payment_status='PAID'),0) AS average_ticket,
            COALESCE(SUM(total_amount) FILTER(
              WHERE payment_status='PAID' AND paid_at>=date_trunc('day',NOW())),0) AS today_sales,
            COALESCE(SUM(total_amount) FILTER(
              WHERE payment_status='PAID' AND paid_at>=date_trunc('week',NOW())),0) AS week_sales,
            COALESCE(SUM(total_amount) FILTER(
              WHERE payment_status='PAID' AND paid_at>=date_trunc('month',NOW())),0) AS month_sales
          FROM store_orders"""
   )
   r = cur.fetchone()
  sales = float(r["gross_sales"] or 0)
  profit = float(r["estimated_profit"] or 0)
  return {
      "paid_orders": int(r["paid_orders"] or 0),
      "pending_orders": int(r["pending_orders"] or 0),
      "failed_orders": int(r["failed_orders"] or 0),
      "gross_sales": sales,
      "estimated_cost": float(r["estimated_cost"] or 0),
      "estimated_profit": profit,
      "units_sold": int(r["units_sold"] or 0),
      "average_ticket": float(r["average_ticket"] or 0),
      "today_sales": float(r["today_sales"] or 0),
      "week_sales": float(r["week_sales"] or 0),
      "month_sales": float(r["month_sales"] or 0),
      "margin_pct": round(profit / sales * 100, 2) if sales else 0,
      "currency": "COP",
      "notifications": notification_summary(),
  }
 finally:
  conn.close()


@router.post("/notifications/retry", dependencies=[Depends(require_admin)])
def retry_store_notifications():
 result = process_pending_notifications(limit=20)
 return {**result, "queue": notification_summary()}


@router.get("/invoices", dependencies=[Depends(require_admin)])
def list_invoices(limit: int = Query(50, ge=1, le=200)):
 conn = get_connection()
 try:
  with conn.cursor() as cur:
   cur.execute(
       """SELECT i.id,i.invoice_number,i.public_token,i.order_id,o.reference,
                 o.customer_name,o.customer_email,i.total_amount,i.currency,
                 i.status,i.issued_at
          FROM store_invoices i JOIN store_orders o ON o.id=i.order_id
          ORDER BY i.issued_at DESC LIMIT %s""",
       (limit,),
   )
   rows = cur.fetchall()
   _ensure_invoice_tokens(cur, rows)
  conn.commit()
  return rows
 finally:
  conn.close()


@router.get("/invoices/{invoice_number}")
def get_invoice(
    invoice_number: str,
    token: str = Query(min_length=40, max_length=64),
):
 conn = get_connection()
 try:
  with conn.cursor() as cur:
   cur.execute(
       """SELECT i.invoice_number,i.status,i.total_amount,i.currency,i.issued_at,
                 o.reference,o.customer_name,oi.product_id,oi.product_title,
                 oi.quantity,oi.unit_price,oi.line_total
          FROM store_invoices i JOIN store_orders o ON o.id=i.order_id
          JOIN store_order_items oi ON oi.order_id=o.id
          WHERE i.invoice_number=%s AND i.public_token=%s ORDER BY oi.id""",
       (invoice_number, token),
   )
   rows = cur.fetchall()
  if not rows:
   raise HTTPException(404, "Factura no encontrada")
  h = rows[0]
  return {"invoice_number": h["invoice_number"], "status": h["status"], "total_amount": h["total_amount"], "currency": h["currency"],
          "issued_at": h["issued_at"], "reference": h["reference"], "customer_name": h["customer_name"], "items": rows}
 finally:
  conn.close()
