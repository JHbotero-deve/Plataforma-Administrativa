"""
Central Product Analyzer REST API.
"""

import os
import time
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from psycopg2.extras import Json

from analysis import score_product
from db import get_connection, upsert_product
from monetization import router as monetization_router
from publications import router as publication_router
from store_orders import router as store_orders_router
from tiktok_api import router as tiktok_creator_router
from url_import import import_url
from wompi import router as wompi_router
from meli_oauth import router as meli_oauth_router, notification_router as meli_notification_router

API_VERSION = "1.3.0"

app = FastAPI(
    title="Central Product Analyzer API",
    description="Ingesta, análisis, comparación y monetización de productos reales.",
    version=API_VERSION,
)

app.include_router(monetization_router, prefix="/api/v1")
app.include_router(wompi_router, prefix="/api/v1")
app.include_router(meli_oauth_router, prefix="/api/v1")
app.include_router(meli_notification_router, prefix="/api/v1")
app.include_router(tiktok_creator_router, prefix="/api/v1")
app.include_router(publication_router, prefix="/api/v1")
app.include_router(store_orders_router, prefix="/api/v1")


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
                WHERE p.is_active = TRUE
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
                pc.sale_price,pc.opportunity_score,pc.footer,p.description,p.current_price,p.previous_price,p.currency,
                COALESCE(NULLIF(pc.image_url, ''), NULLIF(p.image_url, '')) AS image_url,p.image_gallery,
                COALESCE(NULLIF(pc.product_url, ''), NULLIF(p.product_url, '')) AS product_url,
                p.affiliate_url,p.sku,p.external_id,p.stock,
                pl.name AS platform,c.name AS category,p.rating,p.reviews_count,p.updated_at
                FROM published_cards pc JOIN products p ON p.id=pc.product_id JOIN platforms pl ON pl.id=p.platform_id
                LEFT JOIN categories c ON c.id=p.category_id
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
    category: str = Field(default="accesorios", min_length=2, max_length=100)
    title: Optional[str] = Field(default=None, max_length=500)
    price: Optional[float] = Field(default=None, gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=10)
    image_url: Optional[str] = Field(default=None, max_length=2_000_000)


@core_router.post("/products/import-url")
def import_product_from_url(payload: ProductImport):
    try:
        product = import_url(
            payload.url,
            payload.category,
            title=payload.title,
            price=payload.price,
            currency=payload.currency,
            image_url=payload.image_url,
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
                       p.current_price, p.currency, p.image_url, p.product_url,
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
def create_personal_product(payload: PersonalProduct):
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


@core_router.patch("/products/{product_id}/images")
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


@core_router.patch("/products/{product_id}/model")
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
