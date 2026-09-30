"""Pedidos públicos y métricas de la tienda."""
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from db import get_connection
import secrets,datetime

router=APIRouter(prefix="/store",tags=["store"])
class StoreOrderPayload(BaseModel):
 product_id:int=Field(gt=0); publication_id:Optional[int]=Field(default=None,gt=0)
 customer_name:str=Field(min_length=2,max_length=200); customer_email:str=Field(min_length=5,max_length=255)
 customer_phone:Optional[str]=Field(default=None,max_length=40); quantity:int=Field(default=1,ge=1,le=100)
 notes:Optional[str]=Field(default=None,max_length=2000)

@router.post("/orders")
def create_store_order(payload:StoreOrderPayload):
 conn=get_connection()
 try:
  with conn.cursor() as cur:
   cur.execute("""SELECT p.id,p.title,p.image_url,p.product_url,p.current_price,p.currency,p.stock,pl.name AS platform,
                        pc.id AS publication_id,pc.sale_price,pc.cost_price
                 FROM products p JOIN platforms pl ON pl.id=p.platform_id
                 LEFT JOIN published_cards pc ON pc.product_id=p.id AND pc.is_published=TRUE
                 WHERE p.id=%s AND p.is_active=TRUE""",(payload.product_id,))
   p=cur.fetchone()
   if not p: raise HTTPException(404,"Producto no encontrado")
   if payload.publication_id and p["publication_id"]!=payload.publication_id: raise HTTPException(409,"La publicación no corresponde al producto solicitado")
   if p["stock"] is not None and payload.quantity>p["stock"]: raise HTTPException(409,"Stock insuficiente")
   sale=float(p["sale_price"] or p["current_price"] or 0); cost=float(p["cost_price"] if p["cost_price"] is not None else p["current_price"] or 0)
   if sale<=0: raise HTTPException(400,"Producto sin precio de venta")
   total=sale*payload.quantity;profit=(sale-cost)*payload.quantity;ref=f"REQ-{datetime.datetime.utcnow():%Y%m%d%H%M%S}-{payload.product_id}-{secrets.token_hex(4).upper()}"
   cur.execute("""INSERT INTO store_orders(reference,product_id,publication_id,customer_name,customer_email,customer_phone,quantity,unit_price,cost_unit_price,total_amount,estimated_profit,currency,status,payment_status,source,product_title_snapshot,product_image_snapshot,product_url_snapshot,platform_snapshot,notes)
                 VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PENDING','PENDING','TIENDA',%s,%s,%s,%s,%s)
                 RETURNING id,reference,total_amount,estimated_profit,currency,status,payment_status""",
                (ref,payload.product_id,p["publication_id"],payload.customer_name.strip(),payload.customer_email.strip().lower(),payload.customer_phone,payload.quantity,sale,cost,total,profit,p["currency"],p["title"],p["image_url"],p["product_url"],p["platform"],payload.notes))
   out=cur.fetchone()
  conn.commit();return out
 finally: conn.close()

@router.get("/orders")
def list_store_orders(limit:int=Query(50,ge=1,le=200)):
 conn=get_connection()
 try:
  with conn.cursor() as cur:
   cur.execute("""SELECT id,reference,product_id,product_title_snapshot,platform_snapshot,quantity,total_amount,estimated_profit,currency,status,payment_status,created_at,paid_at FROM store_orders ORDER BY created_at DESC LIMIT %s""",(limit,))
   return cur.fetchall()
 finally: conn.close()

@router.get("/metrics")
def store_metrics():
 conn=get_connection()
 try:
  with conn.cursor() as cur:
   cur.execute("""SELECT COUNT(*) FILTER(WHERE payment_status='PAID') paid_orders,
                         COALESCE(SUM(total_amount) FILTER(WHERE payment_status='PAID'),0) gross_sales,
                         COALESCE(SUM(estimated_profit) FILTER(WHERE payment_status='PAID'),0) estimated_profit,
                         COALESCE(SUM(quantity) FILTER(WHERE payment_status='PAID'),0) units_sold FROM store_orders""")
   r=cur.fetchone()
  sales=float(r["gross_sales"] or 0);profit=float(r["estimated_profit"] or 0)
  return {"paid_orders":int(r["paid_orders"] or 0),"gross_sales":sales,"estimated_profit":profit,"units_sold":int(r["units_sold"] or 0),"margin_pct":round(profit/sales*100,2) if sales else 0}
 finally: conn.close()


@router.get("/invoices")
def list_invoices(limit:int=Query(50,ge=1,le=200)):
 conn=get_connection()
 try:
  with conn.cursor() as cur:
   cur.execute("""SELECT i.id,i.invoice_number,i.order_id,o.reference,o.customer_name,o.customer_email,
                         i.total_amount,i.currency,i.status,i.issued_at
                  FROM store_invoices i JOIN store_orders o ON o.id=i.order_id
                  ORDER BY i.issued_at DESC LIMIT %s""",(limit,))
   return cur.fetchall()
 finally: conn.close()

@router.get("/invoices/{invoice_number}")
def get_invoice(invoice_number:str):
 conn=get_connection()
 try:
  with conn.cursor() as cur:
   cur.execute("""SELECT i.invoice_number,i.status,i.total_amount,i.currency,i.issued_at,
                         o.reference,o.customer_name,o.customer_email,o.customer_phone,
                         oi.product_id,oi.product_title,oi.quantity,oi.unit_price,oi.line_total
                  FROM store_invoices i JOIN store_orders o ON o.id=i.order_id
                  JOIN store_order_items oi ON oi.order_id=o.id
                  WHERE i.invoice_number=%s ORDER BY oi.id""",(invoice_number,))
   rows=cur.fetchall()
  if not rows: raise HTTPException(404,"Factura no encontrada")
  h=rows[0]
  return {"invoice_number":h["invoice_number"],"status":h["status"],"total_amount":h["total_amount"],"currency":h["currency"],
          "issued_at":h["issued_at"],"reference":h["reference"],"customer_name":h["customer_name"],"customer_email":h["customer_email"],
          "customer_phone":h["customer_phone"],"items":rows}
 finally: conn.close()
