"""Durable delivery for store receipts and sale notifications."""

import html
import os
import smtplib
from email.message import EmailMessage
from typing import Any
from urllib.parse import urlencode

import requests

from db import get_connection


RESEND_URL = "https://api.resend.com/emails"


def delivery_configuration() -> dict[str, Any]:
    resend_ready = bool(
        os.getenv("RESEND_API_KEY", "").strip()
        and os.getenv("RESEND_FROM_EMAIL", "").strip()
    )
    smtp_ready = bool(
        os.getenv("SMTP_HOST", "").strip()
        and (os.getenv("SMTP_FROM", "").strip() or os.getenv("SMTP_FROM_EMAIL", "").strip())
    )
    return {
        "email_configured": resend_ready or smtp_ready,
        "email_provider": "smtp" if smtp_ready else "resend" if resend_ready else None,
        "resend_configured": resend_ready,
        "smtp_configured": smtp_ready,
        "seller_email_configured": bool(os.getenv("CENTRAL_ADMIN_EMAIL", "").strip()),
        "telegram_configured": bool(
            os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
            and os.getenv("TELEGRAM_CHAT_ID", "").strip()
        ),
    }


def _money(amount: Any, currency: str) -> str:
    value = float(amount or 0)
    return f"{currency} {value:,.0f}".replace(",", ".")


def _invoice_url(invoice_number: str, token: str) -> str:
    base_url = os.getenv(
        "PUBLIC_STORE_URL", "https://central-7ykr.vercel.app"
    ).strip().rstrip("/")
    return (
        f"{base_url}/factura.html?"
        f"{urlencode({'numero': invoice_number, 'token': token})}"
    )


def _email_content(kind: str, order: dict, invoice: dict, items: list[dict]):
    reference = html.escape(str(order["reference"]))
    invoice_number = html.escape(str(invoice["invoice_number"]))
    customer_name = html.escape(str(order["customer_name"]))
    total = _money(order["total_amount"], order["currency"])
    url = html.escape(
        _invoice_url(invoice["invoice_number"], invoice["public_token"]),
        quote=True,
    )
    subject = (
        f"Comprobante de compra {order['reference']}"
        if kind == "CUSTOMER_RECEIPT"
        else f"Nueva venta aprobada {order['reference']}"
    )
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item['product_title']))}</td>"
        f"<td>{int(item['quantity'])}</td>"
        f"<td>{_money(item['line_total'], order['currency'])}</td>"
        "</tr>"
        for item in items
    )
    profit = _money(order["estimated_profit"], order["currency"])
    greeting = (
        f"Hola {customer_name}, recibimos tu pago y confirmamos tu compra."
        if kind == "CUSTOMER_RECEIPT"
        else f"Se aprobó una venta para {customer_name}."
    )
    detail = (
        ""
        if kind == "CUSTOMER_RECEIPT"
        else f"<p>Utilidad bruta estimada según los costos registrados: <b>{profit}</b>. "
        "No descuenta comisiones, impuestos, envíos ni devoluciones.</p>"
    )
    body = (
        f"<p>{greeting}</p>"
        f"<p>Pedido <b>{reference}</b> · Comprobante <b>{invoice_number}</b></p>"
        "<table cellpadding='8' cellspacing='0' border='1' "
        "style='border-collapse:collapse;border-color:#ddd;width:100%'>"
        "<thead><tr><th>Producto</th><th>Cantidad</th><th>Valor</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        f"<p>Total pagado: <b>{html.escape(total)}</b></p>"
        f"{detail}"
        f"<p><a href=\"{url}\">Ver y descargar comprobante</a></p>"
        "<p>Central · Comprobante comercial. No reemplaza una factura electrónica "
        "validada por la DIAN.</p>"
    )
    text = (
        f"{greeting}\nPedido {order['reference']} · Comprobante "
        f"{invoice['invoice_number']}\nTotal pagado: {total}\n"
        f"Ver comprobante: "
        f"{_invoice_url(invoice['invoice_number'], invoice['public_token'])}\n"
        "Este comprobante comercial no reemplaza una factura electrónica validada por la DIAN."
    )
    return subject, text, body


