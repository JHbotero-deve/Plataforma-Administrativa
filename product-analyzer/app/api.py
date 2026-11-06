"""
Central Product Analyzer REST API.
"""

import asyncio
import logging
import os
import secrets
import time
import requests
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from psycopg2.extras import Json

from admin_auth import (
    authenticate_admin,
    clear_admin_cookie,
    require_admin,
    set_admin_cookie,
)
from analysis import score_product
from db import get_connection, upsert_product
from notifications import send_telegram_alert
from monetization import router as monetization_router
from publications import ensure_draft_storage, router as publication_router
from storefront_sections import ensure_storefront_section_storage, router as storefront_sections_router
from store_orders import ensure_commerce_storage, router as store_orders_router
from store_notifications import process_pending_notifications
from tiktok_api import router as tiktok_creator_router
from url_import import import_url
from product_sources import enrich_product
from pricing import publish_catalog_product
from wompi import router as wompi_router
from notifications import _config as _config_telegram
from telegram_alert import run_bot
from main import run_pipeline

API_VERSION = "1.3.0"
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Central Product Analyzer API",
    description="Ingesta, análisis, comparación y monetización de productos reales.",
    version=API_VERSION,
)


notification_worker_task = None
telegram_bot_task = None
catalog_worker_task = None


@app.on_event("startup")
async def ensure_admin_draft_storage():
    global notification_worker_task, telegram_bot_task, catalog_worker_task
    ensure_draft_storage()
    ensure_storefront_section_storage()
    ensure_commerce_storage()
    notification_worker_task = asyncio.create_task(notification_delivery_loop())

    token, _ = _config_telegram()
    if token and os.getenv("ENABLE_TELEGRAM_BOT", "true").lower() in ("true", "1", "yes"):
        telegram_bot_task = asyncio.create_task(telegram_bot_delivery_loop())

    if os.getenv("ENABLE_BACKGROUND_WORKER", "false").lower() in ("true", "1", "yes"):
        catalog_worker_task = asyncio.create_task(catalog_pipeline_delivery_loop())


@app.on_event("shutdown")
async def stop_notification_delivery():
    for task in (notification_worker_task, telegram_bot_task, catalog_worker_task):
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


async def telegram_bot_delivery_loop():
    try:
        await asyncio.to_thread(run_bot)
    except Exception:
        logger.exception("Telegram bot background task failed")


async def catalog_pipeline_delivery_loop():
    while True:
        try:
            await asyncio.to_thread(run_pipeline)
        except Exception:
            logger.exception("Catalog background pipeline cycle failed")
        await asyncio.sleep(7200)


async def notification_delivery_loop():
    while True:
        try:
            result = await asyncio.to_thread(process_pending_notifications)
            if result["failed"]:
                logger.warning(
                    "Store notifications retried: %s failed", result["failed"]
                )
        except Exception:
            logger.exception("Store notification delivery cycle failed")
        await asyncio.sleep(30)


app.include_router(monetization_router, prefix="/api/v1")
app.include_router(wompi_router, prefix="/api/v1")
app.include_router(tiktok_creator_router, prefix="/api/v1")
app.include_router(publication_router, prefix="/api/v1")
app.include_router(storefront_sections_router, prefix="/api/v1")
app.include_router(store_orders_router, prefix="/api/v1")


@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


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
        "https://central-five-pied.vercel.app",
        "https://central-jorgedevop27-9650.vercel.app",
        "https://central-git-main-jorgedevop27-9650.vercel.app",
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
)

core_router = APIRouter(tags=["core"])


class AdminLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


@core_router.post("/auth/login")
def admin_login(payload: AdminLoginRequest, response: Response):
    email = authenticate_admin(payload.email, payload.password)
    set_admin_cookie(response, email)
    return {"authenticated": True}


@core_router.get("/auth/session")
def admin_session(email: str = Depends(require_admin)):
    return {"authenticated": True, "email": email}


