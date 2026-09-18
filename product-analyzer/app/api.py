"""
API REST para consultar los productos analizados.

Endpoints principales:
GET /products                 -> lista productos (filtrable por categoría/plataforma)
GET /products/{product_id}    -> detalle de un producto + su histórico de precios
GET /comparison                -> compara el mismo tipo de producto entre plataformas
GET /opportunities/top         -> top productos por opportunity_score (los m                                     interesantes para promocionar/vender)
"""

from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from db import get_connection
from monetization import router as monetization_router

app = FastAPI(
    title="API de Análisis de Productos",
    description="Comparación y scoring de productos de moda en Mercado Libre, Amazon y TikTok Shop",
    version="1.0.0",
)

app.include_router(monetization_router)

# Habilitado abierto para poder conectar un frontend fácilmente; restringir en producción
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/products")
def list_products(
    category: Optional[str] = Query(None, description="ej: ropa, calzado, accesorios"),
    platform: Optional[str] = Query(None, description="ej: mercadolibre, amazon, tiktok"),
    limit: int = Query(50, le=200),
):
    conn = get_connection()
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
        results = cur.fetchall()
    conn.close()
    return results


class ModelUpdate(BaseModel):
    model_url: Optional[str] = None
    model_shape: Optional[str] = None  # 'garment', 'footwear', 'accessory'


@app.patch("/products/{product_id}/model")
def set_product_model(product_id: int, update: ModelUpdate):
    """
    Asigna un modelo 3D real (.glb/.gltf) a un producto, o solo cambia la
    forma de referencia usada por el visor 3D genérico cuando no hay modelo.
    """
    conn = get_connection()
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
            conn.close()
            raise HTTPException(status_code=404, detail="Producto no encontrado")
    conn.commit()
    conn.close()
    return result


@app.get("/products/{product_id}")
def get_product(product_id: int):
    conn = get_connection()
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
            conn.close()
            raise HTTPException(status_code=404, detail="Producto no encontrado")

        cur.execute(
            "SELECT price, recorded_at FROM price_history "
            "WHERE product_id = %s ORDER BY recorded_at ASC",
            (product_id,),
        )
        history = cur.fetchall()

        cur.execute(
            "SELECT * FROM product_scores WHERE product_id = %s", (product_id,)
        )
        score = cur.fetchone()
    conn.close()

    return {"product": product, "price_history": history, "score": score}


@app.get("/comparison")
def price_comparison(limit: int = Query(100, le=500)):
    """Usa la vista product_price_comparison para ver el mismo producto entre plataformas."""
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM product_price_comparison LIMIT %s", (limit,)
        )
        results = cur.fetchall()
    conn.close()
    return results


@app.get("/opportunities/top")
def top_opportunities(limit: int = Query(20, le=100)):
    """Productos con mejor opportunity_score: los más recomendables para promocionar."""
    conn = get_connection()
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
            ORDER BY s.opportunity_score DESC
            LIMIT %s
            """,
            (limit,),
        )
        results = cur.fetchall()
    conn.close()
    return results

