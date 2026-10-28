import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from db import get_connection
from telegram_alert import _add_url

POLL_SECONDS = 5


def claim_next(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE link_queue SET status = 'processing'
            WHERE id = (
                SELECT id FROM link_queue WHERE status = 'pending'
                ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED
            )
            RETURNING id, url
            """
        )
        row = cur.fetchone()
    conn.commit()
    return row


def main():
    print("[leer_enlaces] esperando enlaces...")
    while True:
        conn = get_connection()
        try:
            row = claim_next(conn)
            if not row:
                time.sleep(POLL_SECONDS)
                continue
            product_id, product, score = _add_url("accesorios|" + row["url"])
            with conn.cursor() as cur:
                if product_id is None:
                    cur.execute(
                        "UPDATE link_queue SET status = 'failed', error = %s, processed_at = NOW() WHERE id = %s",
                        (str(product), row["id"]),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE link_queue
                        SET status = 'done', product_id = %s, title = %s, price = %s,
                            currency = %s, image_url = %s, processed_at = NOW()
                        WHERE id = %s
                        """,
                        (product_id, product["title"], product.get("price"),
                         product.get("currency"), product.get("image_url"), row["id"]),
                    )
            conn.commit()
            print("[leer_enlaces] " + row["url"] + " -> " + ("ok" if product_id else "fallo"))
        except Exception as exc:
            print("[leer_enlaces] error: " + str(exc))
            time.sleep(POLL_SECONDS)
        finally:
            conn.close()


if __name__ == "__main__":
    main()