@core_router.post("/auth/logout")
def admin_logout(response: Response):
    clear_admin_cookie(response)
    return {"authenticated": False}


@core_router.get("/health")
@core_router.get("/healthz")
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
    price = product.get("price")
    currency = product.get("currency") or "COP"
    price_display = f"{price:,.2f} {currency}" if price is not None else "Consultar"
    image_url = product.get("image_url") or None
    product_url = product.get("product_url") or None
    score = product.get("_score")
    with conn.cursor() as cur:
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
            product_id, product["title"], f"{platform_label} · producto original",
            price_display, image_url, product_url,
            price, price, score or 0, f"Oferta {platform_label}", "#b6f23a"
        ))


@core_router.post("/store/sync", dependencies=[Depends(require_admin)])
def sync_store_catalog():
    raise HTTPException(
        status_code=410,
        detail="La ingesta automática está desactivada. Usa la importación por URL de Amazon o AliExpress.",
    )


@core_router.post("/amazon/sync", dependencies=[Depends(require_admin)])
def sync_amazon_store(limit: int = Query(20, ge=1, le=20)):
    raise HTTPException(
        status_code=410,
        detail="La búsqueda automática de Amazon está desactivada. Usa la importación por URL.",
    )


@core_router.get("/telegram/status")
def telegram_status():
    token, chat_id = _config_telegram()
    if not token:
        return {
            "configured": False,
            "connected": False,
            "chat_configured": bool(chat_id),
            "username": None,
        }
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=5)
        payload = r.json()
        if r.ok and isinstance(payload, dict) and payload.get("ok") is True:
            data = payload.get("result") or {}
            if not isinstance(data, dict):
                data = {}
            return {
                "configured": bool(chat_id),
                "connected": True,
                "chat_configured": bool(chat_id),
                "username": data.get("username"),
            }
        return {
            "configured": bool(chat_id),
            "connected": False,
            "chat_configured": bool(chat_id),
            "username": None,
        }
    except (requests.RequestException, ValueError):
        return {
            "configured": bool(chat_id),
            "connected": False,
            "chat_configured": bool(chat_id),
            "username": None,
        }


@core_router.post("/admin/inject", dependencies=[Depends(require_admin)])
def admin_inject():
    raise HTTPException(
        status_code=410,
        detail="La búsqueda por APIs de marketplaces fue retirada. Usa la importación y el enriquecimiento URL-first.",
    )

