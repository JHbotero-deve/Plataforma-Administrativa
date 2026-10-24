"""
Central Product Analyzer REST API.
"""

import os
import time
import secrets

import requests
from typing import Optional
from urllib.parse import quote, urljoin, urlparse, urlunparse

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Header
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from psycopg2.extras import Json
from mongo_store import store_product_image

from analysis import score_product
from amazon_api import search_amazon_products
from db import get_connection, upsert_product, _affiliate_url
from catalog_rules import discount_percentage
from monetization import router as monetization_router
from publications import router as publication_router
from store_orders import router as store_orders_router
from commerce import router as commerce_router
from auth import router as auth_router, require_admin
from tiktok_api import router as tiktok_creator_router
from url_import import import_url, _public_url
from wompi import router as wompi_router
from aliexpress_oauth import router as aliexpress_oauth_router


API_VERSION = "1.3.0"


app = FastAPI(
    title="Central Product Analyzer API",
    description="Ingesta, análisis, comparación y monetización de productos reales.",
    version=API_VERSION,
)

app.include_router(auth_router, prefix="/api/v1")
app.include_router(monetization_router, prefix="/api/v1")
app.include_router(wompi_router, prefix="/api/v1")
app.include_router(aliexpress_oauth_router, prefix="/api/v1")
app.include_router(tiktok_creator_router, prefix="/api/v1")
app.include_router(publication_router, prefix="/api/v1")
app.include_router(store_orders_router, prefix="/api/v1")
app.include_router(commerce_router, prefix="/api/v1")


@app.middleware("http")
async def normalize_legacy_api_prefix(request, call_next):
    path = request.scope.get("path", "")
    if path.startswith("/api/v1/v1/"):
        request.scope["path"] = "/api/v1/" + path[len("/api/v1/v1/"):]
    elif path == "/api/v1/v1":
        request.scope["path"] = "/api/v1"
    return await call_next(request)


default_origins = ",".join(
    [
        "http://localhost:3000",
        "http://localhost:8000",
        "https://central-7ykr.vercel.app",
    ]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv("ALLOWED_ORIGINS", default_origins).split(",")
        if origin.strip()
    ],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

core_router = APIRouter(tags=["core"])
@core_router.get("/media/image")
def proxy_product_image(url: str = Query(..., min_length=8, max_length=2_000_000)):
    """Entrega imágenes públicas de fuentes comerciales permitidas."""
    if url.startswith("mongo://"):
        from mongo_store import read_product_image
        data, content_type = read_product_image(url[8:])
        if not data:
            raise HTTPException(status_code=404, detail="Imagen Mongo no encontrada")
        return Response(content=data, media_type=content_type, headers={"Cache-Control":"public, max-age=86400"})

    current = url.strip()
    try:
        for _ in range(4):
            parsed = urlparse(current)
            if parsed.scheme == "http":
                current = urlunparse(("https", parsed.netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
            _public_url(current)
            response = requests.get(
                current,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; CentralImageProxy/1.0)",
                    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                },
                timeout=12,
                allow_redirects=False,
            )
            if 300 <= response.status_code < 400:
                location = response.headers.get("Location")
                if not location:
                    raise HTTPException(status_code=502, detail="La fuente de imagen devolvió una redirección sin destino")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            _public_url(response.url)
            break
        else:
            raise HTTPException(status_code=502, detail="Demasiadas redirecciones de imagen")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No fue posible obtener la imagen: {exc}")

    content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
    allowed_types = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif", "image/svg+xml"}
    if content_type not in allowed_types:
        raise HTTPException(status_code=415, detail="El recurso no es una imagen compatible")

    data = response.content
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="La imagen supera el límite permitido")

    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=3600, stale-while-revalidate=86400"},
    )


@core_router.get("/telegram/status")
def telegram_status():
    """Estado público mínimo del bot: no expone token ni chat ID."""
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    if not token:
        return {"configured": False, "connected": False, "username": None, "name": None}
    try:
        response = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=8)
        payload = response.json()
        if not response.ok or not payload.get("ok"):
            return {"configured": True, "connected": False, "username": None, "name": None}
        user = payload.get("result") or {}
        return {
            "configured": True,
            "connected": True,
            "username": user.get("username"),
            "name": user.get("first_name") or user.get("username"),
        }
    except (requests.RequestException, ValueError):
        return {"configured": True, "connected": False, "username": None, "name": None}


@core_router.get("/media/mongo/{file_id}")
def mongo_product_image(file_id: str):
    from mongo_store import read_product_image
    data, content_type = read_product_image(file_id)
    if not data:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")
    return Response(content=data, media_type=content_type, headers={"Cache-Control":"public, max-age=86400"})

@core_router.get("/mongo/health")
def mongo_health(_: dict = Depends(require_admin)):
    from mongo_store import health
    return health()

@core_router.get("/health")
def health():
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return {"status": "ok", "database": "ok", "version": API_VERSION}
        finally:
            conn.close()
    except Exception:
        raise HTTPException(
            status_code=503,
            detail={"status": "degraded", "database": "error"},
        )


