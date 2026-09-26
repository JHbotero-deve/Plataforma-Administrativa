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
from monetization import router as monetization_router
from wompi import router as wompi_router

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
    category: Optional[str] = Query(
        None, description="ropa, calzado, accesorios"),
    platform: Optional[str] = Query(
        None, description="mercadolibre, amazon, tiktok"),
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
                raise HTTPException(
                    status_code=404, detail="Producto no encontrado")
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
                raise HTTPException(
                    status_code=404, detail="Producto no encontrado")

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