def _send_email(
    recipient: str,
    subject: str,
    text: str,
    body: str,
    idempotency_key: str,
) -> None:
    config = delivery_configuration()
    if config["resend_configured"] and not config["smtp_configured"]:
        response = requests.post(
            RESEND_URL,
            headers={
                "Authorization": f"Bearer {os.environ['RESEND_API_KEY'].strip()}",
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json={
                "from": os.environ["RESEND_FROM_EMAIL"].strip(),
                "to": [recipient],
                "subject": subject,
                "text": text,
                "html": body,
            },
            timeout=20,
        )
        if not response.ok:
            raise RuntimeError(f"Resend rechazó el correo (HTTP {response.status_code}).")
        return

    host = os.getenv("SMTP_HOST", "").strip()
    sender = (
        os.getenv("SMTP_FROM", "").strip()
        or os.getenv("SMTP_FROM_EMAIL", "").strip()
    )
    if not host or not sender:
        raise RuntimeError("No hay proveedor de correo configurado.")
    port = int(os.getenv("SMTP_PORT", "587"))
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(text)
    message.add_alternative(body, subtype="html")

    use_ssl = os.getenv("SMTP_USE_SSL", "").strip().lower() in {"1", "true", "yes"}
    smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with smtp_class(host, port, timeout=20) as client:
        if not use_ssl and os.getenv("SMTP_STARTTLS", "true").strip().lower() not in {
            "0",
            "false",
            "no",
        }:
            client.starttls()
        username = os.getenv("SMTP_USER", "").strip()
        password = os.getenv("SMTP_PASSWORD", "")
        if username:
            if not password:
                raise RuntimeError("SMTP_PASSWORD no está configurado.")
            client.login(username, password)
        client.send_message(message)


def _load_order(order_id: int) -> tuple[dict, dict, list[dict]]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT o.id,o.reference,o.customer_name,o.customer_email,
                          o.total_amount,o.estimated_profit,o.currency,
                          i.invoice_number,i.public_token
                   FROM store_orders o
                   JOIN store_invoices i ON i.order_id=o.id
                   WHERE o.id=%s""",
                (order_id,),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError("No se encontró el pedido o su comprobante.")
            invoice = {
                "invoice_number": row["invoice_number"],
                "public_token": row["public_token"],
            }
            order = {
                key: value
                for key, value in row.items()
                if key not in {"invoice_number", "public_token"}
            }
            cur.execute(
                """SELECT product_title,quantity,line_total
                   FROM store_order_items WHERE order_id=%s ORDER BY id""",
                (order_id,),
            )
            items = cur.fetchall()
            return order, invoice, items
    finally:
        conn.close()


def _deliver(notification: dict) -> None:
    order, invoice, items = _load_order(notification["order_id"])
    kind = notification["notification_type"]
    if notification["channel"] == "EMAIL":
        subject, text, body = _email_content(kind, order, invoice, items)
        _send_email(
            notification["recipient"],
            subject,
            text,
            body,
            f"central-store-notification-{notification['id']}",
        )
        return
    if notification["channel"] == "TELEGRAM":
        from notifications import send_store_sale_notification

        if not send_store_sale_notification(order, invoice, items):
            raise RuntimeError("Telegram no confirmó el aviso de venta.")
        return
    raise RuntimeError("Canal de notificación desconocido.")


def _finish_delivery(notification_id: int, error: Exception | None) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if error is None:
                cur.execute(
                    """UPDATE store_notification_outbox
                       SET status='SENT',sent_at=NOW(),locked_at=NULL,
                           last_error=NULL,updated_at=NOW()
                       WHERE id=%s""",
                    (notification_id,),
                )
            else:
                cur.execute(
                    """UPDATE store_notification_outbox
                       SET status='PENDING',attempts=attempts+1,
                           available_at=NOW()+make_interval(
                             secs => LEAST(3600,(30*POWER(2,LEAST(attempts,7)))::integer)
                           ),
                           locked_at=NULL,last_error=%s,updated_at=NOW()
                       WHERE id=%s""",
                    (type(error).__name__, notification_id),
                )
        conn.commit()
    finally:
        conn.close()


def process_pending_notifications(limit: int = 20) -> dict[str, int]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE store_notification_outbox
                   SET status='PENDING',locked_at=NULL,updated_at=NOW()
                   WHERE status='PROCESSING'
                     AND locked_at < NOW()-INTERVAL '5 minutes'"""
            )
            cur.execute(
                """SELECT id,order_id,channel,notification_type,recipient
                   FROM store_notification_outbox
                   WHERE status='PENDING' AND available_at<=NOW()
                   ORDER BY id
                   LIMIT %s
                   FOR UPDATE SKIP LOCKED
                   """,
                (limit,),
            )
            notifications = cur.fetchall()
            if notifications:
                cur.execute(
                    """UPDATE store_notification_outbox
                       SET status='PROCESSING',locked_at=NOW(),updated_at=NOW()
                       WHERE id=ANY(%s)""",
                    ([item["id"] for item in notifications],),
                )
        conn.commit()
    finally:
        conn.close()

    delivered = 0
    failed = 0
    for notification in notifications:
        error = None
        try:
            _deliver(notification)
            delivered += 1
        except Exception as exc:
            error = exc
            failed += 1
            print(
                "[store-notifications] delivery failed "
                f"id={notification['id']} error={type(exc).__name__}"
            )
        _finish_delivery(notification["id"], error)
    return {"claimed": len(notifications), "delivered": delivered, "failed": failed}


def notification_summary() -> dict[str, Any]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT COUNT(*) FILTER(WHERE status='PENDING') AS pending,
                          COUNT(*) FILTER(WHERE status='PROCESSING') AS processing,
                          COUNT(*) FILTER(WHERE status='SENT') AS sent,
                          COUNT(*) FILTER(WHERE status='PENDING' AND attempts>0) AS retrying
                   FROM store_notification_outbox"""
            )
            counts = cur.fetchone() or {}
    finally:
        conn.close()
    return {**delivery_configuration(), **{key: int(counts.get(key) or 0) for key in (
        "pending", "processing", "sent", "retrying"
    )}}
