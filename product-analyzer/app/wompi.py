

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
        raise HTTPException(
            status_code=503, detail=f"Integración Wompi no configurada: {name}")
    return value


def _amount_to_cents(amount: Any) -> int:
    cents = int(round(float(amount) * 100))
    if cents <= 0:
        raise HTTPException(
            status_code=400, detail="El monto debe ser mayor que cero")
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
        raise HTTPException(
            status_code=400, detail="Evento Wompi sin firma completa")
    try:
        values = "".join(str(_get_path(event.get("data", {}), prop))
                    for prop in properties)
    except KeyError as exc:
        raise HTTPException(
            status_code=400, detail=f"Propiedad de firma ausente: {exc.args[0]}")
    return hashlib.sha256(f"{values}{timestamp}{secret}".encode("utf-8")).hexdigest()


def _checkout_base_url() -> str:
    return os.getenv("WOMPI_CHECKOUT_URL", "https://checkout.wompi.co/p/").rstrip("/") + "/"


class ProductCheckout(BaseModel):
    product_id: int
    publication_id: int | None = None
    quantity: int = 1
    customer_name: str
    customer_email: str
    customer_phone: str | None = None
    redirect_url: str | None = None


@router.post("/checkout/product")
def create_product_checkout(req: ProductCheckout):
    if req.quantity<1 or req.quantity>100: raise HTTPException(status_code=400,detail="Cantidad inválida")
    public_key=_required("WOMPI_PUBLIC_KEY")
    currency=os.getenv("WOMPI_CURRENCY","COP").strip().upper()
    environment=os.getenv("WOMPI_ENVIRONMENT","prod").strip().lower()
    if currency!="COP": raise HTTPException(status_code=500,detail="Wompi Colombia requiere moneda COP")
    conn=get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT p.id,p.title,p.image_url,p.product_url,p.current_price,p.currency,p.stock,
                                  pc.id AS publication_id,pc.sale_price,pc.cost_price
                           FROM products p LEFT JOIN published_cards pc ON pc.product_id=p.id AND pc.is_published=TRUE
                           WHERE p.id=%s AND p.is_active=TRUE""",(req.product_id,))
            product=cur.fetchone()
            if not product: raise HTTPException(status_code=404,detail="Producto no encontrado")
            if req.publication_id and product["publication_id"]!=req.publication_id: raise HTTPException(status_code=409,detail="La publicación no corresponde al producto solicitado")
            if product["stock"] is not None and req.quantity>product["stock"]: raise HTTPException(status_code=409,detail="Stock insuficiente")
            sale=float(product["sale_price"] or product["current_price"] or 0)
            cost=float(product["cost_price"] if product["cost_price"] is not None else product["current_price"] or 0)
            if sale<=0: raise HTTPException(status_code=400,detail="Producto sin precio de venta")
            total=sale*req.quantity; cents=_amount_to_cents(total); reference=f"ORD-{secrets.token_hex(10).upper()}"; signature=_integrity_signature(reference,cents,currency)
            cur.execute("""INSERT INTO store_orders
                (reference,product_id,publication_id,customer_name,customer_email,customer_phone,quantity,unit_price,cost_unit_price,total_amount,estimated_profit,currency,status,payment_status,source,product_title_snapshot,product_image_snapshot,product_url_snapshot,platform_snapshot)
                SELECT %s,p.id,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PENDING','PENDING','TIENDA',p.title,p.image_url,p.product_url,pl.name
                FROM products p JOIN platforms pl ON pl.id=p.platform_id WHERE p.id=%s RETURNING id,reference""",
                (reference,product["publication_id"],req.customer_name.strip(),req.customer_email.strip().lower(),req.customer_phone,req.quantity,sale,cost,total,(sale-cost)*req.quantity,currency,req.product_id))
            order=cur.fetchone()
            cur.execute("""INSERT INTO store_order_items(order_id,product_id,publication_id,product_title,quantity,unit_price,cost_unit_price,line_total,estimated_profit)
                           SELECT %s,p.id,%s,p.title,%s,%s,%s,%s,%s
                           FROM products p WHERE p.id=%s""",
                        (order["id"],product["publication_id"],req.quantity,sale,cost,total,(sale-cost)*req.quantity,req.product_id))
            cur.execute("""INSERT INTO payment_transactions
                (reference,provider,product_id,order_id,customer_email,amount_in_cents,currency,status,environment)
                VALUES (%s,'wompi',%s,%s,%s,%s,%s,'PENDING',%s) RETURNING id,reference""",
                (reference,req.product_id,order["id"],req.customer_email.strip().lower(),cents,currency,environment))
            payment=cur.fetchone()
        conn.commit()
    finally: conn.close()
    redirect=req.redirect_url or os.getenv("WOMPI_REDIRECT_URL","").strip() or None
    params=[("public-key",public_key),("currency",currency),("amount-in-cents",str(cents)),("reference",reference),("signature:integrity",signature),("customer-data:email",req.customer_email.strip().lower()),("customer-data:full-name",req.customer_name.strip())]
    if req.customer_phone: params += [("customer-data:phone-number",req.customer_phone),("customer-data:phone-number-prefix","+57")]
    if redirect: params.append(("redirect-url",redirect))
    return {"order_id":order["id"],"payment_id":payment["id"],"product_id":req.product_id,"reference":reference,"amount_in_cents":cents,"currency":currency,"checkout_url":f"{_checkout_base_url()}?{urlencode(params)}"}


@router.post("/checkout/cart")
async def create_cart_checkout(request: Request):
    """Checkout de carrito: crea UN pedido con varios ítems y devuelve la URL de Wompi."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON inválido")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Cuerpo inválido")

    # --- Cliente (acepta campos planos o anidados en "customer") ---
    customer = body.get("customer") if isinstance(body.get("customer"), dict) else {}
    name = str(body.get("customer_name") or customer.get("name") or body.get("name") or "").strip()
    email = str(body.get("customer_email") or customer.get("email") or body.get("email") or "").strip().lower()
    phone = body.get("customer_phone") or customer.get("phone") or body.get("phone")
    phone = str(phone).strip() if phone else None
    if not name or "@" not in email:
        raise HTTPException(status_code=400, detail="Nombre y correo válidos son obligatorios")

    # --- Ítems del carrito (acepta "items" o "cart"; product_id / productId / id) ---
    raw_items = body.get("items") or body.get("cart") or []
    if not isinstance(raw_items, list) or not raw_items:
        raise HTTPException(status_code=400, detail="El carrito está vacío")
    if len(raw_items) > 30:
        raise HTTPException(status_code=400, detail="Demasiados ítems en el carrito")

    wanted: dict[int, dict] = {}
    for it in raw_items:
        if not isinstance(it, dict):
            raise HTTPException(status_code=400, detail="Ítem de carrito inválido")
        try:
            pid = int(it.get("product_id") or it.get("productId") or it.get("id"))
            qty = int(it.get("quantity") or it.get("qty") or 1)
            pub_raw = it.get("publication_id") or it.get("publicationId")
            pub = int(pub_raw) if pub_raw else None
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Ítem de carrito inválido")
        if qty < 1 or qty > 100:
            raise HTTPException(status_code=400, detail="Cantidad inválida")
        entry = wanted.setdefault(pid, {"qty": 0, "publication_id": pub})
        entry["qty"] += qty
        if entry["qty"] > 100:
            raise HTTPException(status_code=400, detail="Cantidad inválida")

    # --- Configuración Wompi ---
    public_key = _required("WOMPI_PUBLIC_KEY")
    currency = os.getenv("WOMPI_CURRENCY", "COP").strip().upper()
    environment = os.getenv("WOMPI_ENVIRONMENT", "prod").strip().lower()
    if currency != "COP":
        raise HTTPException(status_code=500, detail="Wompi Colombia requiere moneda COP")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT p.id, p.title, p.image_url, p.product_url, p.current_price, p.stock,
                          pc.id AS publication_id, pc.sale_price, pc.cost_price,
                          pl.name AS platform_name
                   FROM products p
                   LEFT JOIN published_cards pc ON pc.product_id=p.id AND pc.is_published=TRUE
                   LEFT JOIN platforms pl ON pl.id=p.platform_id
                   WHERE p.id = ANY(%s) AND p.is_active=TRUE""",
                (list(wanted.keys()),),
            )
            rows = {r["id"]: r for r in cur.fetchall()}

            # --- Validar y calcular cada línea con precios del servidor ---
            lines = []
            for pid, entry in wanted.items():
                p = rows.get(pid)
                if not p:
                    raise HTTPException(status_code=404, detail=f"Producto {pid} no encontrado")
                if entry["publication_id"] and p["publication_id"] != entry["publication_id"]:
                    raise HTTPException(status_code=409, detail="La publicación no corresponde al producto solicitado")
                qty = entry["qty"]
                if p["stock"] is not None and qty > p["stock"]:
                    raise HTTPException(status_code=409, detail=f"Stock insuficiente: {p['title']}")
                sale = float(p["sale_price"] or p["current_price"] or 0)
                cost = float(p["cost_price"] if p["cost_price"] is not None else p["current_price"] or 0)
                if sale <= 0:
                    raise HTTPException(status_code=400, detail=f"Producto sin precio de venta: {p['title']}")
                lines.append({
                    "product_id": pid, "publication_id": p["publication_id"],
                    "title": p["title"], "qty": qty, "sale": sale, "cost": cost,
                })

            total = sum(l["sale"] * l["qty"] for l in lines)
            cost_total = sum(l["cost"] * l["qty"] for l in lines)
            total_qty = sum(l["qty"] for l in lines)
            profit = total - cost_total
            cents = _amount_to_cents(total)
            reference = f"ORD-{secrets.token_hex(10).upper()}"
            signature = _integrity_signature(reference, cents, currency)

            first = lines[0]
            first_row = rows[first["product_id"]]
            title_snapshot = first["title"] if len(lines) == 1 else f"{first['title']} y {len(lines) - 1} más"

            # --- Pedido (cabecera) ---
            cur.execute(
                """INSERT INTO store_orders
                   (reference, product_id, publication_id, customer_name, customer_email, customer_phone,
                    quantity, unit_price, cost_unit_price, total_amount, estimated_profit, currency,
                    status, payment_status, source,
                    product_title_snapshot, product_image_snapshot, product_url_snapshot, platform_snapshot)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PENDING','PENDING','TIENDA',%s,%s,%s,%s)
                   RETURNING id, reference""",
                (reference, first["product_id"], first["publication_id"], name, email, phone,
                 total_qty, total / total_qty, cost_total / total_qty, total, profit, currency,
                 title_snapshot, first_row["image_url"], first_row["product_url"], first_row["platform_name"]),
            )
            order = cur.fetchone()

            # --- Pedido (ítems) ---
            for l in lines:
                cur.execute(
                    """INSERT INTO store_order_items
                       (order_id, product_id, publication_id, product_title, quantity,
                        unit_price, cost_unit_price, line_total, estimated_profit)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (order["id"], l["product_id"], l["publication_id"], l["title"], l["qty"],
                     l["sale"], l["cost"], l["sale"] * l["qty"], (l["sale"] - l["cost"]) * l["qty"]),
                )

            # --- Transacción de pago ---
            cur.execute(
                """INSERT INTO payment_transactions
                   (reference, provider, product_id, order_id, customer_email,
                    amount_in_cents, currency, status, environment)
                   VALUES (%s,'wompi',%s,%s,%s,%s,%s,'PENDING',%s) RETURNING id, reference""",
                (reference, first["product_id"], order["id"], email, cents, currency, environment),
            )
            payment = cur.fetchone()
        conn.commit()
    finally:
        conn.close()

    redirect = body.get("redirect_url") or os.getenv("WOMPI_REDIRECT_URL", "").strip() or None
    params = [
        ("public-key", public_key),
        ("currency", currency),
        ("amount-in-cents", str(cents)),
        ("reference", reference),
        ("signature:integrity", signature),
        ("customer-data:email", email),
        ("customer-data:full-name", name),
    ]
    if phone:
        params += [("customer-data:phone-number", phone), ("customer-data:phone-number-prefix", "+57")]
    if redirect:
        params.append(("redirect-url", redirect))

    return {
        "order_id": order["id"],
        "payment_id": payment["id"],
        "reference": reference,
        "items": len(lines),
        "amount_in_cents": cents,
        "currency": currency,
        "checkout_url": f"{_checkout_base_url()}?{urlencode(params)}",
    }


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
        raise HTTPException(
            status_code=500, detail="Wompi Colombia requiere moneda COP")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, price FROM subscription_plans WHERE name = %s", (req.plan_name,))
            plan = cur.fetchone()
            if not plan:
                raise HTTPException(
                    status_code=404, detail="Plan no encontrado")
            if float(plan["price"]) <= 0:
                raise HTTPException(
                    status_code=400, detail="El plan gratuito no requiere pago")

            amount_in_cents = _amount_to_cents(plan["price"])
            reference = f"SUB-{secrets.token_hex(12).upper()}"
            signature = _integrity_signature(
                reference, amount_in_cents, currency)
            redirect_url = req.redirect_url or os.getenv(
                "WOMPI_REDIRECT_URL", "").strip() or None

            cur.execute(
                """INSERT INTO payment_transactions
                    (reference, provider, plan_id, customer_email, amount_in_cents, currency, status, environment)
                    VALUES (%s, 'wompi', %s, %s, %s, %s, 'PENDING', %s)
                    RETURNING id, reference""",
                (reference, plan["id"], req.customer_email.strip(
                ).lower(), amount_in_cents, currency, environment),
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
    x_event_checksum: str | None = Header(
        default=None, alias="X-Event-Checksum"),
):
    event = await request.json()
    if event.get("event") != "transaction.updated":
        return {"received": True, "processed": False}

    calculated = _event_checksum(event)
    supplied = (x_event_checksum or event.get(
        "signature", {}).get("checksum") or "").strip()
    if not supplied or not hmac.compare_digest(calculated.lower(), supplied.lower()):
        raise HTTPException(
            status_code=401, detail="Firma de evento Wompi inválida")

    transaction = event.get("data", {}).get("transaction", {})
    reference = transaction.get("reference")
    transaction_id = transaction.get("id")
    status = transaction.get("status")
    if not reference or not transaction_id or not status:
        raise HTTPException(
            status_code=400, detail="Evento de transacción incompleto")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO payment_events
                (provider, event_type, transaction_id, checksum, payload)
                VALUES ('wompi', %s, %s, %s, %s)
                ON CONFLICT (provider, event_type, transaction_id, checksum) DO NOTHING""",
                (event["event"], transaction_id, supplied, Json(event)),
            )
            cur.execute(
                """UPDATE payment_transactions
                    SET transaction_id=%s, status=%s, payment_method_type=%s,
                    status_message=%s, event_checksum=%s, raw_event=%s,
                    updated_at=NOW(),
                    paid_at=CASE WHEN %s='APPROVED' AND paid_at IS NULL THEN NOW() ELSE paid_at END
                    WHERE reference=%s
                    RETURNING id, plan_id, order_id, customer_email""",
                (
                    transaction_id, status, transaction.get(
                        "payment_method_type"),
                    transaction.get("status_message"), supplied, Json(
                        event), status, reference,
                ),
            )
            payment = cur.fetchone()

            if payment and payment.get("order_id") and status == "APPROVED":
                cur.execute("""UPDATE store_orders SET status='PAID',payment_status='PAID',paid_at=NOW(),updated_at=NOW()
                               WHERE id=%s AND payment_status<>'PAID' RETURNING id,product_id,quantity""",(payment["order_id"],))
                order=cur.fetchone()
                if order:
                    cur.execute("""UPDATE products p SET stock=CASE WHEN p.stock IS NULL THEN NULL ELSE GREATEST(p.stock-oi.quantity,0) END
                                   FROM store_order_items oi
                                   WHERE oi.order_id=%s AND oi.product_id=p.id""",(order["id"],))
                    cur.execute("""INSERT INTO store_invoices(order_id,invoice_number,total_amount,currency,status)
                                   SELECT id,'FAC-'||to_char(NOW(),'YYYYMMDDHH24MISS')||'-'||id,total_amount,currency,'ISSUED'
                                   FROM store_orders WHERE id=%s
                                   ON CONFLICT(order_id) DO NOTHING""",(order["id"],))
            elif payment and payment.get("order_id") and status in {"DECLINED","VOIDED","ERROR"}:
                cur.execute("""UPDATE store_orders SET status=%s,payment_status=%s,updated_at=NOW() WHERE id=%s AND payment_status='PENDING'""",(status,status,payment["order_id"]))

            if payment and status == "APPROVED" and payment["plan_id"]:
                cur.execute(
                    "SELECT id FROM users WHERE LOWER(email)=LOWER(%s)", (payment["customer_email"],))
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
