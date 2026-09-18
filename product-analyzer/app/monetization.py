"""
Endpoints de monetización: los tres canales conviven en la misma app.

  - Afiliados:     POST /monetize/click/{product_id}   -> registra el click y redirige
  - Dropshipping:  POST /monetize/orders                -> crea una orden con margen
  - Suscripciones: POST /monetize/subscribe              -> asigna un plan a un usuario
  - Resumen:       GET  /monetize/revenue-summary        -> ingresos por canal
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from db import get_connection

router = APIRouter(prefix="/monetize", tags=["monetización"])


# ---------- Afiliados ----------

@router.get("/click/{product_id}")
def register_click(product_id: int, user_id: int | None = None):
    """
    Registra el click de afiliado y redirige al producto real en la
    plataforma de origen (Mercado Libre / Amazon / TikTok).
    La conversión (venta confirmada) se marca después, vía webhook o
    reporte manual del programa de afiliados de cada plataforma.
    """
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT product_url FROM products WHERE id = %s", (product_id,))
        row = cur.fetchone()
        if not row or not row["product_url"]:
            conn.close()
            raise HTTPException(status_code=404, detail="Producto no encontrado o sin URL")

        cur.execute(
            "INSERT INTO affiliate_clicks (product_id, user_id) VALUES (%s, %s)",
            (product_id, user_id),
        )
    conn.commit()
    conn.close()
    return RedirectResponse(row["product_url"])


@router.post("/click/{click_id}/confirm")
def confirm_conversion(click_id: int, commission_earned: float):
    """Marca un click como convertido, con la comisión reportada por la plataforma."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE affiliate_clicks SET converted = TRUE, commission_earned = %s "
            "WHERE id = %s RETURNING id",
            (commission_earned, click_id),
        )
        if not cur.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail="Click no encontrado")
    conn.commit()
    conn.close()
    return {"status": "confirmado"}


# ---------- Dropshipping ----------

class OrderCreate(BaseModel):
    user_id: int
    product_id: int
    quantity: int = 1
    sale_price: float
    supplier_cost: float


@router.post("/orders")
def create_order(order: OrderCreate):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO orders (user_id, product_id, quantity, sale_price, supplier_cost)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, margin
            """,
            (order.user_id, order.product_id, order.quantity,
             order.sale_price, order.supplier_cost),
        )
        result = cur.fetchone()
    conn.commit()
    conn.close()
    return {"order_id": result["id"], "margin": result["margin"]}


@router.get("/orders/{user_id}")
def list_orders(user_id: int):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT o.*, p.title FROM orders o JOIN products p ON p.id = o.product_id "
            "WHERE o.user_id = %s ORDER BY o.created_at DESC",
            (user_id,),
        )
        results = cur.fetchall()
    conn.close()
    return results


# ---------- Suscripciones SaaS ----------

class SubscribeRequest(BaseModel):
    user_id: int
    plan_name: str  # 'free', 'pro', 'business'


@router.post("/subscribe")
def subscribe(req: SubscribeRequest):
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM subscription_plans WHERE name = %s", (req.plan_name,))
        plan = cur.fetchone()
        if not plan:
            conn.close()
            raise HTTPException(status_code=404, detail="Plan no encontrado")

        cur.execute(
            """
            INSERT INTO subscriptions (user_id, plan_id, renews_at)
            VALUES (%s, %s, NOW() + INTERVAL '30 days')
            ON CONFLICT (user_id) DO UPDATE SET
                plan_id = EXCLUDED.plan_id,
                status = 'active',
                started_at = NOW(),
                renews_at = NOW() + INTERVAL '30 days'
            RETURNING id;
            """,
            (req.user_id, plan["id"]),
        )
        sub_id = cur.fetchone()["id"]
    conn.commit()
    conn.close()
    return {"subscription_id": sub_id, "plan": req.plan_name}


@router.get("/plans")
def list_plans():
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM subscription_plans ORDER BY price ASC")
        results = cur.fetchall()
    conn.close()
    return results


# ---------- Resumen de ingresos ----------

@router.get("/revenue-summary")
def revenue_summary():
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM revenue_summary")
        results = cur.fetchall()
    conn.close()
    return results