def _publish_external_product(conn, product_id, product, platform_label):
    """Publica solo ofertas con descuento real >= umbral y enlace monetizable verificable."""
    discount_pct = discount_percentage(product.get("price"), product.get("previous_price"))
    threshold = float(os.getenv("CATALOG_MIN_DISCOUNT_PCT", "40"))
    if discount_pct is None or discount_pct < threshold:
        return False
    current_price = float(product.get("price") or 0)
    previous_price = float(product.get("previous_price") or 0)

    affiliate_url = str(product.get("affiliate_url") or _affiliate_url(product) or "").strip()
    if not affiliate_url.startswith(("https://", "http://")):
        return False

    with conn.cursor() as cur:
        cur.execute("""
            SELECT p.title, p.current_price, p.previous_price, p.currency, p.product_url,
                   p.image_url, p.affiliate_url, p.source_metadata,
                   COALESCE(s.opportunity_score, 0) AS opportunity_score
            FROM products p
            LEFT JOIN product_scores s ON s.product_id = p.id
            WHERE p.id = %s AND p.is_active = TRUE AND p.is_blocked = FALSE
        """, (product_id,))
        stored = cur.fetchone()
    if not stored:
        return False

    metadata = stored.get("source_metadata") or {}
    mongo_image_id = metadata.get("mongo_image_id") if isinstance(metadata, dict) else None
    image_url = ("mongo://" + str(mongo_image_id)) if mongo_image_id else (stored.get("image_url") or product.get("image_url"))
    product_url = affiliate_url or stored.get("affiliate_url") or stored.get("product_url")
    if not image_url or not str(image_url).strip() or not product_url:
        return False

    price = float(stored.get("current_price") or current_price)
    previous = float(stored.get("previous_price") or previous_price)
    currency = stored.get("currency") or product.get("currency") or "COP"
    price_display = f"{price:,.2f} {currency}"
    score = max(float(product.get("_score") or stored.get("opportunity_score") or 0), discount_pct)
    expiry_days = max(1, min(int(os.getenv("CATALOG_EXPIRY_DAYS", "7")), 30))
    with conn.cursor() as cur:
        # Las importaciones manuales por URL conservan su ciclo de vida propio.
        cur.execute("""
            UPDATE products
            SET catalog_expires_at = NOW() + (%s * INTERVAL '1 day')
            WHERE id = %s AND COALESCE(source_metadata->>'import_method', '') <> 'product_url'
        """, (expiry_days, product_id))
        max_cards = max(1, int(os.getenv("CATALOG_MAX_PUBLISHED_CARDS", "550")))
        cur.execute("""
            SELECT EXISTS (
                SELECT 1 FROM published_cards
                WHERE product_id = %s AND is_published = TRUE
            ) AS already_published
        """, (product_id,))
        already_published = bool(cur.fetchone()["already_published"])
        if not already_published:
            cur.execute("""
                SELECT COUNT(*) AS total
                FROM published_cards pc
                JOIN products p ON p.id = pc.product_id
                WHERE pc.is_published = TRUE AND p.is_active = TRUE
                  AND p.is_blocked = FALSE
                  AND (p.catalog_expires_at IS NULL OR p.catalog_expires_at > NOW())
            """)
            if int(cur.fetchone()["total"]) >= max_cards:
                return False
        cur.execute("""
            INSERT INTO published_cards (
                product_id, title, subtitle, price_display, image_url, product_url,
                sale_price, cost_price, profit_amount, profit_margin_pct,
                opportunity_score, footer, accent, is_published, published_at, updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0,0,%s,%s,%s,TRUE,NOW(),NOW())
            ON CONFLICT (product_id) DO UPDATE SET
                title=EXCLUDED.title, subtitle=EXCLUDED.subtitle,
                price_display=EXCLUDED.price_display, image_url=EXCLUDED.image_url,
                product_url=EXCLUDED.product_url, sale_price=EXCLUDED.sale_price,
                cost_price=EXCLUDED.cost_price, opportunity_score=EXCLUDED.opportunity_score,
                is_published=TRUE, published_at=NOW(), updated_at=NOW()
        """, (
            product_id, stored.get("title") or product["title"],
            f"{platform_label} · descuento verificado {discount_pct:.0f}%",
            price_display, image_url, product_url,
            price, previous, score, f"Oferta -{discount_pct:.0f}% · {platform_label}", "#ff8a00"
        ))
    return True

