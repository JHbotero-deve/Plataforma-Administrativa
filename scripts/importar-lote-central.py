"""Importa en lote exportaciones JSON/CSV de herramientas de catálogo autorizadas."""
import argparse
import csv
import json
import os
import re
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
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def first(row, *names):
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip():
            return value
    return None


def number(value):
    if value is None or str(value).strip() == "":
        return None
    raw = re.sub(r"[^0-9,.+-]", "", str(value)).strip()
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".") if raw.rfind(",") > raw.rfind(".") else raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def images(value):
    if not value:
        return []
    if isinstance(value, list):
        values = value
    else:
        try:
            parsed = json.loads(str(value))
            values = parsed if isinstance(parsed, list) else [parsed]
        except (TypeError, ValueError):
            values = re.split(r"[|;]", str(value))
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))[:10]


def category_name(value):
    text = str(value or "").lower()
    if any(word in text for word in ("shoe", "footwear", "sneaker", "boot", "sandals", "calzado", "zapato", "tenis", "bota")):
        return "calzado"
    if any(word in text for word in ("clothing", "apparel", "shirt", "pants", "dress", "jacket", "ropa", "camisa", "pantalon", "vestido")):
        return "ropa"
    if any(word in text for word in ("accessor", "bag", "backpack", "watch", "jewelry", "phone case", "accesorio", "bolso", "mochila", "reloj", "funda", "gorra")):
        return "accesorios"
    return None


def normalize(row):
    url = first(row, "url", "product_url", "link", "original_url", "product_link")
    if not url:
        raise ValueError("falta url/product_url/link")
    item = {
        "url": str(url).strip(),
        "external_id": first(row, "external_id", "item_id", "asin", "sku", "product_id"),
        "category": category_name(first(row, "category", "category_name")),
        "title": first(row, "title", "name", "product_name"),
        "description": first(row, "description"),
        "price": number(first(row, "price", "current_price", "sale_price")),
        "previous_price": number(first(row, "previous_price", "original_price", "list_price", "regular_price")),
        "currency": str(first(row, "currency", "currency_code") or "USD").upper(),
        "image_url": first(row, "image_url", "main_image", "image", "image_link"),
        "gallery_urls": images(first(row, "gallery_urls", "images", "image_gallery")),
        "rating": number(first(row, "rating", "stars")),
        "reviews_count": int(number(first(row, "reviews_count", "review_count", "reviews")) or 0),
        "sales_estimate": int(number(first(row, "sales_estimate", "sales", "sold_count")) or 0) or None,
    }
    if item["image_url"] and item["image_url"] not in item["gallery_urls"]:
        item["gallery_urls"].insert(0, item["image_url"])
    return {key: value for key, value in item.items() if value is not None and value != []}


def read_rows(path):
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            return list(csv.DictReader(file))
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, dict):
        data = data.get("products", data.get("items", [data]))
    if not isinstance(data, list):
        raise ValueError("El JSON debe ser una lista o contener products/items.")
    return data


def main():
    load_env()
    parser = argparse.ArgumentParser(description="Importa productos desde exportaciones JSON/CSV hacia Central y Telegram.")
    parser.add_argument("--file", required=True, help="Archivo JSON o CSV exportado por tu CLI de catálogo.")
    parser.add_argument("--api-url", default=os.getenv("CENTRAL_API_URL", "").strip())
    parser.add_argument("--dry-run", action="store_true", help="Valida el archivo sin enviarlo.")
    parser.add_argument("--batch-size", type=int, default=25)
    args = parser.parse_args()
    path = Path(args.file).expanduser()
    if not path.is_file() or path.suffix.lower() not in {".json", ".csv"}:
        raise SystemExit("Indica un archivo JSON o CSV existente.")
    if not 1 <= args.batch_size <= 100:
        raise SystemExit("--batch-size debe estar entre 1 y 100.")
    try:
        products = [normalize(row) for row in read_rows(path)]
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"No se pudo leer el archivo: {exc}")
    if not products:
        raise SystemExit("El archivo no contiene productos.")
    print(f"Productos válidos: {len(products)}")
    if args.dry_run:
        print(json.dumps(products[:3], ensure_ascii=False, indent=2))
        return
    api_key = os.getenv("CENTRAL_INGEST_API_KEY", "").strip()
    if not args.api_url or not api_key:
        raise SystemExit("Configura CENTRAL_API_URL y CENTRAL_INGEST_API_KEY.")
    endpoint = args.api_url.rstrip("/")
    if not endpoint.endswith("/api/v1/products/import-cli"):
        endpoint += "/api/v1/products/import-cli"
    endpoint += "/batch"
    imported = failed = notified = 0
    for start in range(0, len(products), args.batch_size):
        batch = products[start:start + args.batch_size]
        try:
            response = requests.post(
                endpoint,
                json={"products": batch},
                headers={"X-Central-Ingest-Key": api_key},
                timeout=180,
            )
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError) as exc:
            failed += len(batch)
            print(f"Lote {start + 1}-{start + len(batch)} falló: {type(exc).__name__}")
            continue
        imported += int(result.get("imported", 0))
        failed += int(result.get("failed", len(batch) - int(result.get("imported", 0))))
        notified += int(result.get("telegram_notified", 0))
        for item in result.get("items", []):
            if not item.get("ok"):
                print(f"Producto {start + int(item.get('index', 0)) + 1}: {item.get('error', 'error')}")
    print(json.dumps({"total": len(products), "imported": imported, "failed": failed, "telegram_notified": notified}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
