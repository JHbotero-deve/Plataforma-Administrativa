from fastapi import APIRouter, HTTPException, Query

from db import get_connection
from tiktok_creator import creator_configured, get_creator_profile, sync_showcase

router = APIRouter(prefix="/tiktok/creator", tags=["tiktok-creator"])


@router.get("/status")
def creator_status():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT open_id, granted_scopes, user_type,
                       last_sync_at, last_error, updated_at
                FROM tiktok_creator_state
                WHERE id = 1
                """
            )
            state = cur.fetchone()
        return {
            "configured": creator_configured(),
            "state": state,
        }
    finally:
        conn.close()


@router.get("/profile")
def creator_profile():
    if not creator_configured():
        raise HTTPException(status_code=503, detail="TikTok Creator no está configurado")
    try:
        return get_creator_profile()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/sync")
def creator_sync(limit: int = Query(200, ge=1, le=2000)):
    if not creator_configured():
        raise HTTPException(status_code=503, detail="TikTok Creator no está configurado")
    try:
        return sync_showcase(limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/products")
def creator_products(limit: int = Query(100, ge=1, le=500)):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT p.id, p.title, p.current_price, p.currency,
                       p.image_url, p.product_url, p.updated_at,
                       t.tiktok_product_id, t.showcase_status,
                       t.affiliate_url, t.commission_rate,
                       t.estimated_commission, t.video_url,
                       t.content_type, t.synced_at
                FROM tiktok_creator_products t
                JOIN products p ON p.id = t.product_id
                WHERE p.is_active = TRUE
                ORDER BY t.synced_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return cur.fetchall()
    finally:
        conn.close()