def _sync_store_catalog():
    """Busca hasta 500 ofertas reales verificadas; el total depende de las fuentes habilitadas."""
    conn = get_connection()
    target = max(1, min(int(os.getenv("CATALOG_SYNC_MAX_PRODUCTS", "500")), 500))
    threshold = float(os.getenv("CATALOG_MIN_DISCOUNT_PCT", "40"))
    expiry_days = max(1, min(int(os.getenv("CATALOG_EXPIRY_DAYS", "7")), 30))
    errors = []
    rejected = {"missing_reference_price": 0, "below_discount_threshold": 0, "missing_image_or_url": 0}
    candidates = {}
    imported = {"amazon": [], "aliexpress": []}
    auto_published = []
    expired_count = 0
    card_unpublished_count = 0
    locked = False
    queries = (
        ("electronica", "audifonos bluetooth"), ("electronica", "smartwatch"),
        ("electronica", "mouse gamer"), ("electronica", "teclado mecanico"),
        ("accesorios", "cargador usb c"), ("accesorios", "power bank"),
        ("electronica", "camara seguridad wifi"), ("electronica", "parlante bluetooth"),
        ("electronica", "monitor 4k"), ("electronica", "webcam"),
        ("electronica", "ssd 1tb"), ("electronica", "tablet android"),
        ("hogar", "cafetera"), ("hogar", "freidora aire"),
        ("hogar", "aspiradora robot"), ("hogar", "silla ergonomica"),
        ("accesorios", "mochila portatil"), ("electronica", "microfono usb"),
        ("electronica", "control videojuegos"), ("hogar", "lampara led"),
        ("electronica", "impresora multifuncional"), ("electronica", "audifonos deportivos"),
        ("electronica", "reloj deportivo"), ("electronica", "teclado compacto"),
        ("electronica", "mouse ergonomico"), ("electronica", "base refrigerante laptop"),
        ("electronica", "cable usb c"), ("electronica", "adaptador usb c"),
        ("electronica", "memoria ram ddr5"), ("electronica", "tarjeta micro sd"),
        ("electronica", "capturadora video"), ("electronica", "luces streaming"),
        ("electronica", "mini pc"), ("electronica", "laptop soporte"),
        ("electronica", "proyector full hd"), ("electronica", "lector ebooks"),
        ("hogar", "robot aspirador"), ("hogar", "humidificador"),
        ("hogar", "deshumidificador"), ("hogar", "bascula inteligente"),
        ("hogar", "olla electrica"), ("hogar", "sanduchera"),
        ("hogar", "molinillo cafe"), ("hogar", "dispensador agua"),
        ("hogar", "organizador closet"), ("hogar", "estanteria"),
        ("hogar", "cerradura inteligente"), ("hogar", "enchufe inteligente"),
        ("accesorios", "funda laptop"), ("accesorios", "protector pantalla"),
        ("accesorios", "tripi celular"), ("accesorios", "cargador carro"),
        ("accesorios", "organizador cables"), ("accesorios", "maleta viaje"),
        ("accesorios", "botella termica"), ("accesorios", "linterna recargable"),
        ("electronica", "barra sonido tv"), ("electronica", "parlante inteligente"),
        ("electronica", "camara accion"), ("electronica", "consola videojuegos"),
        ("electronica", "control bluetooth"), ("electronica", "disco duro 2tb"),
        ("hogar", "tira led"), ("hogar", "sensor movimiento"),
        ("hogar", "termostato inteligente"), ("hogar", "balanza cocina"),
        ("hogar", "aspiradora mano"), ("hogar", "ventilador escritorio"),
    )
    providers = (("amazon", "Amazon"), ("aliexpress", "AliExpress"))
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(734218906)")
            locked = bool(cur.fetchone()["pg_try_advisory_lock"])
        if not locked:
            return {"status": "already_running", "message": "Ya hay una ingesta de catálogo en curso."}

        for platform, label in providers:
            if platform == "amazon" and not all(
                os.getenv(name, "").strip()
                for name in ("AMAZON_CREDENTIAL_ID", "AMAZON_CREDENTIAL_SECRET", "AMAZON_PARTNER_TAG")
            ):
                errors.append("Amazon: falta configurar acceso Creators API/Partner Tag.")
                continue
            if platform == "aliexpress":
                errors.append(
                    "AliExpress: no se importa como oferta hasta disponer del precio original "
                    "verificado por API afiliada; OAuth por sí solo no lo acredita."
                )
                continue

            source_count = 0
            for category, query in queries:
                if source_count >= target:
                    break
                try:
                    if platform == "amazon":
                        found = search_amazon_products(
                            query,
                            limit=10,
                            category=category,
                            prime_only=True,
                            min_saving_percent=int(threshold),
                            official_only=True,
                        )
                except Exception as exc:
                    errors.append(f"{label} '{query}': {type(exc).__name__}: {str(exc)[:180]}")
                    continue

                for item in found or []:
                    external_id = str(item.get("external_id") or "").strip()
                    title = str(item.get("title") or "").strip()
                    try:
                        current_price = float(item.get("price") or 0)
                        reference_price = float(item.get("previous_price") or 0)
                    except (TypeError, ValueError):
                        current_price, reference_price = 0, 0
                    if not external_id or not title or current_price <= 0:
                        continue
                    discount_pct = discount_percentage(current_price, reference_price)
                    if discount_pct is None:
                        rejected["missing_reference_price"] += 1
                        continue
                    if discount_pct < threshold:
                        rejected["below_discount_threshold"] += 1
                        continue
                    image_url = str(item.get("image_url") or "").strip()
                    product_url = str(item.get("product_url") or "").strip()
                    affiliate_url = str(item.get("affiliate_url") or _affiliate_url(item) or "").strip()
                    if not image_url.startswith(("https://", "http://")) or not product_url.startswith(("https://", "http://")) or not affiliate_url.startswith(("https://", "http://")):
                        rejected["missing_image_or_url"] += 1
                        continue
                    key = (platform, external_id.upper())
                    if key in candidates:
                        continue
                    normalized = dict(item)
                    normalized.update({
                        "platform": platform,
                        "price": current_price,
                        "previous_price": reference_price,
                        "source_metadata": {
                            **(item.get("source_metadata") or {}),
                            "catalog_ingestion": "verified_discount_search",
                            "verified_discount_pct": discount_pct,
                            "reference_price_source": (
                "amazon_creators_api"
                            ),
                            "amazon_delivery_filter": "Prime" if platform == "amazon" else None,
                        },
                    })
                    candidates[key] = {
                        "platform": platform,
                        "label": label,
                        "category": category,
                        "product": normalized,
                        "discount_pct": discount_pct,
                    }
                    source_count += 1
        best_deals = sorted(
            candidates.values(),
            key=lambda item: item["discount_pct"],
            reverse=True,
        )[:target]
        for deal in best_deals:
            product = deal["product"]
            try:
                product_id = upsert_product(conn, deal["platform"], deal["category"], product)
                product["_score"] = deal["discount_pct"]
                published = _publish_external_product(conn, product_id, product, deal["label"])
                conn.commit()
                imported[deal["platform"]].append({
                    "id": product_id,
                    "title": product["title"],
                    "discount_pct": deal["discount_pct"],
                })
                if published:
                    auto_published.append({
                        "id": product_id,
                        "title": product["title"],
                        "platform": deal["platform"],
                        "discount_pct": deal["discount_pct"],
                    })
            except Exception as exc:
                conn.rollback()
                errors.append(
                    f"{deal['platform']} {product.get('external_id')}: "
                    f"{type(exc).__name__}: {str(exc)[:180]}"
                )

        with conn.cursor() as cur:
            cur.execute("""
                UPDATE products
                SET is_active=FALSE, updated_at=NOW()
                WHERE is_active=TRUE
                  AND catalog_expires_at IS NOT NULL
                  AND catalog_expires_at<=NOW()
                  AND COALESCE(source_metadata->>'import_method','') <> 'product_url'
            """)
            expired_count = cur.rowcount
            cur.execute("""
                UPDATE published_cards pc
                SET is_published=FALSE, updated_at=NOW()
                FROM products p
                WHERE pc.product_id=p.id
                  AND pc.is_published=TRUE
                  AND p.is_active=FALSE
                  AND p.catalog_expires_at IS NOT NULL
                  AND p.catalog_expires_at<=NOW()
            """)
            card_unpublished_count = cur.rowcount
        conn.commit()
        result = {
            "status": "complete",
            "limit": target,
            "eligible_candidates": len(candidates),
            "imported": {platform: len(items) for platform, items in imported.items()},
            "published": len(auto_published),
            "published_products": auto_published,
            "expired_deactivated": expired_count,
            "cards_unpublished": card_unpublished_count,
            "discount_threshold_pct": threshold,
            "expiry_days": expiry_days,
            "rejected": rejected,
            "errors": errors[:20],
        }
        print({"event": "catalog_ingestion_complete", **{k: v for k, v in result.items() if k != "published_products"}}, flush=True)
        return result
    finally:
        if locked:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(734218906)")
                conn.commit()
            except Exception as exc:
                print({"event": "catalog_advisory_unlock_failed", "error": type(exc).__name__}, flush=True)
        conn.close()


@core_router.post("/store/sync")
def sync_store_catalog(_: dict = Depends(require_admin)):
    """Ingresa y publica ofertas verificadas de los proveedores disponibles."""
    return _sync_store_catalog()


@core_router.post("/amazon/sync")
def sync_amazon_store(limit: int = Query(20, ge=1, le=20), _: dict = Depends(require_admin)):
    """Compatibilidad heredada: dirigir la ingesta de ofertas al sincronizador común."""
    return {
        "mode": "verified_catalog_injection",
        "imported": 0,
        "published": 0,
        "message": (
            "Para buscar ofertas verificadas de todos los proveedores disponibles, "
            "usa /store/sync o el comando Telegram /inyectar."
        ),
    }


