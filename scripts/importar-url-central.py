"""Cliente CLI autorizado: envía URLs y metadatos a Central para afiliación y Telegram."""
import argparse
import json
import os
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]

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
    load_env()
    parser = argparse.ArgumentParser(description="Envía un producto de Amazon o AliExpress a Central y Telegram.")
    parser.add_argument("--url", required=True, help="URL del producto, sin credenciales en la URL")
    parser.add_argument("--category", default="accesorios")
    parser.add_argument("--title")
    parser.add_argument("--price", type=float)
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--image")
    parser.add_argument("--api-url", default=os.getenv("CENTRAL_API_URL", "").strip())
    args = parser.parse_args()
    api_key = os.getenv("CENTRAL_INGEST_API_KEY", "").strip()
    if not args.api_url or not api_key:
        raise SystemExit("Configura CENTRAL_API_URL y CENTRAL_INGEST_API_KEY en el entorno del CLI.")
    endpoint = args.api_url.rstrip("/")
    if not endpoint.endswith("/api/v1/products/import-cli"):
        endpoint += "/api/v1/products/import-cli"
    payload = {
        "url": args.url, "category": args.category, "title": args.title,
        "price": args.price, "currency": args.currency, "image_url": args.image,
    }
    payload = {key: value for key, value in payload.items() if value is not None}
    response = requests.post(endpoint, json=payload, headers={"X-Central-Ingest-Key": api_key}, timeout=30)
    if not response.ok:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise SystemExit(f"Central rechazó la importación ({response.status_code}): {detail}")
    result = response.json()
    product = result.get("product") or {}
    print(json.dumps({
        "id": product.get("id"),
        "title": product.get("title"),
        "platform": product.get("platform"),
        "affiliate_link_generated": result.get("affiliate_link_generated", bool(product.get("affiliate_url"))),
        "telegram_notified": result.get("telegram_notified", False),
        "affiliate_url": product.get("affiliate_url"),
    }, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
