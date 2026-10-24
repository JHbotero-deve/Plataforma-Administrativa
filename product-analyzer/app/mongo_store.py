"""MongoDB secundario para imágenes de producto y auditoría.
PostgreSQL sigue siendo la fuente de verdad transaccional de Central.
"""
import base64
import hashlib
import os
import re
import binascii
from datetime import datetime, timezone

import requests
from bson import Binary, ObjectId

_client = None
_db = None

ALLOWED_IMAGE_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/gif",
    "image/avif", "image/svg+xml",
}
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def _get_db():
    global _client, _db
    uri = (os.getenv("MONGO_URL") or "").strip()
    if not uri:
        return None
    if _db is None:
        from pymongo import MongoClient
        _client = MongoClient(uri, serverSelectionTimeoutMS=2500, connectTimeoutMS=2500)
        _db = _client[os.getenv("MONGO_DB_NAME", "central")]
    return _db


def _store_image_bytes(data: bytes, content_type: str, original_url: str = "", **metadata):
    content_type = (content_type or "").split(";", 1)[0].lower().strip()
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise ValueError("formato de imagen no permitido")
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("imagen vacía o mayor de 8 MB")
    db = _get_db()
    if db is None:
        return None
    digest = hashlib.sha256(data).hexdigest()
    found = db.product_images.find_one({"sha256": digest}, {"_id": 1})
    if found:
        return str(found["_id"])
    doc = {
        "data": Binary(data),
        "content_type": content_type,
        "sha256": digest,
        "original_url": (original_url or "")[:2000],
        "created_at": datetime.now(timezone.utc),
        **metadata,
    }
    return str(db.product_images.insert_one(doc).inserted_id)


def store_product_image(url: str, **metadata):
    """Guarda una imagen HTTP(S) o una imagen data: subida desde el editor."""
    url = (url or "").strip()
    if not url:
        return None

    if url.startswith("mongo://"):
        return url[8:]

    if url.startswith("data:"):
        match = re.match(
            r"^data:(image/(?:jpeg|png|webp|gif|avif|svg\+xml));base64,([A-Za-z0-9+/=\r\n]+)$",
            url,
            re.IGNORECASE,
        )
        if not match:
            raise ValueError("imagen subida inválida; se requiere data URI base64")
        try:
            data = base64.b64decode(re.sub(r"\s+", "", match.group(2)), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("contenido base64 de imagen inválido") from exc
        return _store_image_bytes(data, match.group(1), "data:image-upload", **metadata)

    response = requests.get(
        url,
        headers={"User-Agent": "CentralMedia/1.0"},
        timeout=15,
        allow_redirects=True,
    )
    response.raise_for_status()
    return _store_image_bytes(
        response.content,
        (response.headers.get("Content-Type") or "").split(";", 1)[0],
        url,
        **metadata,
    )


def store_product_image_bytes(data: bytes, content_type: str, original_url: str = "telegram-upload", **metadata):
    """Guarda bytes recibidos desde Telegram sin publicar el archivo externamente."""
    return _store_image_bytes(data, content_type, original_url, **metadata)


def read_product_image(file_id: str):
    db = _get_db()
    if db is None:
        return None, None
    try:
        doc = db.product_images.find_one({"_id": ObjectId(str(file_id))})
        if not doc:
            return None, None
        return bytes(doc.get("data") or b""), doc.get("content_type") or "application/octet-stream"
    except Exception:
        return None, None


def audit_event(event: str, **data):
    try:
        db = _get_db()
        if db is None:
            return False
        doc = {
            "event": event,
            "created_at": datetime.now(timezone.utc),
            "data": data,
        }
        db.security_audit.insert_one(doc)
        return True
    except Exception as exc:
        print(f"[mongo] auditoría no disponible: {exc}")
        return False


def health():
    try:
        db = _get_db()
        if db is None:
            return {"configured": False, "connected": False}
        db.command("ping")
        return {"configured": True, "connected": True, "images": db.product_images.count_documents({})}
    except Exception as exc:
        return {"configured": True, "connected": False, "error": str(exc)[:180]}