@core_router.get("/pipeline/summary")
def pipeline_summary(_: dict = Depends(require_admin)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pl.name AS platform, COUNT(p.id) AS product_count,
                       MAX(p.updated_at) AS last_update
                FROM platforms pl
                LEFT JOIN products p
                  ON p.platform_id = pl.id AND p.is_active = TRUE
                GROUP BY pl.name
                ORDER BY pl.name
                """
            )
            return cur.fetchall()
    finally:
        conn.close()


@core_router.get("/products/{product_id}")
def get_product(product_id: int):
    """Ficha pública completa de un producto real, enlazada a su fuente original."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id, p.title, pl.name AS platform, c.name AS category,
                       p.current_price, p.previous_price, p.currency, p.image_url,
                       p.image_gallery, p.product_url, p.affiliate_url, p.stock,
                       p.sku, p.external_id, p.description, p.rating, p.reviews_count,
                       p.sales_estimate, p.source_metadata, p.updated_at,
                       p.catalog_expires_at, p.model_url, p.model_shape,
                       s.name AS seller_name, s.reputation AS seller_reputation
                FROM products p
                JOIN platforms pl ON pl.id = p.platform_id
                LEFT JOIN categories c ON c.id = p.category_id
                LEFT JOIN sellers s ON s.id = p.seller_id
                WHERE p.id = %s AND p.is_active = TRUE AND p.is_blocked = FALSE AND p.current_price > 0 AND (p.catalog_expires_at IS NULL OR p.catalog_expires_at > NOW())
                """,
                (product_id,),
            )
            product = cur.fetchone()
            if not product:
                raise HTTPException(status_code=404, detail="Producto no encontrado")
            product["checkout_mode"] = "CENTRAL" if str(product["platform"]).lower() == "personal" else "EXTERNAL"
            return {"product": product}
    finally:
        conn.close()


@core_router.get("/products")
def list_products(
    category: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            query = """
                SELECT p.id, p.title, pl.name AS platform, c.name AS category,
                       p.current_price, p.currency, p.rating, p.reviews_count,
                       p.sales_estimate, p.image_url, p.image_gallery, p.product_url, p.affiliate_url, p.updated_at, p.source_metadata, p.description, p.previous_price, p.stock, p.sku,
                       p.catalog_expires_at, p.model_url, p.model_shape,
                       s.opportunity_score
                FROM products p
                JOIN platforms pl ON pl.id = p.platform_id
                LEFT JOIN categories c ON c.id = p.category_id
                LEFT JOIN product_scores s ON s.product_id = p.id
                WHERE p.is_active = TRUE AND p.is_blocked = FALSE
                  AND p.current_price > 0
                  AND (p.catalog_expires_at IS NULL OR p.catalog_expires_at > NOW())
            """
            params = []
            if category:
                query += " AND c.name = %s"
                params.append(category)
            if platform:
                query += " AND pl.name = %s"
                params.append(platform)
            query += " ORDER BY COALESCE(s.opportunity_score, 0) DESC, p.updated_at DESC LIMIT %s"
            params.append(limit)
            cur.execute(query, params)
            return cur.fetchall()
    finally:
        conn.close()


@core_router.get("/pipeline/metrics")
def pipeline_metrics(_: dict = Depends(require_admin)):
    """Métricas del último ciclo y de las fuentes reales."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, started_at, finished_at, status, duration_ms,
                       expired_products, active_products, high_opportunity,
                       amazon_products, mercadolibre_products, tiktok_products,
                       telegram_prepared, error_count, errors
                FROM pipeline_runs
                ORDER BY started_at DESC
                LIMIT 1
            """)
            latest = cur.fetchone()
            cur.execute("""
                SELECT COUNT(*) AS runs,
                       COUNT(*) FILTER (WHERE status = 'success') AS successful_runs,
                       COUNT(*) FILTER (WHERE status <> 'success') AS failed_runs,
                       MAX(started_at) AS last_run
                FROM pipeline_runs
                WHERE started_at >= NOW() - INTERVAL '24 hours'
            """)
            day = cur.fetchone()
        return {
            "latest": latest,
            "last_24h": day,
            "sources": {
                "amazon": {
                    "products": int((latest or {}).get("amazon_products") or 0),
                    "configured": bool(os.getenv("AMAZON_CLIENT_ID") or os.getenv("AMAZON_REFRESH_TOKEN")),
                },
                "tiktok": {
                    "products": int((latest or {}).get("tiktok_products") or 0),
                    "configured": bool(os.getenv("TIKTOK_ACCESS_TOKEN") or os.getenv("TIKTOK_CREATOR_ACCESS_TOKEN")),
                },
                "telegram": {
                    "prepared": int((latest or {}).get("telegram_prepared") or 0),
                    "configured": bool(os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")),
                },
            },
        }
    finally:
        conn.close()


@core_router.get("/intelligence/overview")
def intelligence_overview(_: dict = Depends(require_admin)):
    conn=get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT COUNT(*) AS active_products,COUNT(*) FILTER(WHERE COALESCE(s.opportunity_score,0)>=70) AS high_opportunity,ROUND(AVG(COALESCE(s.opportunity_score,0))::numeric,1) AS avg_opportunity,COUNT(*) FILTER(WHERE p.catalog_expires_at IS NOT NULL AND p.catalog_expires_at<=NOW()+INTERVAL '24 hours') AS expiring_24h,COUNT(*) FILTER(WHERE COALESCE(p.sales_estimate,0)>0) AS products_with_demand FROM products p LEFT JOIN product_scores s ON s.product_id=p.id WHERE p.is_active=TRUE""")
            c=cur.fetchone()
            cur.execute("""SELECT COUNT(*) AS orders,COUNT(*) FILTER(WHERE payment_status IN ('PAID','APPROVED') OR paid_at IS NOT NULL) AS paid_orders,COALESCE(SUM(total_amount) FILTER(WHERE payment_status IN ('PAID','APPROVED') OR paid_at IS NOT NULL),0) AS revenue,COALESCE(SUM(estimated_profit) FILTER(WHERE payment_status IN ('PAID','APPROVED') OR paid_at IS NOT NULL),0) AS estimated_profit,COUNT(*) FILTER(WHERE status IN ('DELIVERED','COMPLETED')) AS delivered FROM store_orders""")
            o=cur.fetchone()
            cur.execute("""SELECT (SELECT COUNT(*) FROM affiliate_clicks) AS clicks,(SELECT COUNT(*) FROM store_leads) AS leads,(SELECT COUNT(*) FROM store_returns) AS returns,(SELECT COUNT(*) FROM store_invoices) AS invoices""")
            f=cur.fetchone()
            cur.execute("""SELECT p.id,p.title,pl.name AS platform,p.current_price,p.currency,COALESCE(s.opportunity_score,0) AS opportunity_score,COALESCE(p.sales_estimate,0) AS sales_estimate,p.rating,p.reviews_count,COALESCE((SELECT COUNT(*) FROM affiliate_clicks ac WHERE ac.product_id=p.id),0) AS clicks,COALESCE((SELECT COUNT(*) FROM store_orders so WHERE so.product_id=p.id AND (so.payment_status IN ('PAID','APPROVED') OR so.paid_at IS NOT NULL)),0) AS paid_orders FROM products p JOIN platforms pl ON pl.id=p.platform_id LEFT JOIN product_scores s ON s.product_id=p.id WHERE p.is_active=TRUE ORDER BY COALESCE(s.opportunity_score,0) DESC,COALESCE(p.sales_estimate,0) DESC,p.updated_at DESC LIMIT 8""")
            top=cur.fetchall()
            cur.execute("""SELECT pl.name AS platform,COUNT(p.id) AS products,ROUND(AVG(COALESCE(s.opportunity_score,0))::numeric,1) AS avg_score,COALESCE(SUM(p.sales_estimate),0) AS demand FROM platforms pl LEFT JOIN products p ON p.platform_id=pl.id AND p.is_active=TRUE LEFT JOIN product_scores s ON s.product_id=p.id GROUP BY pl.name HAVING COUNT(p.id)>0 ORDER BY products DESC""")
            src=cur.fetchall()
        n=lambda v:int(v or 0); d=lambda v:float(v or 0)
        return {"catalog":{"active_products":n(c["active_products"]),"high_opportunity":n(c["high_opportunity"]),"avg_opportunity":d(c["avg_opportunity"]),"expiring_24h":n(c["expiring_24h"]),"products_with_demand":n(c["products_with_demand"])},"commerce":{"orders":n(o["orders"]),"paid_orders":n(o["paid_orders"]),"revenue":d(o["revenue"]),"estimated_profit":d(o["estimated_profit"]),"delivered":n(o["delivered"])},"funnel":{"clicks":n(f["clicks"]),"leads":n(f["leads"]),"returns":n(f["returns"]),"invoices":n(f["invoices"])},"top_products":[{"id":x["id"],"title":x["title"],"platform":x["platform"],"current_price":d(x["current_price"]),"currency":x["currency"],"opportunity_score":d(x["opportunity_score"]),"sales_estimate":n(x["sales_estimate"]),"clicks":n(x["clicks"]),"paid_orders":n(x["paid_orders"])} for x in top],"sources":[{"platform":x["platform"],"products":n(x["products"]),"avg_score":d(x["avg_score"]),"demand":n(x["demand"])} for x in src]}
    finally:
        conn.close()

