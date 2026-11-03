"""Central published-card API."""
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from admin_auth import require_admin
from db import get_connection
from notifications import send_telegram_publication

router = APIRouter(tags=["publications"])


def ensure_draft_storage():
    migrations = (
        "016_admin_publication_drafts.sql",
        "020_storefront_exclusions.sql",
        "021_marketplace_search_requests.sql",
    )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for name in migrations:
                migration = Path(__file__).with_name("sql") / name
                with migration.open(encoding="utf-8") as file:
                    cur.execute(file.read())
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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
    footer: Optional[str] = Field(
        default="Disponible en Central", max_length=200)
    accent: str = Field(default="#b6f23a", min_length=4, max_length=20)
    is_published: bool = True


class PublicationDraftPayload(BaseModel):
    subtitle: Optional[str] = Field(default=None, max_length=500)
    footer: str = Field(default="Disponible en Central", max_length=200)
    accent: str = Field(default="#b6f23a", pattern=r"^#[0-9a-fA-F]{6}$")
    is_published: bool = True
    expected_revision: int = Field(default=0, ge=0)


class PublicationOrderPayload(BaseModel):
    publication_ids: list[int]


def _profit_metrics(sale_price, cost_price):
    if sale_price is None or cost_price is None or float(sale_price) <= 0:
        return None, None
    profit = round(float(sale_price) - float(cost_price), 2)
    return profit, round(profit / float(sale_price) * 100, 2)


@router.get("/publications")
def list_publications(
    request: Request,
    limit: int = Query(20, ge=1, le=5000),
    include_unpublished: bool = False,
):
    if include_unpublished:
        require_admin(request)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            where = "" if include_unpublished else "AND pc.is_published = TRUE"
            private_fields = (
                "pc.cost_price, pc.profit_amount, pc.profit_margin_pct"
                if include_unpublished else
                "NULL::NUMERIC AS cost_price, NULL::NUMERIC AS profit_amount, "
                "NULL::NUMERIC AS profit_margin_pct"
            )
            customer_price = "p.current_price" if include_unpublished else "pc.sale_price"
            order_by = (
                "pc.sort_order ASC, pc.published_at DESC, pc.id DESC"
                if include_unpublished else
                "COALESCE(pc.opportunity_score, 0) DESC, pc.sort_order ASC, pc.published_at DESC, pc.id DESC"
            )
            cur.execute(
                f"""
                SELECT pc.id, pc.product_id, pc.title, pc.subtitle, pc.price_display,
                       COALESCE(NULLIF(pc.image_url, ''), NULLIF(p.image_url, '')) AS image_url,
                       COALESCE(NULLIF(pc.product_url, ''), NULLIF(p.product_url, '')) AS product_url,
                       pc.sale_price, {private_fields},
                       pc.opportunity_score, pc.footer,
                       pc.accent, pc.is_published, pc.sort_order, pc.published_at,
                       {customer_price} AS current_price, p.previous_price AS original_price,
                       CASE WHEN p.previous_price > 0 AND pc.sale_price > 0 THEN ROUND((p.previous_price - pc.sale_price) / p.previous_price * 100, 2) ELSE NULL END AS discount_pct,
                       p.currency, p.image_gallery, p.affiliate_url, p.external_id, p.sku,
                       p.description, p.stock, pl.name AS platform, c.name AS category,
                       seller.name AS seller_name, p.source_metadata->>'seller_url' AS seller_url
                FROM published_cards pc
                JOIN products p ON p.id = pc.product_id
                JOIN platforms pl ON pl.id = p.platform_id
                LEFT JOIN categories c ON c.id = p.category_id
                LEFT JOIN sellers seller ON seller.id = p.seller_id
                WHERE p.is_active = TRUE {where}
                ORDER BY {order_by}
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()

            import re
            import unicodedata
            for row in rows:
                slug = unicodedata.normalize("NFKD", row["title"] or "").encode(
                    "ascii", "ignore").decode().lower()
                row["slug"] = re.sub(r"[^a-z0-9]+", "-",
                                     slug).strip("-")[:90] or "producto"
                row["slug"] = row["slug"] + "-" + str(row["product_id"])
                row["canonical_url"] = os.getenv(
                    "PUBLIC_STORE_URL", "https://central-7ykr.vercel.app").rstrip() + "/producto/" + row["slug"]
                row["checkout_mode"] = "CENTRAL" if str(
                    row["platform"]).lower() == "personal" else "EXTERNAL"
            return rows
    finally:
        conn.close()


@router.put(
    "/publications/order", dependencies=[Depends(require_admin)]
)
def reorder_publications(payload: PublicationOrderPayload):
    publication_ids = payload.publication_ids
    if (
        not publication_ids
        or any(publication_id <= 0 for publication_id in publication_ids)
        or len(publication_ids) != len(set(publication_ids))
    ):
        raise HTTPException(
            status_code=422, detail="El orden de publicaciones no es válido"
        )

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pc.id
                FROM published_cards pc
                JOIN products p ON p.id = pc.product_id
                WHERE p.is_active = TRUE
                ORDER BY pc.sort_order ASC, pc.published_at DESC, pc.id DESC
                FOR UPDATE OF pc
                """
            )
            current_ids = [row["id"] for row in cur.fetchall()]
            requested_ids = set(publication_ids)
            if not requested_ids.issubset(current_ids):
                raise HTTPException(
                    status_code=409,
                    detail="Las tarjetas cambiaron. Actualiza la administración e inténtalo de nuevo.",
                )
            ordered_ids = publication_ids + [
                publication_id
                for publication_id in current_ids
                if publication_id not in requested_ids
            ]
            for position, publication_id in enumerate(ordered_ids):
                cur.execute(
                    """
                    UPDATE published_cards
                    SET sort_order = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (position, publication_id),
                )
        conn.commit()
        return {"status": "saved", "publication_ids": ordered_ids}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@router.get(
    "/publications/drafts", dependencies=[Depends(require_admin)]
)
def list_publication_drafts():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT publication_id, subtitle, footer, accent,
                       is_published, revision, updated_at
                FROM admin_publication_drafts
                """
            )
            rows = cur.fetchall()
        return {str(row["publication_id"]): row for row in rows}
    finally:
        conn.close()


