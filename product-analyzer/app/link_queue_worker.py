"""Procesa enlaces recibidos por Telegram desde la cola PostgreSQL."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

from db import get_connection
from telegram_alert import _add_url, _send, _send_product, _token

POLL_SECONDS = max(1, int(os.getenv("LINK_QUEUE_POLL_SECONDS", "5")))
MAX_ATTEMPTS = max(1, int(os.getenv("LINK_QUEUE_MAX_ATTEMPTS", "3")))
STALE_MINUTES = max(1, int(os.getenv("LINK_QUEUE_STALE_MINUTES", "15")))


def claim_next(conn):
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE link_queue
                  SET status=CASE WHEN attempts >= %s THEN 'failed' ELSE 'pending' END,
                      error=COALESCE(error, 'Trabajo abandonado; se recuperó automáticamente'),
                      processed_at=CASE WHEN attempts >= %s THEN NOW() ELSE processed_at END,
                      processing_at=NULL
                WHERE status='processing'
                  AND processing_at < NOW() - (%s * INTERVAL '1 minute')""",
            (MAX_ATTEMPTS, MAX_ATTEMPTS, STALE_MINUTES),
        )
        cur.execute(
            """WITH next_job AS (
                   SELECT id FROM link_queue WHERE status='pending' AND attempts < %s
                    ORDER BY created_at, id FOR UPDATE SKIP LOCKED LIMIT 1
               )
               UPDATE link_queue q
                  SET status='processing', attempts=q.attempts + 1,
                      processing_at=NOW(), error=NULL
                 FROM next_job WHERE q.id=next_job.id
               RETURNING q.id, q.url, q.chat_id, q.attempts""",
            (MAX_ATTEMPTS,),
        )
        row = cur.fetchone()
    conn.commit()
    return row


def finish_job(row, product_id, payload):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if product_id is None:
                cur.execute(
                    """UPDATE link_queue SET status='failed', error=%s, processed_at=NOW(),
                              processing_at=NULL WHERE id=%s""",
                    (str(payload)[:2000], row['id']),
                )
            else:
                cur.execute(
                    """UPDATE link_queue SET status='done', product_id=%s, title=%s,
                              price=%s, currency=%s, image_url=%s, processed_at=NOW(),
                              processing_at=NULL, error=NULL WHERE id=%s""",
                    (product_id, payload.get('title'), payload.get('price'),
                     payload.get('currency'), payload.get('image_url'), row['id']),
                )
        conn.commit()
    finally:
        conn.close()


def mark_retry(row, exc):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE link_queue
                      SET status=CASE WHEN attempts >= %s THEN 'failed' ELSE 'pending' END,
                          error=%s, processing_at=NULL,
                          processed_at=CASE WHEN attempts >= %s THEN NOW() ELSE NULL END
                    WHERE id=%s""",
                (MAX_ATTEMPTS, str(exc)[:2000], MAX_ATTEMPTS, row['id']),
            )
        conn.commit()
    finally:
        conn.close()


def notify(row, product=None, error=None):
    token = _token()
    if not token or not row.get('chat_id'):
        return False
    if error:
        sent = _send(token, row['chat_id'], f"Falló la importación del enlace (cola #{row['id']}): {error}")
    else:
        sent = _send_product(token, row['chat_id'], {
            'title': product.get('title'), 'current_price': product.get('price'),
            'currency': product.get('currency'), 'product_url': product.get('product_url'),
            'image_url': product.get('image_url'), 'platform': product.get('platform'),
            'category': 'accesorios',
        })
    if sent:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute('UPDATE link_queue SET notified_at=NOW() WHERE id=%s', (row['id'],))
            conn.commit()
        finally:
            conn.close()
    return sent


def process_one(row):
    product_id, payload, _score = _add_url('accesorios|' + row['url'])
    finish_job(row, product_id, payload)
    if product_id is None:
        notify(row, error='No fue posible importar el producto; revisa que el enlace sea público y válido.')
    else:
        notify(row, product=payload)


def main():
    print('[link-queue] worker iniciado; esperando enlaces')
    while True:
        conn = None
        try:
            conn = get_connection()
            row = claim_next(conn)
        except Exception as exc:
            print(f'[link-queue] error consultando cola: {type(exc).__name__}: {exc}')
            time.sleep(POLL_SECONDS)
            continue
        finally:
            if conn is not None:
                conn.close()
        if row is None:
            time.sleep(POLL_SECONDS)
            continue
        try:
            process_one(row)
            print(f"[link-queue] #{row['id']} procesado")
        except Exception as exc:
            print(f"[link-queue] #{row['id']} error: {type(exc).__name__}: {exc}")
            try:
                mark_retry(row, exc)
            except Exception as retry_exc:
                print(f'[link-queue] no se pudo registrar reintento: {retry_exc}')
            time.sleep(POLL_SECONDS)


if __name__ == '__main__':
    main()