def _seo_slug(value: str) -> str:
    import re, unicodedata
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:90] or "producto"

@core_router.get("/publications/product/{product_id}")
def public_product(product_id: int):
    conn=get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT
                p.id AS id,
                pc.id AS publication_id,
                pc.product_id,
                COALESCE(NULLIF(pc.title, ''), p.title) AS title,
                pc.subtitle,
                pc.price_display,
                pc.sale_price,
                pc.opportunity_score,
                pc.footer,
                p.description,
                p.current_price,
                p.previous_price,
                p.currency,
                CASE
                    WHEN COALESCE(p.source_metadata->>'mongo_image_id', '') <> ''
                    THEN 'mongo://' || (p.source_metadata->>'mongo_image_id')
                    ELSE COALESCE(NULLIF(pc.image_url, ''), NULLIF(p.image_url, ''))
                END AS image_url,
                p.image_gallery,
                COALESCE(NULLIF(pc.product_url, ''), NULLIF(p.product_url, '')) AS product_url,
                p.affiliate_url,
                p.sku,
                p.external_id,
                p.stock,
                p.sales_estimate,
                p.source_metadata,
                p.model_url,
                p.model_shape,
                p.catalog_expires_at,
                p.is_active AS source_active,
                s.name AS seller_name,
                s.reputation AS seller_reputation,
                pl.name AS platform,
                c.name AS category,
                p.rating,
                p.reviews_count,
                p.updated_at
                FROM published_cards pc
                JOIN products p ON p.id=pc.product_id
                JOIN platforms pl ON pl.id=p.platform_id
                LEFT JOIN categories c ON c.id=p.category_id
                LEFT JOIN sellers s ON s.id=p.seller_id
                WHERE pc.product_id=%s AND pc.is_published=TRUE AND p.is_active=TRUE AND p.is_blocked=FALSE AND COALESCE(pc.sale_price,p.current_price)>0 AND (p.catalog_expires_at IS NULL OR p.catalog_expires_at > NOW())""",(product_id,))
            row=cur.fetchone()
    finally: conn.close()
    if not row: raise HTTPException(404,"Producto publicado no encontrado")
    row["slug"]=_seo_slug(row["title"])+f"-{row['product_id']}"
    base=os.getenv("PUBLIC_STORE_URL","https://central-7ykr.vercel.app").rstrip("/")
    row["canonical_url"]=base+"/producto/"+row["slug"]
    row["checkout_mode"]="CENTRAL" if str(row["platform"]).lower()=="personal" else "EXTERNAL"
    return row

@core_router.post("/affiliate/click")
def affiliate_click(payload: dict):
    """
    Resuelve la URL comercial de un producto externo.

    Amazon:
      1. affiliate_url almacenada.
      2. product_url + AMAZON_PARTNER_TAG.
      3. product_url sin atribución.

    Mercado Libre:
      1. affiliate_url almacenada.
      2. product_url sin modificar.

    Productos propios:
      No utilizan afiliación; deben pasar por Wompi.
    """
    product_id = int(payload.get("product_id") or 0)

    if product_id <= 0:
        raise HTTPException(
            status_code=400,
            detail="product_id es obligatorio",
        )

    conn = get_connection()

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    p.id,
                    p.product_url,
                    p.affiliate_url,
                    pl.name AS platform
                FROM products p
                JOIN platforms pl
                  ON pl.id = p.platform_id
                WHERE p.id = %s
                  AND p.is_active = TRUE
                """,
                (product_id,),
            )

            row = cur.fetchone()

            if not row:
                raise HTTPException(
                    status_code=404,
                    detail="Producto no encontrado",
                )

            platform = str(
                row["platform"] or ""
            ).strip().lower()

            is_own = platform == "personal"

            if is_own:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Los productos propios no "
                        "utilizan afiliación."
                    ),
                )

            product_url = str(
                row["product_url"] or ""
            ).strip()

            affiliate_url = str(
                row["affiliate_url"] or ""
            ).strip()

            target_url = ""
            tracking = False

            if platform == "amazon":

                if affiliate_url:
                    target_url = affiliate_url
                    tracking = True

                else:
                    partner_tag = str(
                        os.getenv(
                            "AMAZON_PARTNER_TAG"
                        )
                        or os.getenv(
                            "AMAZON_ASSOCIATE_TAG"
                        )
                        or ""
                    ).strip()

                    if product_url and partner_tag:
                        separator = (
                            "&"
                            if "?" in product_url
                            else "?"
                        )

                        target_url = (
                            product_url
                            + separator
                            + "tag="
                            + quote(
                                partner_tag,
                                safe="",
                            )
                        )

                        tracking = True

                    else:
                        target_url = product_url

            elif platform in {
                "mercadolibre",
                "mercado libre",
                "meli",
            }:
                if affiliate_url:
                    target_url = affiliate_url
                    tracking = True
                else:
                    target_url = product_url

            else:
                target_url = (
                    affiliate_url
                    or product_url
                )

                tracking = bool(
                    affiliate_url
                )

            if not target_url:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "El producto no tiene "
                        "una URL comercial válida."
                    ),
                )

            cur.execute(
                """
                INSERT INTO affiliate_clicks(
                    product_id,
                    platform,
                    target_url
                )
                VALUES(%s,%s,%s)
                """,
                (
                    product_id,
                    row["platform"],
                    target_url,
                ),
            )

        conn.commit()

        return {
            "product_id": product_id,
            "platform": row["platform"],
            "url": target_url,
            "tracking": tracking,
        }

    finally:
        conn.close()

