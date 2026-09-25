"""
API REST para consultar y analizar productos.

Compatibilidad:
- Rutas legacy: /health, /products, /comparison, etc.
- API versionada: /api/v1/...
"""

import os
from typing import Optional

from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from db import get_connection
from curated import import_product_url
from monetization import router as monetization_router
from wompi import router as wompi_router
from tiktok_creator import creator_configured, get_creator_profile, get_showcase_products, sync_showcase

API_VERSION = "1.1.0"

app = FastAPI(
    title="Central Product Analyzer API",
    description="API REST para ingesta, análisis, comparación y monetización de productos.",
    version=API_VERSION,
)

# Los routers especializados conservan sus rutas actuales y se exponen también
# bajo /api/v1 para permitir una migración gradual del frontend y clientes.
app.include_router(monetization_router)
app.include_router(monetization_router, prefix="/api/v1")
app.include_router(wompi_router)
app.include_router(wompi_router, prefix="/api/v1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "ALLOWED_ORIGINS", "http://localhost:3000"
        ).split(",")
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
            return {"status": "ok", "database": "ok"}
        finally:
            conn.close()
    except Exception:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "degraded",
                "database": "error",
                "message": "Base de datos no disponible",
            },
        )


@core_router.get("/pipeline/summary")
def pipeline_summary():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    pl.name AS platform,
                    COUNT(p.id) AS product_count,
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
    category: Optional[str] = Query(None, description="ropa, calzado, accesorios"),
    platform: Optional[str] = Query(None, description="mercadolibre, amazon, tiktok"),
    limit: int = Query(50, ge=1, le=200),
):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            query = """
                SELECT p.id, p.title, pl.name AS platform, c.name AS category,
                       p.current_price, p.currency, p.rating, p.reviews_count,
                       p.sales_estimate, p.image_url, p.product_url, p.updated_at,
                       p.model_url, p.model_shape
                FROM products p
                JOIN platforms pl ON pl.id = p.platform_id
                LEFT JOIN categories c ON c.id = p.category_id
                WHERE p.is_active = TRUE
            """
            params = []
            if category:
                query += " AND c.name = %s"
                params.append(category)
            if platform:
                query += " AND pl.name = %s"
                params.append(platform)
            query += " ORDER BY p.updated_at DESC LIMIT %s"
            params.append(limit)
            cur.execute(query, params)
            return cur.fetchall()
    finally:
        conn.close()


class CuratedProductCreate(BaseModel):
    url: str
    category: str = "accesorios"
    title: Optional[str] = None
    notes: Optional[str] = None


@core_router.post("/curated-products")
def create_curated_product(payload: CuratedProductCreate):
    try:
        return import_product_url(payload.url, payload.category, payload.title, payload.notes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"No se pudo importar el producto: {exc}")


