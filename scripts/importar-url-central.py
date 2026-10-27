"""Importa una URL autorizada a Central y envía el producto a Telegram."""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "product-analyzer" / "app"
sys.path.insert(0, str(APP))

def load_env():
    for name in (".env", "product-analyzer/.env"):
        path = ROOT / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip(chr(34)).strip(chr(39)))

def main():
    parser = argparse.ArgumentParser(description="Importa una URL de Amazon o AliExpress y notifica a Telegram.")
    parser.add_argument("--url", required=True, help="URL pública del producto")
    parser.add_argument("--category", default="accesorios")
    parser.add_argument("--title")
    parser.add_argument("--price", type=float)
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--image")
    args = parser.parse_args()
    load_env()

    from analysis import score_product
    from db import get_connection, upsert_product
    from telegram_alert import _send_product, _token
    from url_import import import_url

    product = import_url(args.url, args.category, title=args.title, price=args.price, currency=args.currency, image_url=args.image)
    conn = get_connection()
    try:
        product_id = upsert_product(conn, product["platform"], args.category, product)
        score = None
        if product.get("price") is not None:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT AVG(p.current_price) AS category_avg
                    FROM products p JOIN categories c ON c.id=p.category_id
                    WHERE c.name=%s AND p.currency=%s AND p.current_price IS NOT NULL AND p.is_active=TRUE
                """, (args.category, product["currency"]))
                row = cur.fetchone()
            score = score_product(conn, product_id, float(row["category_avg"] or product["price"]))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print("Producto importado:", product["title"])
    print("Plataforma:", product["platform"], "| ID:", product_id)
    print("URL afiliada:", product.get("affiliate_url") or product["product_url"])
    print("Score:", round(float(score), 1) if score is not None else "pendiente por falta de precio")
    if product.get("source_metadata", {}).get("affiliate_link_status") == "pending_or_not_configured":
        print("AVISO: AliExpress no confirmó enlace afiliado; revisa App Key, App Secret, Tracking ID y elegibilidad.")
    token = _token()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID") or "").strip()
    if token and chat_id:
        sent = _send_product(token, chat_id, {
            "title": product["title"], "current_price": product.get("price"),
            "currency": product.get("currency"), "product_url": product["product_url"],
            "affiliate_url": product.get("affiliate_url"), "image_url": product.get("image_url"),
            "platform": product["platform"], "opportunity_score": score,
        })
        print("Telegram:", "enviado" if sent else "no confirmado")
    else:
        print("Telegram: no enviado; configura TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID.")

if __name__ == "__main__":
    main()