@core_router.get("/pipeline/metrics")
def pipeline_metrics():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT pl.name AS platform, COUNT(p.id) AS product_count,
                       MAX(p.updated_at) AS last_update
                FROM platforms pl
                LEFT JOIN products p
                  ON p.platform_id = pl.id AND p.is_active = TRUE
                GROUP BY pl.name
                ORDER BY pl.name
            """)
            rows = cur.fetchall()
            return {
                "latest": {
                    "status": "success",
                    "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "amazon_products": sum(r["product_count"] for r in rows if r["platform"] == "amazon"),
                    "tiktok_products": 0,
                    "telegram_prepared": sum(r["product_count"] for r in rows),
                    "error_count": 0,
                },
                "platforms": rows,
            }
    finally:
        conn.close()


@core_router.get(
    "/intelligence/overview", dependencies=[Depends(require_admin)]
)
def intelligence_overview():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    COUNT(*) AS active_products,
                    SUM(CASE WHEN COALESCE(sales_estimate, 0) > 0 THEN 1 ELSE 0 END) AS products_with_demand,
                    SUM(CASE WHEN COALESCE(s.opportunity_score, 0) >= 70 THEN 1 ELSE 0 END) AS high_opportunity,
                    AVG(COALESCE(s.opportunity_score, 0)) AS avg_opportunity,
                    SUM(CASE WHEN catalog_expires_at <= NOW() + INTERVAL '24 hours' THEN 1 ELSE 0 END) AS expiring_24h
                FROM products p
                LEFT JOIN product_scores s ON s.product_id = p.id
                WHERE p.is_active = TRUE
            """)
            cat_row = cur.fetchone() or {}

            cur.execute("""
                SELECT
                    COUNT(*) AS orders,
                    SUM(CASE WHEN payment_status = 'PAID' THEN 1 ELSE 0 END) AS paid_orders,
                    SUM(CASE WHEN payment_status = 'PAID' THEN total_amount ELSE 0 END) AS revenue,
                    SUM(CASE WHEN payment_status = 'PAID' THEN estimated_profit ELSE 0 END) AS estimated_profit
                FROM store_orders
            """)
            com_row = cur.fetchone() or {}

            cur.execute("""
                SELECT p.id, p.title, pl.name AS platform, p.current_price, p.currency,
                       COALESCE(s.opportunity_score, 0) AS opportunity_score,
                       COALESCE(p.sales_estimate, 0) AS clicks,
                (SELECT COUNT(*) FROM store_orders so
                  WHERE so.product_id=p.id AND so.payment_status='PAID') AS paid_orders
                FROM products p
                JOIN platforms pl ON pl.id = p.platform_id
                LEFT JOIN product_scores s ON s.product_id = p.id
                WHERE p.is_active = TRUE
                ORDER BY opportunity_score DESC, p.updated_at DESC
                LIMIT 5
            """)
            top_products = cur.fetchall()

            cur.execute("""
                SELECT pl.name AS platform,
                       COUNT(p.id) AS products,
                       AVG(COALESCE(s.opportunity_score, 0)) AS avg_score,
                       SUM(CASE WHEN COALESCE(p.sales_estimate, 0) > 0 THEN 1 ELSE 0 END) AS demand
                FROM platforms pl
                LEFT JOIN products p ON p.platform_id = pl.id AND p.is_active = TRUE
                LEFT JOIN product_scores s ON s.product_id = p.id
                GROUP BY pl.name
                ORDER BY products DESC
            """)
            sources = cur.fetchall()

            return {
                "catalog": {
                    "active_products": int(cat_row.get("active_products") or 0),
                    "products_with_demand": int(cat_row.get("products_with_demand") or 0),
                    "high_opportunity": int(cat_row.get("high_opportunity") or 0),
                    "avg_opportunity": float(cat_row.get("avg_opportunity") or 0),
                    "expiring_24h": int(cat_row.get("expiring_24h") or 0),
                },
                "commerce": {
                    "orders": int(com_row.get("orders") or 0),
                    "paid_orders": int(com_row.get("paid_orders") or 0),
                    "revenue": float(com_row.get("revenue") or 0),
                    "estimated_profit": float(com_row.get("estimated_profit") or 0),
                },
                "top_products": top_products,
                "sources": sources,
            }
    finally:
        conn.close()


@core_router.get("/pipeline/summary")
def pipeline_summary():
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