@router.put(
    "/publications/{publication_id}/draft",
    dependencies=[Depends(require_admin)],
)
def save_publication_draft(
    publication_id: int,
    payload: PublicationDraftPayload,
):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pc.id
                FROM published_cards pc
                JOIN products p ON p.id = pc.product_id
                WHERE pc.id = %s AND p.is_active = TRUE
                """,
                (publication_id,),
            )
            if not cur.fetchone():
                raise HTTPException(
                    status_code=404, detail="Publicación no encontrada"
                )
            cur.execute(
                "SELECT revision FROM admin_publication_drafts WHERE publication_id = %s",
                (publication_id,),
            )
            current_draft = cur.fetchone()
            if current_draft:
                if current_draft["revision"] != payload.expected_revision:
                    raise HTTPException(
                        status_code=409,
                        detail="El borrador cambió en otro dispositivo. Tu copia local se conservó.",
                    )
                cur.execute(
                    """
                    UPDATE admin_publication_drafts
                    SET subtitle = %s, footer = %s, accent = %s,
                        is_published = %s, revision = revision + 1, updated_at = NOW()
                    WHERE publication_id = %s AND revision = %s
                    RETURNING revision, updated_at
                    """,
                    (
                        payload.subtitle,
                        payload.footer,
                        payload.accent,
                        payload.is_published,
                        publication_id,
                        payload.expected_revision,
                    ),
                )
            else:
                if payload.expected_revision != 0:
                    raise HTTPException(
                        status_code=409,
                        detail="El borrador cambió en otro dispositivo. Tu copia local se conservó.",
                    )
                cur.execute(
                    """
                    INSERT INTO admin_publication_drafts (
                        publication_id, subtitle, footer, accent,
                        is_published, revision, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, 1, NOW())
                    ON CONFLICT (publication_id) DO NOTHING
                    RETURNING revision, updated_at
                    """,
                    (
                        publication_id,
                        payload.subtitle,
                        payload.footer,
                        payload.accent,
                        payload.is_published,
                    ),
                )
            saved = cur.fetchone()
            if not saved:
                raise HTTPException(
                    status_code=409,
                    detail="El borrador cambió en otro dispositivo. Tu copia local se conservó.",
                )
        conn.commit()
        return {
            "status": "saved",
            "revision": saved["revision"],
            "updated_at": saved["updated_at"],
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@router.delete(
    "/publications/{publication_id}/draft",
    dependencies=[Depends(require_admin)],
)
def delete_publication_draft(publication_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM admin_publication_drafts WHERE publication_id = %s",
                (publication_id,),
            )
        conn.commit()
        return {"status": "deleted"}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@router.post(
    "/publications", dependencies=[Depends(require_admin)]
)
def publish_card(payload: PublicationPayload):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id, p.image_url, p.product_url, p.current_price, p.currency,
                       p.title, p.description, p.image_gallery, p.affiliate_url,
                       pl.name AS platform, s.opportunity_score
                FROM products p
                JOIN platforms pl ON pl.id = p.platform_id
                LEFT JOIN product_scores s ON s.product_id = p.id
                WHERE p.id = %s AND p.is_active = TRUE
                """,
                (payload.product_id,),
            )
            product = cur.fetchone()
            if not product:
                raise HTTPException(
                    status_code=404, detail="Producto activo no encontrado")

            # Fuentes externas: se publica la ficha original sin editar.
            # Producto personal: Studio sí puede editarlo antes de publicar.
            is_personal = str(product["platform"]).lower() == "personal"
            if not is_personal:
                canonical_title = product["title"]
                canonical_image = product["image_url"] or (
                    (product["image_gallery"] or [None])[0])
                canonical_url = product["affiliate_url"] or product["product_url"]
                canonical_price = product["current_price"]
                payload = payload.model_copy(update={
                    "title": canonical_title,
                    "image_url": canonical_image,
                    "product_url": canonical_url,
                    "sale_price": canonical_price,
                    "cost_price": canonical_price,
                    "price_display": (
                        f"{canonical_price:,.0f} {product['currency'] or 'COP'}"
                        if canonical_price is not None else None
                    ),
                })
            else:
                canonical_price = product["current_price"]
                payload = payload.model_copy(update={
                    "sale_price": canonical_price,
                    "price_display": (
                        f"{canonical_price:,.0f} {product['currency'] or 'COP'}"
                        if canonical_price is not None else None
                    ),
                })
            profit_amount, profit_margin_pct = _profit_metrics(
                payload.sale_price,
                payload.cost_price if is_personal else product["current_price"],
            )

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
                    payload.sale_price,
                    payload.cost_price if is_personal else product["current_price"],
                    profit_amount,
                    profit_margin_pct,
                    payload.opportunity_score if payload.opportunity_score is not None else product[
                        "opportunity_score"],
                    payload.footer, payload.accent, payload.is_published,
                ),
            )
            publication_id = cur.fetchone()["id"]
            cur.execute(
                "UPDATE products SET storefront_excluded = %s WHERE id = %s",
                (not payload.is_published, payload.product_id),
            )
            cur.execute(
                "DELETE FROM admin_publication_drafts WHERE publication_id = %s",
                (publication_id,),
            )
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


