"""Central published-card API."""
from typing import Optional
import os

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from db import get_connection
from notifications import send_telegram_publication

router = APIRouter(tags=["publications"])


class PublicationPayload(BaseModel):
    product_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=500)
    subtitle: Optional[str] = Field(default=None, max_length=500)
    price_display: Optional[str] = Field(default=None, max_length=100)
    image_url: Optional[str] = Field(default=None, max_length=2_000_000)
    product_url: Optional[str] = Field(default=None, max_length=2000)
    sale_price: Optional[float] = Field(default=None, gt=0)
    cost_price: Optional[float] = Field(default=None, ge=0)
    opportunity_score: Optional[float] = None
    footer: Optional[str] = Field(default="Disponible en Central", max_length=200)
    accent: str = Field(default="#b6f23a", min_length=4, max_length=20)
    is_published: bool = True


@router.get("/publications")
def list_publications(limit: int = Query(20, ge=1, le=100), include_unpublished: bool = False):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            where = "" if include_unpublished else "AND pc.is_published = TRUE"
            cur.execute(
                f"""
                SELECT pc.id, pc.product_id, pc.title, pc.subtitle, pc.price_display,
                       COALESCE(NULLIF(pc.image_url, ''), NULLIF(p.image_url, '')) AS image_url,
                       COALESCE(NULLIF(pc.product_url, ''), NULLIF(p.product_url, '')) AS product_url,
                       pc.sale_price, pc.cost_price,
                       pc.profit_amount, pc.profit_margin_pct, pc.opportunity_score, pc.footer,
                       pc.accent, pc.is_published, pc.sort_order, pc.published_at,
                       p.current_price, p.currency, p.image_gallery, p.affiliate_url, p.external_id, p.sku, p.description, p.stock, pl.name AS platform, c.name AS category
                FROM published_cards pc
                JOIN products p ON p.id = pc.product_id
                JOIN platforms pl ON pl.id = p.platform_id
                LEFT JOIN categories c ON c.id = p.category_id
                WHERE p.is_active = TRUE {where}
                ORDER BY pc.sort_order ASC, pc.published_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows=cur.fetchall()
            # Si todavía no hay tarjetas publicadas, la tienda no debe quedar vacía:
            # muestra temporalmente las mejores oportunidades activas del catálogo.
            if not rows and not include_unpublished:
                cur.execute(
                    """
                    SELECT p.id AS product_id, p.id, p.title, NULL AS subtitle, NULL AS price_display,
                           p.image_url, COALESCE(NULLIF(p.affiliate_url, ''), NULLIF(p.product_url, '')) AS product_url,
                           p.current_price AS sale_price, p.current_price AS cost_price,
                           0::numeric AS profit_amount, 0::numeric AS profit_margin_pct,
                           COALESCE(s.opportunity_score, 0) AS opportunity_score,
                           'Disponible en Central' AS footer, '#b6f23a' AS accent,
                           FALSE AS is_published, 0 AS sort_order, p.updated_at AS published_at,
                           p.current_price, p.currency, p.image_gallery, p.affiliate_url, p.external_id, p.sku,
                           p.description, p.stock, pl.name AS platform, c.name AS category
                    FROM products p
                    JOIN platforms pl ON pl.id = p.platform_id
                    LEFT JOIN categories c ON c.id = p.category_id
                    LEFT JOIN product_scores s ON s.product_id = p.id
                    WHERE p.is_active = TRUE
                      AND (p.image_url IS NOT NULL OR jsonb_array_length(COALESCE(p.image_gallery, '[]'::jsonb)) > 0)
                    ORDER BY COALESCE(s.opportunity_score, 0) DESC, p.updated_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()

            import re, unicodedata
            for row in rows:
                slug=unicodedata.normalize("NFKD",row["title"] or "").encode("ascii","ignore").decode().lower()
                row["slug"]=re.sub(r"[^a-z0-9]+","-",slug).strip("-")[:90] or "producto"
                row["slug"]=row["slug"]+"-"+str(row["product_id"])
                row["canonical_url"]=os.getenv("PUBLIC_STORE_URL","https://central-7ykr.vercel.app").rstrip()+"/producto/"+row["slug"]
                row["checkout_mode"]="CENTRAL" if str(row["platform"]).lower()=="personal" else "EXTERNAL"
            return rows
    finally:
        conn.close()


@router.post("/publications")
def publish_card(payload: PublicationPayload):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id, p.image_url, p.product_url, p.current_price, p.currency,
                       s.opportunity_score
                FROM products p
                LEFT JOIN product_scores s ON s.product_id = p.id
                WHERE p.id = %s AND p.is_active = TRUE
                """,
                (payload.product_id,),
            )
            product = cur.fetchone()
            if not product:
                raise HTTPException(status_code=404, detail="Producto activo no encontrado")
            cur.execute(
                """
                INSERT INTO published_cards (
                    product_id, title, subtitle, price_display, image_url, product_url,
                    sale_price, cost_price, profit_amount, profit_margin_pct,
                    opportunity_score, footer, accent, is_published, published_at, updated_at
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
                ON CONFLICT (product_id) DO UPDATE SET
                    title=EXCLUDED.title, subtitle=EXCLUDED.subtitle,
                    price_display=EXCLUDED.price_display, image_url=EXCLUDED.image_url,
                    product_url=EXCLUDED.product_url, sale_price=EXCLUDED.sale_price,
                    cost_price=EXCLUDED.cost_price, profit_amount=EXCLUDED.profit_amount,
                    profit_margin_pct=EXCLUDED.profit_margin_pct, opportunity_score=EXCLUDED.opportunity_score,
                    footer=EXCLUDED.footer, accent=EXCLUDED.accent,
                    is_published=EXCLUDED.is_published, published_at=NOW(), updated_at=NOW()
                RETURNING id
                """,
                (
                    payload.product_id, payload.title, payload.subtitle, payload.price_display,
                    payload.image_url or product["image_url"], payload.product_url or product["product_url"],
                    payload.sale_price if payload.sale_price is not None else product["current_price"],
                    payload.cost_price if payload.cost_price is not None else product["current_price"],
                    (payload.sale_price if payload.sale_price is not None else product["current_price"]) - (payload.cost_price if payload.cost_price is not None else product["current_price"]),
                    (((payload.sale_price if payload.sale_price is not None else product["current_price"]) - (payload.cost_price if payload.cost_price is not None else product["current_price"])) /
                     (payload.sale_price if payload.sale_price is not None else product["current_price"]))*100 if (payload.sale_price if payload.sale_price is not None else product["current_price"]) else 0,
                    payload.opportunity_score if payload.opportunity_score is not None else product["opportunity_score"],
                    payload.footer, payload.accent, payload.is_published,
                ),
            )
            publication_id = cur.fetchone()["id"]
        conn.commit()
        return {"id": publication_id, "status": "published" if payload.is_published else "draft"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@router.patch("/publications/{publication_id}")
def update_publication(publication_id: int, payload: PublicationPayload):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE published_cards
                SET title=%s, subtitle=%s, price_display=%s, image_url=%s, product_url=%s,
                    sale_price=%s, cost_price=%s, profit_amount=%s, profit_margin_pct=%s,
                    opportunity_score=%s, footer=%s, accent=%s, is_published=%s, updated_at=NOW()
                WHERE id=%s
                RETURNING id, is_published
                """,
                (
                    payload.title, payload.subtitle, payload.price_display, payload.image_url,
                    payload.product_url, payload.sale_price, payload.cost_price,
                    ((payload.sale_price or 0)-(payload.cost_price or 0)),
                    (((payload.sale_price or 0)-(payload.cost_price or 0))/payload.sale_price*100) if payload.sale_price else 0,
                    payload.opportunity_score, payload.footer, payload.accent,
                    payload.is_published, publication_id,
                ),
            )
            result = cur.fetchone()
            if not result:
                raise HTTPException(status_code=404, detail="Publicación no encontrada")
        conn.commit()
        return result
    except HTTPException:
        conn.rollback()
        raise
    finally:
        conn.close()


@router.post("/publications/{publication_id}/telegram")
def publish_publication_telegram(publication_id: int):
    conn=get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT pc.id,pc.title,pc.subtitle,pc.price_display,pc.image_url,pc.product_url
                FROM published_cards pc JOIN products p ON p.id=pc.product_id WHERE pc.id=%s""",(publication_id,))
            publication=cur.fetchone()
        if not publication: raise HTTPException(status_code=404,detail="Publicación no encontrada")
        base=os.getenv("PUBLIC_STORE_URL","").rstrip("/")
        publication["store_url"]=f"{base}/tienda" if base else publication.get("product_url")
        send_telegram_publication(publication)
        return {"status":"sent","channel":"telegram","publication_id":publication_id}
    except HTTPException: raise
    except RuntimeError as exc: raise HTTPException(status_code=503,detail=str(exc))
    finally: conn.close()