@core_router.get("/products")
def list_products(
    category: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            query = """
                SELECT p.id, p.title, pl.name AS platform, c.name AS category,
                       p.current_price, p.currency, p.rating, p.reviews_count,
                       p.sales_estimate, p.image_url, p.image_gallery, p.product_url, p.affiliate_url, p.updated_at, p.source_metadata, p.description, p.previous_price, p.stock, p.sku,
                       p.catalog_expires_at, p.model_url, p.model_shape,
                       seller.name AS seller_name, p.source_metadata->>'seller_url' AS seller_url,
                       s.opportunity_score
                FROM products p
                JOIN platforms pl ON pl.id = p.platform_id
                LEFT JOIN categories c ON c.id = p.category_id
                LEFT JOIN sellers seller ON seller.id = p.seller_id
                LEFT JOIN product_scores s ON s.product_id = p.id
                WHERE p.is_active = TRUE
            """
            params = []
            if category:
                query += " AND c.name = %s"
                params.append(category)
            if platform:
                query += " AND pl.name = %s"
                params.append(platform)
            query += " ORDER BY COALESCE(s.opportunity_score, 0) DESC, p.updated_at DESC LIMIT %s OFFSET %s"
            params.extend((limit, offset))
            cur.execute(query, params)
            return cur.fetchall()
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
            cur.execute("""SELECT pc.id AS publication_id,pc.product_id,pc.title,pc.subtitle,pc.price_display,
                pc.sale_price,pc.opportunity_score,pc.footer,p.description,pc.sale_price AS current_price,p.previous_price,p.previous_price AS original_price,CASE WHEN p.previous_price>0 AND pc.sale_price>0 THEN ROUND((p.previous_price-pc.sale_price)/p.previous_price*100,2) ELSE NULL END AS discount_pct,p.currency,
                COALESCE(NULLIF(pc.image_url, ''), NULLIF(p.image_url, '')) AS image_url,p.image_gallery,
                COALESCE(NULLIF(pc.product_url, ''), NULLIF(p.product_url, '')) AS product_url,
                p.affiliate_url,p.sku,p.external_id,p.stock,
                pl.name AS platform,c.name AS category,p.rating,p.reviews_count,p.updated_at,
                seller.name AS seller_name,p.source_metadata->>'seller_url' AS seller_url
                FROM published_cards pc JOIN products p ON p.id=pc.product_id JOIN platforms pl ON pl.id=p.platform_id
                LEFT JOIN categories c ON c.id=p.category_id
                LEFT JOIN sellers seller ON seller.id=p.seller_id
                WHERE pc.product_id=%s AND pc.is_published=TRUE AND p.is_active=TRUE""",(product_id,))
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
    product_id = int(payload.get("product_id") or 0)
    if product_id <= 0:
        raise HTTPException(400, "product_id es obligatorio")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT p.product_url,p.affiliate_url,pl.name AS platform
                FROM products p
                JOIN platforms pl ON pl.id=p.platform_id
                JOIN published_cards pc ON pc.product_id=p.id AND pc.is_published=TRUE
                WHERE p.id=%s AND p.is_active=TRUE""", (product_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Producto no encontrado")
            if str(row["platform"]).lower() != "amazon":
                raise HTTPException(400, "Este producto no pertenece a Amazon")

            target_url = (row["affiliate_url"] or "").strip()
            if not target_url:
                product_url = (row["product_url"] or "").strip()
                partner_tag = (os.getenv("AMAZON_PARTNER_TAG") or os.getenv("AMAZON_ASSOCIATE_TAG") or "").strip()
                if product_url and partner_tag:
                    target_url = product_url + ("&" if "?" in product_url else "?") + "tag=" + quote(partner_tag, safe="")
                else:
                    target_url = product_url

            if not target_url:
                raise HTTPException(400, "El producto no tiene una URL de Amazon válida")

            cur.execute(
                "INSERT INTO affiliate_clicks(product_id,platform,target_url) VALUES(%s,%s,%s)",
                (product_id, row["platform"], target_url),
            )
        conn.commit()
        return {
            "product_id": product_id,
            "url": target_url,
            "tracking": target_url != row["product_url"],
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
    category: Optional[str] = Field(default=None, min_length=2, max_length=100)
    external_id: Optional[str] = Field(default=None, max_length=150)
    title: Optional[str] = Field(default=None, max_length=500)
    description: Optional[str] = Field(default=None, max_length=5000)
    price: Optional[float] = Field(default=None, gt=0)
    previous_price: Optional[float] = Field(default=None, gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=10)
    image_url: Optional[str] = Field(default=None, max_length=2000)
    gallery_urls: list[str] = Field(default_factory=list, max_length=10)
    rating: Optional[float] = Field(default=None, ge=0, le=5)
    reviews_count: int = Field(default=0, ge=0)
    sales_estimate: Optional[int] = Field(default=None, ge=0)


class ProductImportBatch(BaseModel):
    products: list[ProductImport]


@core_router.post(
    "/products/import-url", dependencies=[Depends(require_admin)]
)
def import_product_from_url(payload: ProductImport):
    category = (payload.category or "").strip().lower() or None
    if category and category not in {"ropa", "calzado", "accesorios", "tecnologia", "hogar", "moda", "deportes", "belleza", "bienestar", "juguetes", "oficina"}:
        raise HTTPException(status_code=400, detail="Categoría no permitida.")
    if payload.previous_price is not None and payload.price is not None and payload.previous_price < payload.price:
        raise HTTPException(status_code=400, detail="El precio anterior debe ser igual o superior al precio actual.")
    for image in payload.gallery_urls:
        parsed_image = urlsplit(image)
        if parsed_image.scheme not in ("http", "https") or not parsed_image.hostname:
            raise HTTPException(status_code=400, detail="Todas las imágenes deben usar URLs HTTP o HTTPS válidas.")
    try:
        product = import_url(
            payload.url,
            category,
            title=payload.title,
            price=payload.price,
            currency=payload.currency,
            image_url=payload.image_url,
            external_id=payload.external_id,
        )
        product.update({
            "description": payload.description,
            "previous_price": payload.previous_price,
            "rating": payload.rating,
            "reviews_count": payload.reviews_count,
            "sales_estimate": payload.sales_estimate,
            "gallery_urls": list(dict.fromkeys((product.get("gallery_urls") or []) + payload.gallery_urls))[:10],
        })
        if not product.get("image_url") and product.get("gallery_urls"):
            product["image_url"] = product["gallery_urls"][0]
        product = enrich_product(product)
        category = category or product.get("category")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=502, detail="No fue posible procesar la URL")

    conn = get_connection()
    try:
        product_id = upsert_product(conn, product["platform"], category, product)
        score = None
        if product.get("price") is not None and category and product.get("image_url") and product.get("title") and not product["title"].startswith("Producto "):
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT AVG(p.current_price) AS avg_price
                    FROM products p
                    JOIN categories c ON c.id = p.category_id
                    WHERE p.is_active = TRUE AND c.name = %s
                      AND p.currency = %s AND p.current_price IS NOT NULL
                    """,
                    (category, product.get("currency") or "USD"),
                )
                average = cur.fetchone()["avg_price"]
            score = score_product(conn, product_id, float(average or 0))
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id, p.title, p.description, pl.name AS platform, c.name AS category,
                       p.current_price, p.previous_price, p.currency, p.image_url, p.image_gallery,
                       p.rating, p.reviews_count, p.sales_estimate, p.product_url, p.affiliate_url,
                       p.source_metadata, p.updated_at, p.catalog_expires_at, p.model_url, p.model_shape
                FROM products p
                JOIN platforms pl ON pl.id = p.platform_id
                LEFT JOIN categories c ON c.id = p.category_id
                WHERE p.id = %s
                """,
                (product_id,),
            )
            saved = cur.fetchone()
        customer_offer = publish_catalog_product(conn, product_id, dict(saved), score)
        return {"product": saved, "opportunity_score": score, "customer_offer": customer_offer}
    finally:
        conn.close()


@core_router.post("/products/import-cli")
def import_product_from_cli(
    payload: ProductImport,
    x_central_ingest_key: str = Header(default="", alias="X-Central-Ingest-Key"),
):
    """Endpoint autenticado para que los CLI importen datos autorizados y Central avise por Telegram."""
    expected_key = (os.getenv("CENTRAL_INGEST_API_KEY") or "").strip()
    if not expected_key:
        raise HTTPException(status_code=503, detail="La importación CLI no está configurada.")
    if not x_central_ingest_key or not secrets.compare_digest(x_central_ingest_key, expected_key):
        raise HTTPException(status_code=401, detail="Credencial de ingesta inválida.")

    result = import_product_from_url(payload)
    product = result["product"]
    score = result.get("opportunity_score")
    notified = send_telegram_alert({
        "title": product.get("title"),
        "price": product.get("current_price"),
        "url": product.get("affiliate_url") or product.get("product_url"),
        "score": score if score is not None else "pendiente",
        "platform": product.get("platform"),
        "category": product.get("category"),
        "affiliate_link_generated": bool(product.get("affiliate_url")),
    })
    return {
        "product": product,
        "opportunity_score": score,
        "telegram_notified": bool(notified),
        "affiliate_link_generated": bool(product.get("affiliate_url")),
    }


@core_router.post("/products/import-cli/batch")
def import_products_from_cli_batch(
    payload: ProductImportBatch,
    x_central_ingest_key: str = Header(default="", alias="X-Central-Ingest-Key"),
):
    expected_key = (os.getenv("CENTRAL_INGEST_API_KEY") or "").strip()
    if not expected_key:
        raise HTTPException(status_code=503, detail="La importación CLI no está configurada.")
    if not x_central_ingest_key or not secrets.compare_digest(x_central_ingest_key, expected_key):
        raise HTTPException(status_code=401, detail="Credencial de ingesta inválida.")
    if not payload.products or len(payload.products) > 100:
        raise HTTPException(status_code=400, detail="El lote debe contener entre 1 y 100 productos.")
    items = []
    for index, item in enumerate(payload.products):
        try:
            result = import_product_from_url(item)
            product = result["product"]
            score = result.get("opportunity_score")
            notified = send_telegram_alert({
                "title": product.get("title"),
                "price": product.get("current_price"),
                "url": product.get("affiliate_url") or product.get("product_url"),
                "score": score if score is not None else "pendiente",
                "platform": product.get("platform"),
                "category": product.get("category"),
                "affiliate_link_generated": bool(product.get("affiliate_url")),
            })
            items.append({
                "index": index,
                "ok": True,
                "product": product,
                "opportunity_score": score,
                "telegram_notified": bool(notified),
            })
        except HTTPException as exc:
            items.append({"index": index, "ok": False, "status_code": exc.status_code, "error": exc.detail})
        except Exception as exc:
            logger.exception("CLI batch item %s failed", index)
            items.append({"index": index, "ok": False, "status_code": 500, "error": "No fue posible procesar este producto."})
    imported = sum(1 for item in items if item["ok"])
    return {
        "total": len(items),
        "imported": imported,
        "failed": len(items) - imported,
        "telegram_notified": sum(1 for item in items if item.get("telegram_notified")),
        "items": items,
    }


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
def create_personal_product(
    payload: PersonalProduct,
    x_central_ingest_key: str = Header(default="", alias="X-Central-Ingest-Key"),
):
    expected_key = (os.getenv("CENTRAL_INGEST_API_KEY") or "").strip()
    if not expected_key:
        raise HTTPException(status_code=503, detail="La carga personal no está configurada.")
    if not x_central_ingest_key or not secrets.compare_digest(x_central_ingest_key, expected_key):
        raise HTTPException(status_code=401, detail="Credencial de ingesta inválida.")
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
            cur.execute("""
                INSERT INTO products (
                    platform_id,category_id,external_id,sku,title,description,image_url,image_gallery,
                    product_url,current_price,previous_price,currency,stock,is_active,
                    catalog_expires_at,updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,NULL,NOW())
                ON CONFLICT(platform_id,external_id) DO UPDATE SET
                    category_id=EXCLUDED.category_id,sku=EXCLUDED.sku,title=EXCLUDED.title,
                    description=EXCLUDED.description,image_url=EXCLUDED.image_url,
                    product_url=EXCLUDED.product_url,current_price=EXCLUDED.current_price,
                    previous_price=EXCLUDED.previous_price,currency=EXCLUDED.currency,
                    stock=EXCLUDED.stock,is_active=TRUE,catalog_expires_at=NULL,updated_at=NOW()
                RETURNING id
            """,(platform["id"],category["id"],sku,sku,payload.title.strip(),payload.description,
                 payload.image_url,Json(payload.image_gallery or []),payload.product_url,payload.price,payload.previous_price,
                 payload.currency.upper(),payload.stock))
            product_id=cur.fetchone()["id"]
            cur.execute("INSERT INTO price_history(product_id,price) VALUES(%s,%s)",(product_id,payload.price))
            cur.execute("""SELECT p.id,p.title,pl.name AS platform,c.name AS category,p.current_price,
                p.previous_price,p.currency,p.image_url,p.image_gallery,p.product_url,p.stock,p.sku,p.description,p.updated_at
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