@router.patch(
    "/publications/{publication_id}", dependencies=[Depends(require_admin)]
)
def update_publication(publication_id: int, payload: PublicationPayload):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pc.product_id, p.title, p.image_url, p.image_gallery, p.product_url,
                       p.affiliate_url, p.current_price, p.currency, pl.name AS platform,
                       s.opportunity_score
                FROM published_cards pc
                JOIN products p ON p.id = pc.product_id
                JOIN platforms pl ON pl.id = p.platform_id
                LEFT JOIN product_scores s ON s.product_id = p.id
                WHERE pc.id = %s AND p.is_active = TRUE
                """,
                (publication_id,),
            )
            current = cur.fetchone()
            if not current:
                raise HTTPException(
                    status_code=404, detail="Publicación no encontrada")

            if str(current["platform"]).lower() != "personal":
                # Una publicación externa no puede convertirse en una ficha editada.
                payload = payload.model_copy(update={
                    "title": current["title"],
                    "image_url": current["image_url"] or ((current["image_gallery"] or [None])[0]),
                    "product_url": current["affiliate_url"] or current["product_url"],
                    "sale_price": current["current_price"],
                    "cost_price": current["current_price"],
                    "price_display": (
                        f"{current['current_price']:,.0f} {current['currency'] or 'COP'}"
                        if current["current_price"] is not None else None
                    ),
                    "opportunity_score": current["opportunity_score"],
                })
            else:
                canonical_price = current["current_price"]
                payload = payload.model_copy(update={
                    "sale_price": canonical_price,
                    "price_display": (
                        f"{canonical_price:,.0f} {current['currency'] or 'COP'}"
                        if canonical_price is not None else None
                    ),
                })
            profit_amount, profit_margin_pct = _profit_metrics(
                payload.sale_price,
                payload.cost_price if str(current["platform"]).lower() == "personal"
                else current["current_price"],
            )

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
                    profit_amount, profit_margin_pct,
                    payload.opportunity_score, payload.footer, payload.accent,
                    payload.is_published, publication_id,
                ),
            )
            result = cur.fetchone()
            if not result:
                raise HTTPException(
                    status_code=404, detail="Publicación no encontrada")
            cur.execute(
                "UPDATE products SET storefront_excluded = %s WHERE id = %s",
                (not payload.is_published, current["product_id"]),
            )
            cur.execute(
                "DELETE FROM admin_publication_drafts WHERE publication_id = %s",
                (publication_id,),
            )
        conn.commit()
        return result
    except HTTPException:
        conn.rollback()
        raise
    finally:
        conn.close()


@router.delete(
    "/publications/{publication_id}",
    dependencies=[Depends(require_admin)],
)
def delete_publication(publication_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM published_cards
                WHERE id = %s
                RETURNING id, product_id
                """,
                (publication_id,),
            )
            deleted = cur.fetchone()
            if not deleted:
                raise HTTPException(
                    status_code=404, detail="Publicación no encontrada"
                )
            cur.execute(
                "UPDATE products SET storefront_excluded = TRUE WHERE id = %s",
                (deleted["product_id"],),
            )
        conn.commit()
        return {"id": deleted["id"], "status": "deleted"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@router.post(
    "/publications/{publication_id}/telegram",
    dependencies=[Depends(require_admin)],
)
def publish_publication_telegram(publication_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT pc.id,pc.title,pc.subtitle,pc.price_display,pc.image_url,pc.product_url
                FROM published_cards pc JOIN products p ON p.id=pc.product_id WHERE pc.id=%s""",
                (publication_id,),
            )
            publication = cur.fetchone()
        if not publication:
            raise HTTPException(
                status_code=404, detail="Publicación no encontrada")
        base = os.getenv("PUBLIC_STORE_URL", "").rstrip("/")
        publication["store_url"] = f"{base}/tienda" if base else publication.get(
            "product_url")
        send_telegram_publication(publication)
        return {"status": "sent", "channel": "telegram", "publication_id": publication_id}
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    finally:
        conn.close()
