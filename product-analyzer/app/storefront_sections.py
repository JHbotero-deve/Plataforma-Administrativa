"""Objetivos de catálogo por sección y API administrativa."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StrictInt
from admin_auth import require_admin
from db import get_connection

PUBLIC_CARD_QUALITY_FILTER = """
AND COALESCE(p.source_metadata->>'catalog_status', 'current') <> 'stale'
AND NOT (COALESCE(p.source_metadata->'missing_fields', '[]'::jsonb) ?| ARRAY['title', 'price', 'currency', 'image_url'])
AND p.current_price > 0
AND p.currency NOT IN ('UNK', 'N/D')
AND pc.sale_price > 0
AND (p.catalog_expires_at IS NULL OR p.catalog_expires_at > NOW())
AND COALESCE(NULLIF(pc.product_url, ''), NULLIF(p.product_url, '')) IS NOT NULL
AND COALESCE(NULLIF(pc.image_url, ''), NULLIF(p.image_url, '')) IS NOT NULL
AND LOWER(COALESCE(pc.title, p.title, '')) NOT LIKE 'producto amazon %%'
AND LOWER(COALESCE(pc.title, p.title, '')) NOT LIKE 'producto aliexpress %%'
AND LOWER(COALESCE(pc.title, p.title, '')) NOT LIKE 'producto url-%%'
AND LOWER(COALESCE(pc.image_url, p.image_url, '')) NOT LIKE '%%enlace-de-la-imagen.jpg%%'
AND LOWER(COALESCE(pc.image_url, p.image_url, '')) NOT LIKE '%%placeholder%%'
AND LOWER(COALESCE(pc.image_url, p.image_url, '')) NOT LIKE '%%example.com%%'
AND (
    p.source_metadata IS NULL
    OR COALESCE(p.source_metadata->>'metadata_source', '') NOT IN ('url_only', 'public_product_page', 'public_product_page_empty', 'legacy_marketplace_api')
    OR (
        p.source_metadata->'field_sources'->>'title' IN ('public_product_page', 'user')
        AND p.source_metadata->'field_sources'->>'price' IN ('public_product_page', 'user')
        AND p.source_metadata->'field_sources'->>'currency' IN ('public_product_page', 'user')
        AND p.source_metadata->'field_sources'->>'image_url' IN ('public_product_page', 'user')
    )
)
"""

router = APIRouter(tags=["storefront-sections"])
DEFAULT_SECTIONS = (
    ("tecnologia", "Tecnología", "tecnologia", ["tecnologia"], "celular laptop tablet tecnologia", 100, 10),
    ("hogar", "Hogar", "hogar", ["hogar"], "hogar cocina decoracion muebles", 100, 20),
    ("moda", "Moda", "moda", ["moda", "ropa", "calzado"], "moda ropa zapatos calzado", 100, 30),
    ("accesorios", "Accesorios", "accesorios", ["accesorios"], "bolsos relojes joyeria accesorios", 100, 40),
    ("deportes", "Deportes", "deportes", ["deportes"], "deportes fitness gimnasio", 100, 50),
    ("belleza", "Belleza y cuidado personal", "belleza", ["belleza"], "belleza maquillaje skincare", 100, 60),
    ("bienestar", "Salud y bienestar", "bienestar", ["bienestar"], "bienestar cuidado personal", 100, 70),
    ("juguetes", "Juguetes", "juguetes", ["juguetes"], "juguetes juegos", 100, 80),
    ("oficina-estudio", "Oficina y estudio", "oficina", ["oficina"], "oficina papeleria estudio", 100, 90),
    ("ofertas-destacadas", "Ofertas destacadas", None, [], None, 100, 100),
)

def normalize_target_count(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 100 <= value <= 500:
        raise ValueError("La cantidad debe ser un entero entre 100 y 500.")
    return value

def ensure_storefront_section_storage():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS storefront_sections (
                    slug VARCHAR(60) PRIMARY KEY,
                    title VARCHAR(120) NOT NULL,
                    category_name VARCHAR(100),
                    category_names TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
                    search_keyword VARCHAR(255),
                    target_count INTEGER NOT NULL DEFAULT 100 CHECK (target_count BETWEEN 100 AND 500),
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            categories = ("tecnologia", "hogar", "moda", "ropa", "calzado", "accesorios", "deportes", "belleza", "bienestar", "juguetes", "oficina")
            cur.executemany("INSERT INTO categories(name) VALUES(%s) ON CONFLICT(name) DO NOTHING", [(name,) for name in categories])
            cur.executemany("""
                INSERT INTO storefront_sections (
                    slug, title, category_name, category_names, search_keyword, target_count, sort_order
                ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (slug) DO NOTHING
            """, DEFAULT_SECTIONS)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def get_refill_sections():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT slug, title, category_name, category_names, search_keyword, target_count
                FROM storefront_sections
                WHERE is_active = TRUE AND category_name IS NOT NULL
                ORDER BY sort_order
            """)
            return cur.fetchall()
    finally:
        conn.close()

