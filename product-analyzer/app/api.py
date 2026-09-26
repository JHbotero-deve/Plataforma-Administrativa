"""
Central Product Analyzer REST API.
"""

import os
from typing import Optional

from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from analysis import score_product
from db import get_connection, upsert_product
from monetization import router as monetization_router
from url_import import import_url
from wompi import router as wompi_router

API_VERSION = "1.2.0"

app = FastAPI(
    title="Central Product Analyzer API",
    description="Ingesta, análisis, comparación y monetización de productos reales.",
    version=API_VERSION,
)

app.include_router(monetization_router)
app.include_router(monetization_router, prefix="/api/v1")
app.include_router(wompi_router)
app.include_router(wompi_router, prefix="/api/v1")

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
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"status": "degraded", "database": "error", "message": str(exc)[:300]},
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
                       p.sales_estimate, p.image_url, p.product_url, p.updated_at,
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


class ProductImport(BaseModel):
    url: str = Field(min_length=10, max_length=2000)
    category: str = Field(default="accesorios", min_length=2, max_length=100)
    title: Optional[str] = Field(default=None, max_length=500)
    price: Optional[float] = Field(default=None, gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=10)


@core_router.post("/products/import-url")
def import_product_from_url(payload: ProductImport):
    try:
        product = import_url(
            payload.url,
            payload.category,
            title=payload.title,
            price=payload.price,
            currency=payload.currency,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"No fue posible leer la URL: {exc}")

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
                       p.model_url, p.model_shape
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


app.include_router(core_router)
app.include_router(core_router, prefix="/api/v1")