@core_router.get("/public/robots.txt")
def public_robots():
    base=os.getenv("PUBLIC_STORE_URL","https://central-7ykr.vercel.app").rstrip("/")
    return Response(f"User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /api/\nSitemap: {base}/sitemap.xml\n",media_type="text/plain")

@core_router.get("/public/sitemap.xml")
def public_sitemap():
    base=os.getenv("PUBLIC_STORE_URL","https://central-7ykr.vercel.app").rstrip("/")
    conn=get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pc.product_id,pc.title FROM published_cards pc JOIN products p ON p.id=pc.product_id WHERE pc.is_published=TRUE AND p.is_active=TRUE")
            rows=cur.fetchall()
    finally: conn.close()
    urls=[base+"/",base+"/tienda"]+[base+"/producto/"+_seo_slug(r["title"])+f"-{r['product_id']}" for r in rows]
    xml='<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+''.join("<url><loc>"+u+"</loc></url>" for u in urls)+"</urlset>"
    return Response(xml,media_type="application/xml")

class ProductImport(BaseModel):
    url: str = Field(min_length=10, max_length=2000)
    category: str = Field(default="accesorios", min_length=2, max_length=100)
    title: Optional[str] = Field(default=None, max_length=500)
    price: Optional[float] = Field(default=None, gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=10)
    image_url: Optional[str] = Field(default=None, max_length=2_000_000)


@core_router.post("/products/import-url")
def import_product_from_url(payload: ProductImport, _: dict = Depends(require_admin)):
    try:
        product = import_url(
            payload.url,
            payload.category,
            title=payload.title,
            price=payload.price,
            currency=payload.currency,
            image_url=payload.image_url,
            require_image=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=502, detail="No fue posible leer la URL")

    conn = get_connection()
    try:
        product_id = upsert_product(conn, product["platform"], payload.category, product)
        score = None

        if product.get("price") is not None:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT AVG(p.current_price) AS avg_price
                    FROM products p
                    JOIN categories c ON c.id = p.category_id
                    WHERE p.is_active = TRUE
                      AND c.name = %s
                      AND p.currency = %s
                      AND p.current_price IS NOT NULL
                    """,
                    (payload.category, product.get("currency") or "USD"),
                )
                average = cur.fetchone()["avg_price"]
            score = score_product(conn, product_id, float(average or 0))

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id, p.title, pl.name AS platform, c.name AS category,
                       p.current_price, p.currency, p.image_url, p.product_url, p.affiliate_url,
                       p.updated_at, p.catalog_expires_at, p.model_url, p.model_shape
                FROM products p
                JOIN platforms pl ON pl.id = p.platform_id
                LEFT JOIN categories c ON c.id = p.category_id
                WHERE p.id = %s
                """,
                (product_id,),
            )
            saved = cur.fetchone()
        return {"product": saved, "opportunity_score": score}
    finally:
        conn.close()


def _persist_product_image_reference(value: str | None, *, platform: str, external_id: str, index: int = 0):
    """Guarda imágenes subidas como mongo:// y mantiene URL pública si el proveedor remoto falla."""
    value = str(value or "").strip()
    if not value:
        return None, None
    if value.startswith("mongo://"):
        return value, value[8:]

    is_data_uri = value.startswith("data:")
    if not is_data_uri:
        try:
            _public_url(value)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        mongo_id = store_product_image(
            value,
            platform=platform,
            external_id=external_id,
            image_index=index,
        )
        if mongo_id:
            return "mongo://" + mongo_id, mongo_id
    except Exception as exc:
        if is_data_uri:
            raise HTTPException(
                status_code=422,
                detail="No se pudo guardar la imagen subida. Comprueba que MongoDB esté conectado y vuelve a intentar.",
            ) from exc
        print(f"[media] imagen externa no cacheada ({platform}): {type(exc).__name__}")

    if is_data_uri:
        raise HTTPException(
            status_code=503,
            detail="El almacenamiento de imágenes no está disponible; la tarjeta no se guardó para evitar perder la imagen.",
        )
    return value, None


class PersonalProduct(BaseModel):
    title: str = Field(min_length=2, max_length=500)
    description: Optional[str] = Field(default=None, max_length=5000)
    price: float = Field(gt=0)
    previous_price: Optional[float] = Field(default=None, gt=0)
    currency: str = Field(default="COP", min_length=3, max_length=10)
    category: str = Field(default="otros", min_length=2, max_length=100)
    image_url: Optional[str] = Field(default=None, max_length=2_000_000)
    image_gallery: list[str] = Field(default_factory=list, max_length=5)
    product_url: Optional[str] = Field(default=None, max_length=2000)
    sku: Optional[str] = Field(default=None, max_length=150)
    stock: Optional[int] = Field(default=None, ge=0)