def count_published_for_category(conn, category_names):
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT COUNT(*) AS total
            FROM published_cards pc
            JOIN products p ON p.id = pc.product_id
            JOIN categories c ON c.id = p.category_id
            WHERE pc.is_published = TRUE AND p.is_active = TRUE
              {PUBLIC_CARD_QUALITY_FILTER}
              AND c.name = ANY(%s)
        """, (category_names,))
        row = cur.fetchone()
    return int((row or {}).get("total") or 0)

def get_storefront_sections():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT slug, title, category_name, category_names, target_count, is_active, sort_order
                FROM storefront_sections ORDER BY sort_order
            """)
            sections = cur.fetchall()
            result = []
            for section in sections:
                if section["category_name"] is None:
                    cur.execute(f"""
                        SELECT COUNT(*) AS total FROM (
                            SELECT pc.id FROM published_cards pc
                            JOIN products p ON p.id = pc.product_id
                            WHERE pc.is_published = TRUE AND p.is_active = TRUE
                              {PUBLIC_CARD_QUALITY_FILTER}
                            ORDER BY COALESCE(pc.opportunity_score, 0) DESC, pc.published_at DESC
                            LIMIT %s
                        ) featured
                    """, (section["target_count"],))
                else:
                    cur.execute(f"""
                        SELECT COUNT(*) AS total
                        FROM published_cards pc
                        JOIN products p ON p.id = pc.product_id
                        JOIN categories c ON c.id = p.category_id
                        WHERE pc.is_published = TRUE AND p.is_active = TRUE
                          {PUBLIC_CARD_QUALITY_FILTER}
                          AND c.name = ANY(%s)
                    """, (section["category_names"],))
                count = int((cur.fetchone() or {}).get("total") or 0)
                result.append({
                    "slug": section["slug"], "title": section["title"],
                    "category_name": section["category_name"],
                    "category_names": section["category_names"] or [],
                    "target_count": section["target_count"], "published_count": count,
                    "missing_count": max(0, section["target_count"] - count),
                    "is_active": section["is_active"], "sort_order": section["sort_order"],
                })
            return result
    finally:
        conn.close()

class SectionTargetPayload(BaseModel):
    target_count: StrictInt = Field(ge=100, le=500)

@router.get("/storefront/sections")
def list_storefront_sections():
    return get_storefront_sections()

@router.put("/storefront/sections/{slug}", dependencies=[Depends(require_admin)])
def update_storefront_section(slug: str, payload: SectionTargetPayload):
    try:
        target = normalize_target_count(payload.target_count)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE storefront_sections SET target_count = %s, updated_at = NOW()
                WHERE slug = %s RETURNING slug
            """, (target, slug))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Sección no encontrada.")
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return get_storefront_sections()