@core_router.patch(
    "/products/{product_id}/images", dependencies=[Depends(require_admin)]
)
def update_product_images(product_id: int, payload: ProductImages):
    clean=[str(x).strip() for x in payload.images if str(x).strip()]
    if len(clean)>5: raise HTTPException(status_code=400, detail="Máximo 5 imágenes por producto")
    if any(len(x)>2_000_000 for x in clean): raise HTTPException(status_code=400, detail="Cada imagen es demasiado grande")
    conn=get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""UPDATE products SET image_gallery=%s,image_url=%s,updated_at=NOW()
                           WHERE id=%s RETURNING id,image_url,image_gallery,updated_at""",(Json(clean),clean[0] if clean else None,product_id))
            result=cur.fetchone()
            if not result: raise HTTPException(status_code=404,detail="Producto no encontrado")
        conn.commit(); return result
    except HTTPException:
        conn.rollback(); raise
    finally: conn.close()


class ModelUpdate(BaseModel):
    model_url: Optional[str] = None
    model_shape: Optional[str] = None


@core_router.patch(
    "/products/{product_id}/model", dependencies=[Depends(require_admin)]
)
def set_product_model(product_id: int, update: ModelUpdate):
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


@core_router.get("/products/{product_id}")
def get_product(product_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.*, pl.name AS platform, c.name AS category,
                       s.opportunity_score
                FROM products p
                JOIN platforms pl ON pl.id = p.platform_id
                LEFT JOIN categories c ON c.id = p.category_id
                LEFT JOIN product_scores s ON s.product_id = p.id
                WHERE p.id = %s
                """,
                (product_id,),
            )
            product = cur.fetchone()
            if not product:
                raise HTTPException(status_code=404, detail="Producto no encontrado")
            cur.execute(
                """
                SELECT price, recorded_at
                FROM price_history
                WHERE product_id = %s
                ORDER BY recorded_at ASC
                """,
                (product_id,),
            )
            history = cur.fetchall()
        return {"product": product, "price_history": history}
    finally:
        conn.close()


@core_router.get("/comparison")
def price_comparison(limit: int = Query(100, ge=1, le=500)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM product_price_comparison LIMIT %s", (limit,))
            return cur.fetchall()
    finally:
        conn.close()


@core_router.get("/opportunities/top")
def top_opportunities(limit: int = Query(20, ge=1, le=100)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id, p.title, pl.name AS platform, c.name AS category,
                       p.current_price, p.currency, p.rating, p.reviews_count,
                       p.sales_estimate, s.price_score, s.demand_score,
                       s.trend_score, s.opportunity_score, p.product_url,
                       p.image_url, p.updated_at, p.catalog_expires_at,
                       p.model_url, p.model_shape, p.source_metadata
                FROM product_scores s
                JOIN products p ON p.id = s.product_id
                JOIN platforms pl ON pl.id = p.platform_id
                LEFT JOIN categories c ON c.id = p.category_id
                WHERE p.is_active = TRUE
                ORDER BY s.opportunity_score DESC
                LIMIT %s
                """,
                (limit,),
            )
            return cur.fetchall()
    finally:
        conn.close()


app.include_router(core_router, prefix="/api/v1")