@core_router.get("/curated-products")
def list_curated_products(limit: int = Query(50, ge=1, le=200)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id, p.title, pl.name AS platform, p.product_url, p.image_url,
                       p.current_price, p.currency, cl.notes, cl.created_at
                FROM curated_links cl
                JOIN products p ON p.id = cl.product_id
                JOIN platforms pl ON pl.name = cl.platform
                WHERE p.is_active = TRUE
                ORDER BY cl.created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return cur.fetchall()
    finally:
        conn.close()


class TikTokCreatorMonetization(BaseModel):
    affiliate_url: Optional[str] = None
    commission_rate: Optional[float] = None
    video_url: Optional[str] = None
    content_type: Optional[str] = None
    showcase_status: str = "showcase"


@core_router.get("/tiktok/creator/status")
def tiktok_creator_status():
    configured = creator_configured()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT open_id, granted_scopes, user_type, last_sync_at, last_error
                FROM tiktok_creator_state
                WHERE id = 1
                """
            )
            state = cur.fetchone()
        return {
            "configured": configured,
            "connected": bool(state and state["open_id"]) and configured,
            "state": state,
        }
    finally:
        conn.close()


@core_router.post("/tiktok/creator/sync")
def tiktok_creator_sync(
    category: str = Query("accesorios"),
    limit: int = Query(2000, ge=1, le=2000),
):
    if not creator_configured():
        raise HTTPException(
            status_code=503,
            detail="Falta TIKTOK_CREATOR_ACCESS_TOKEN o las credenciales de la aplicación TikTok.",
        )
    try:
        return sync_showcase(category, limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo sincronizar TikTok Shop Creator: {exc}")


@core_router.get("/tiktok/creator/showcase")
def tiktok_creator_showcase(limit: int = Query(200, ge=1, le=2000)):
    if not creator_configured():
        raise HTTPException(
            status_code=503,
            detail="La conexión TikTok Shop Creator no está configurada.",
        )
    try:
        return get_showcase_products(limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo consultar el Showcase: {exc}")


@core_router.get("/tiktok/creator/products")
def tiktok_creator_products(limit: int = Query(100, ge=1, le=500)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id, p.title, p.current_price, p.currency, p.image_url,
                       p.product_url, tcp.tiktok_product_id, tcp.showcase_status,
                       tcp.affiliate_url, tcp.commission_rate,
                       tcp.estimated_commission, tcp.video_url,
                       tcp.content_type, tcp.synced_at
                FROM tiktok_creator_products tcp
                JOIN products p ON p.id = tcp.product_id
                WHERE p.is_active = TRUE
                ORDER BY tcp.synced_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return cur.fetchall()
    finally:
        conn.close()


@core_router.patch("/tiktok/creator/products/{product_id}")
def update_tiktok_creator_product(
    product_id: int,
    payload: TikTokCreatorMonetization,
):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE tiktok_creator_products
                SET affiliate_url=COALESCE(%s, affiliate_url),
                    commission_rate=COALESCE(%s, commission_rate),
                    estimated_commission=CASE
                        WHEN %s IS NOT NULL AND p.current_price IS NOT NULL
                        THEN p.current_price * %s / 100
                        ELSE estimated_commission
                    END,
                    video_url=COALESCE(%s, video_url),
                    content_type=COALESCE(%s, content_type),
                    showcase_status=COALESCE(%s, showcase_status),
                    updated_at=NOW()
                FROM products p
                WHERE tiktok_creator_products.product_id=%s
                  AND p.id=tiktok_creator_products.product_id
                RETURNING tiktok_creator_products.*
                """,
                (
                    payload.affiliate_url,
                    payload.commission_rate,
                    payload.commission_rate,
                    payload.commission_rate,
                    payload.video_url,
                    payload.content_type,
                    payload.showcase_status,
                    product_id,
                ),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Producto TikTok Creator no encontrado")
        conn.commit()
        return row
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@core_router.post("/tiktok/creator/products/{product_id}/click")
def register_tiktok_creator_click(product_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT affiliate_url
                FROM tiktok_creator_products
                WHERE product_id=%s AND affiliate_url IS NOT NULL
                """,
                (product_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="El producto no tiene enlace afiliado configurado")
            cur.execute(
                """
                INSERT INTO tiktok_creator_clicks(product_id, affiliate_url)
                VALUES (%s,%s)
                RETURNING id, clicked_at
                """,
                (product_id, row["affiliate_url"]),
            )
        conn.commit()
        return {"tracked": True, "affiliate_url": row["affiliate_url"], "click": cur.fetchone() if False else None}
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
def set_product_model(product_id: int, update: ModelUpdate):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE products SET
                    model_url = COALESCE(%s, model_url),
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
                SELECT p.*, pl.name AS platform, c.name AS category
                FROM products p
                JOIN platforms pl ON pl.id = p.platform_id
                LEFT JOIN categories c ON c.id = p.category_id
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

            cur.execute(
                "SELECT * FROM product_scores WHERE product_id = %s",
                (product_id,),
            )
            score = cur.fetchone()
        return {"product": product, "price_history": history, "score": score}
    finally:
        conn.close()


@core_router.get("/comparison")
def price_comparison(limit: int = Query(100, ge=1, le=500)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM product_price_comparison LIMIT %s",
                (limit,),
            )
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
                SELECT p.id, p.title, pl.name AS platform, p.current_price, p.currency,
                       p.rating, s.price_score, s.demand_score, s.trend_score,
                       s.opportunity_score, p.product_url, p.image_url,
                       p.model_url, p.model_shape
                FROM product_scores s
                JOIN products p ON p.id = s.product_id
                JOIN platforms pl ON pl.id = p.platform_id
                WHERE p.is_active = TRUE
                ORDER BY s.opportunity_score DESC
                LIMIT %s
                """,
                (limit,),
            )
            return cur.fetchall()
    finally:
        conn.close()


# Mantiene los endpoints existentes y publica la versión estable bajo /api/v1.
app.include_router(core_router)
app.include_router(core_router, prefix="/api/v1")

# OpenAPI se mantiene en /openapi.json; /docs y /redoc quedan disponibles.