@core_router.post("/products/personal")
def create_personal_product(payload: PersonalProduct, _: dict = Depends(require_admin)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM platforms WHERE name='personal'")
            platform = cur.fetchone()
            if not platform:
                raise HTTPException(status_code=500, detail="Fuente personal no configurada")
            category_name = payload.category.lower().strip()
            cur.execute("SELECT id FROM categories WHERE name=%s", (category_name,))
            category = cur.fetchone()
            if not category:
                cur.execute("INSERT INTO categories(name) VALUES(%s) RETURNING id", (category_name,))
                category = cur.fetchone()
            sku = (payload.sku or "").strip() or f"PERSONAL-{int(time.time()*1000)}"
            primary_image, primary_mongo_id = _persist_product_image_reference(
                payload.image_url, platform="personal", external_id=sku, index=0
            )
            saved_gallery = []
            for image_index, image_value in enumerate(payload.image_gallery or [], start=1):
                stored_ref, _ = _persist_product_image_reference(
                    image_value, platform="personal", external_id=sku, index=image_index
                )
                if stored_ref:
                    saved_gallery.append(stored_ref)
            source_metadata = {"mongo_image_id": primary_mongo_id} if primary_mongo_id else {}
            cur.execute("""
                INSERT INTO products (
                    platform_id,category_id,external_id,sku,title,description,image_url,image_gallery,
                    product_url,current_price,previous_price,currency,stock,source_metadata,is_active,
                    catalog_expires_at,updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,NULL,NOW())
                ON CONFLICT(platform_id,external_id) DO UPDATE SET
                    category_id=EXCLUDED.category_id,sku=EXCLUDED.sku,title=EXCLUDED.title,
                    description=EXCLUDED.description,image_url=EXCLUDED.image_url,
                    image_gallery=EXCLUDED.image_gallery,product_url=EXCLUDED.product_url,
                    current_price=EXCLUDED.current_price,previous_price=EXCLUDED.previous_price,
                    currency=EXCLUDED.currency,stock=EXCLUDED.stock,source_metadata=EXCLUDED.source_metadata,
                    is_active=TRUE,catalog_expires_at=NULL,updated_at=NOW()
                RETURNING id
            """,(platform["id"],category["id"],sku,sku,payload.title.strip(),payload.description,
                 primary_image,Json(saved_gallery or ([primary_image] if primary_image else [])),payload.product_url,
                 payload.price,payload.previous_price,payload.currency.upper(),payload.stock,Json(source_metadata)))
            product_id=cur.fetchone()["id"]
            cur.execute("INSERT INTO price_history(product_id,price) VALUES(%s,%s)",(product_id,payload.price))
            cur.execute("""SELECT p.id,p.title,pl.name AS platform,c.name AS category,p.current_price,
                p.previous_price,p.currency,p.image_url,p.image_gallery,p.product_url,p.affiliate_url,p.stock,p.sku,
                p.description,p.source_metadata,p.updated_at
                FROM products p JOIN platforms pl ON pl.id=p.platform_id LEFT JOIN categories c ON c.id=p.category_id
                WHERE p.id=%s""",(product_id,))
            saved=cur.fetchone()
        conn.commit()
        return {"product":saved}
    except HTTPException:
        conn.rollback(); raise
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()


class ProductImages(BaseModel):
    images: list[str] = Field(default_factory=list, max_length=5)


@core_router.patch("/products/{product_id}/images")
def update_product_images(product_id: int, payload: ProductImages, _: dict = Depends(require_admin)):
    clean=[str(x).strip() for x in payload.images if str(x).strip()]
    if len(clean)>5: raise HTTPException(status_code=400, detail="Máximo 5 imágenes por producto")
    if any(len(x)>2_000_000 for x in clean): raise HTTPException(status_code=400, detail="Cada imagen es demasiado grande")
    conn=get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pl.name AS platform,p.external_id FROM products p JOIN platforms pl ON pl.id=p.platform_id WHERE p.id=%s",(product_id,))
            current=cur.fetchone()
            if not current:
                raise HTTPException(status_code=404,detail="Producto no encontrado")
            saved=[]
            first_mongo_id=None
            for index,image in enumerate(clean):
                stored_ref,mongo_id=_persist_product_image_reference(
                    image,platform=str(current["platform"]),external_id=str(current["external_id"]),index=index
                )
                if stored_ref:
                    saved.append(stored_ref)
                    if index==0:
                        first_mongo_id=mongo_id
            metadata={"mongo_image_id":first_mongo_id} if first_mongo_id else {}
            cur.execute("""UPDATE products SET image_gallery=%s,image_url=%s,
                           source_metadata=(COALESCE(source_metadata,'{}'::jsonb)-'mongo_image_id') || %s,
                           updated_at=NOW()
                           WHERE id=%s RETURNING id,image_url,image_gallery,source_metadata,updated_at""",
                        (Json(saved),saved[0] if saved else None,Json(metadata),product_id))
            result=cur.fetchone()
            cur.execute("UPDATE published_cards SET image_url=%s,updated_at=NOW() WHERE product_id=%s",
                        (saved[0] if saved else None,product_id))
        conn.commit()
        return result
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()



class ModelUpdate(BaseModel):
    model_url: Optional[str] = None
    model_shape: Optional[str] = None


@core_router.patch("/products/{product_id}/model")
def set_product_model(product_id: int, update: ModelUpdate, _: dict = Depends(require_admin)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE products
                SET model_url = COALESCE(%s, model_url),
                    model_shape = COALESCE(%s, model_shape)
                WHERE id = %s
                RETURNING id, model_url, model_shape
                """,
                (update.model_url, update.model_shape, product_id),
            )
            result = cur.fetchone()
            if not result:
                raise HTTPException(status_code=404, detail="Producto no encontrado")
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@core_router.get("/catalog/active")
def active_catalog(_: dict = Depends(require_admin)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.id, p.title, pl.name AS platform, c.name AS category,
                       p.current_price, p.currency, p.image_url, p.image_gallery,
                       p.product_url, p.affiliate_url, p.updated_at, p.catalog_expires_at,
                       p.stock, p.sku, p.rating, p.reviews_count,
                       COALESCE(pc.is_published, FALSE) AS is_published,
                       pc.id AS publication_id
                FROM products p
                JOIN platforms pl ON pl.id = p.platform_id
                LEFT JOIN categories c ON c.id = p.category_id
                LEFT JOIN published_cards pc ON pc.product_id = p.id
                WHERE p.is_active = TRUE AND p.is_blocked = FALSE AND p.current_price > 0 AND (p.catalog_expires_at IS NULL OR p.catalog_expires_at > NOW())
                ORDER BY p.updated_at DESC
                LIMIT 200            """)
            return cur.fetchall()
    finally:
        conn.close()


class ProductUpdate(BaseModel):
    title: str = Field(min_length=2, max_length=500)
    description: Optional[str] = Field(default=None, max_length=5000)
    price: float = Field(gt=0)
    previous_price: Optional[float] = Field(default=None, gt=0)
    currency: str = Field(default="COP", min_length=3, max_length=10)
    category: str = Field(default="otros", min_length=2, max_length=100)
    image_url: str = Field(min_length=8, max_length=2_000_000)
    image_gallery: list[str] = Field(default_factory=list, max_length=5)
    product_url: Optional[str] = Field(default=None, max_length=2000)


@core_router.patch("/products/{product_id}")
def update_personal_product(product_id: int, payload: ProductUpdate, _: dict = Depends(require_admin)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM platforms WHERE name='personal'")
            platform = cur.fetchone()
            if not platform:
                raise HTTPException(status_code=500, detail="Fuente personal no configurada")
            category_name = payload.category.lower().strip()
            cur.execute("SELECT id FROM categories WHERE name=%s", (category_name,))
            category = cur.fetchone()
            if not category:
                cur.execute("INSERT INTO categories(name) VALUES(%s) RETURNING id", (category_name,))
                category = cur.fetchone()
            primary_image, primary_mongo_id = _persist_product_image_reference(
                payload.image_url, platform="personal", external_id=str(product_id), index=0
            )
            saved_gallery = []
            for image_index, image_value in enumerate(payload.image_gallery or [], start=1):
                stored_ref, _ = _persist_product_image_reference(
                    image_value, platform="personal", external_id=str(product_id), index=image_index
                )
                if stored_ref:
                    saved_gallery.append(stored_ref)
            image_metadata = {"mongo_image_id": primary_mongo_id} if primary_mongo_id else {}
            cur.execute("""UPDATE products SET category_id=%s,title=%s,description=%s,current_price=%s,
                previous_price=%s,currency=%s,image_url=%s,image_gallery=%s,product_url=%s,
                source_metadata=(COALESCE(source_metadata,'{}'::jsonb)-'mongo_image_id') || %s,updated_at=NOW()
                WHERE id=%s AND platform_id=%s AND is_active=TRUE
                RETURNING id,title,description,current_price AS price,previous_price,currency,image_url,image_gallery,product_url""",
                (category["id"],payload.title.strip(),payload.description,payload.price,payload.previous_price,
                 payload.currency.upper(),primary_image,Json(saved_gallery or ([primary_image] if primary_image else [])),
                 payload.product_url,Json(image_metadata),product_id,platform["id"]))
            saved = cur.fetchone()
            if not saved:
                raise HTTPException(status_code=404, detail="Producto propio no encontrado")
            cur.execute("""UPDATE published_cards
                SET title=%s,subtitle=%s,price_display=%s,image_url=%s,product_url=%s,
                    sale_price=%s,updated_at=NOW()
                WHERE product_id=%s""",
                (payload.title.strip(),payload.description,
                 f"{payload.price:,.0f} {payload.currency.upper()}",primary_image,
                 payload.product_url,payload.price,product_id))
        conn.commit()
        return {"product": saved}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


class ProductStatus(BaseModel):
    active: bool


@core_router.patch("/products/{product_id}/status")
def update_product_status(product_id: int, payload: ProductStatus, _: dict = Depends(require_admin)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE products
                SET is_active=%s,
                    is_blocked=CASE WHEN %s THEN FALSE ELSE TRUE END,
                    updated_at=NOW()
                WHERE id=%s
                RETURNING id, is_active, is_blocked, updated_at
            """, (payload.active, payload.active, product_id))
            result = cur.fetchone()
            if not result:
                raise HTTPException(status_code=404, detail="Producto no encontrado")
            if not payload.active:
                cur.execute("UPDATE published_cards SET is_published=FALSE, updated_at=NOW() WHERE product_id=%s", (product_id,))
        conn.commit()
        return result
    except HTTPException:
        conn.rollback()
        raise
    finally:
        conn.close()


@core_router.delete("/products/{product_id}")
def remove_product(product_id: int, _: dict = Depends(require_admin)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE products
                SET is_active=FALSE, is_blocked=TRUE, updated_at=NOW()
                WHERE id=%s
                RETURNING id, title
            """, (product_id,))
            result = cur.fetchone()
            if not result:
                raise HTTPException(status_code=404, detail="Producto no encontrado")
            cur.execute("UPDATE published_cards SET is_published=FALSE, updated_at=NOW() WHERE product_id=%s", (product_id,))
        conn.commit()
        return {"status":"removed","product":result}
    except HTTPException:
        conn.rollback()
        raise
    finally:
        conn.close()


@core_router.get("/comparison")
def price_comparison(limit: int = Query(100, ge=1, le=500), _: dict = Depends(require_admin)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM product_price_comparison LIMIT %s", (limit,))
            return cur.fetchall()
    finally:
        conn.close()


@core_router.get("/opportunities/top")
def top_opportunities(limit: int = Query(20, ge=1, le=100), _: dict = Depends(require_admin)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.id, p.title, pl.name AS platform, c.name AS category,
                       p.current_price, p.currency, p.rating, p.reviews_count,
                       p.sales_estimate, s.price_score, s.demand_score,
                       s.trend_score, s.opportunity_score, p.product_url,
                       CASE WHEN COALESCE(p.source_metadata->>'mongo_image_id','') <> ''
                            THEN 'mongo://' || (p.source_metadata->>'mongo_image_id')
                            ELSE p.image_url END AS image_url,
                       p.updated_at, p.catalog_expires_at, p.model_url, p.model_shape,
                       p.source_metadata
                FROM product_scores s
                JOIN products p ON p.id = s.product_id
                JOIN platforms pl ON pl.id = p.platform_id
                LEFT JOIN categories c ON c.id = p.category_id
                WHERE p.is_active = TRUE
                ORDER BY s.opportunity_score DESC
                LIMIT %s
            """, (limit,))
            return cur.fetchall()
    finally:
        conn.close()


app.include_router(core_router, prefix="/api/v1")
