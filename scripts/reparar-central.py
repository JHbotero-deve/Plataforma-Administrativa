import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "product-analyzer" / "app"
sys.path.insert(0, str(APP))


def load_env():
    for name in (".env", "product-analyzer/.env"):
        path = ROOT / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def log(message):
    print(f"[repair] {message}")


def publish_products(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT p.id,p.title,p.current_price,p.currency,p.image_url,p.product_url,
                   s.opportunity_score
            FROM products p
            LEFT JOIN product_scores s ON s.product_id=p.id
            WHERE p.is_active=TRUE
              AND p.current_price IS NOT NULL
              AND p.product_url IS NOT NULL
              AND p.product_url <> ''
              AND NOT EXISTS (
                  SELECT 1 FROM published_cards pc
                  WHERE pc.product_id=p.id AND pc.is_published=TRUE
              )
            ORDER BY COALESCE(s.opportunity_score,0) DESC,p.updated_at DESC
        """)
        rows = cur.fetchall()
        for p in rows:
            currency = str(p["currency"] or "COP").upper()
            price = float(p["current_price"])
            price_display = f"{currency} {price:,.0f}"
            cur.execute("""
                INSERT INTO published_cards(
                    product_id,title,subtitle,price_display,image_url,product_url,
                    sale_price,cost_price,profit_amount,profit_margin_pct,
                    opportunity_score,footer,accent,is_published,published_at,updated_at
                )
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,0,0,%s,%s,%s,TRUE,NOW(),NOW())
                ON CONFLICT(product_id) DO UPDATE SET
                    title=EXCLUDED.title,
                    price_display=EXCLUDED.price_display,
                    image_url=COALESCE(EXCLUDED.image_url,published_cards.image_url),
                    product_url=EXCLUDED.product_url,
                    sale_price=EXCLUDED.sale_price,
                    opportunity_score=EXCLUDED.opportunity_score,
                    is_published=TRUE,
                    updated_at=NOW()
            """,(
                p["id"],p["title"],"Producto verificado por Central",price_display,
                p["image_url"],p["product_url"],price,price,p["opportunity_score"],
                "Disponible en Central","#b6f23a"
            ))
        conn.commit()
    return len(rows)


def refresh_amazon_metadata(conn):
    from url_import import _metadata

    updated = 0
    with conn.cursor() as cur:
        cur.execute("""
            SELECT p.id,p.product_url,p.image_url,p.image_gallery
            FROM products p
            JOIN platforms pl ON pl.id=p.platform_id
            WHERE pl.name='amazon' AND p.is_active=TRUE
        """)
        rows = cur.fetchall()
    for row in rows:
        try:
            data = _metadata(row["product_url"])
            image = (data.get("image_url") or "").strip() or row["image_url"]
            gallery = data.get("gallery_urls") or row["image_gallery"] or []
            price = data.get("price")
            currency = data.get("currency")
            title = data.get("title")
            if not image and not gallery and price is None and not title:
                continue
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE products
                    SET title=COALESCE(%s,title),
                        image_url=COALESCE(%s,image_url),
                        image_gallery=CASE WHEN %s::jsonb <> '[]'::jsonb THEN %s::jsonb ELSE image_gallery END,
                        current_price=COALESCE(%s,current_price),
                        currency=COALESCE(%s,currency),
                        source_metadata=COALESCE(source_metadata,'{}'::jsonb) || %s::jsonb,
                        catalog_expires_at=NOW()+INTERVAL '48 hours',
                        updated_at=NOW()
                    WHERE id=%s
                """,(
                    title,image,json.dumps(gallery),json.dumps(gallery),price,currency,
                    json.dumps({"metadata_refresh":"amazon_product_page"}),row["id"]
                ))
            conn.commit()
            updated += 1
        except Exception as exc:
            log(f"Amazon {row['id']}: metadata no disponible ({exc})")
    return updated


def ingest_marketplaces(conn):
    from db import upsert_product
    from ingest import fetch_mercadolibre

    queries = [
        ("accesorios","soporte celular"),
        ("accesorios","audifonos bluetooth"),
        ("electronica","smartwatch"),
        ("electronica","teclado mecanico"),
        ("electronica","mouse gamer"),
        ("hogar","organizador hogar"),
        ("hogar","lampara led"),
        ("ropa","campera mujer"),
        ("calzado","zapatillas urbanas"),
        ("fitness","accesorios gimnasio"),
    ]
    inserted = 0
    for category, query in queries:
        try:
            products = fetch_mercadolibre(query, limit=20)
            for product in products:
                try:
                    upsert_product(conn,"mercadolibre",category,product)
                    inserted += 1
                except Exception as exc:
                    conn.rollback()
                    log(f"Mercado Libre {query}: no se pudo guardar {exc}")
        except Exception as exc:
            log(f"Mercado Libre {query}: {exc}")
    return inserted


def score_catalog(conn):
    from analysis import score_product

    with conn.cursor() as cur:
        cur.execute("""
            SELECT c.name AS category,p.currency,AVG(p.current_price) AS avg_price
            FROM products p
            JOIN categories c ON c.id=p.category_id
            WHERE p.is_active=TRUE AND p.current_price IS NOT NULL
            GROUP BY c.name,p.currency
        """)
        averages={(r["category"],r["currency"]):float(r["avg_price"] or 0) for r in cur.fetchall()}
        cur.execute("""
            SELECT p.id,c.name AS category,p.currency
            FROM products p
            LEFT JOIN categories c ON c.id=p.category_id
            WHERE p.is_active=TRUE AND p.current_price IS NOT NULL
        """)
        rows=cur.fetchall()
    for row in rows:
        try:
            score_product(conn,row["id"],averages.get((row["category"],row["currency"]),0))
        except Exception as exc:
            log(f"score {row['id']}: {exc}")


def audit(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM products WHERE is_active=TRUE")
        active=cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM products WHERE is_active=TRUE AND (image_url IS NOT NULL OR jsonb_array_length(COALESCE(image_gallery,'[]'::jsonb))>0)")
        images=cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM published_cards WHERE is_published=TRUE")
        published=cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM products p JOIN platforms pl ON pl.id=p.platform_id WHERE p.is_active=TRUE AND pl.name='amazon'")
        amazon=cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM products p JOIN platforms pl ON pl.id=p.platform_id WHERE p.is_active=TRUE AND pl.name='mercadolibre'")
        meli=cur.fetchone()["n"]
    return active,images,published,amazon,meli


def main():
    load_env()
    database_url=os.getenv("DATABASE_URL","").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL no esta configurada. Usa la URL de PostgreSQL de Railway.")

    from db import get_connection
    from init_db import init_database

    log("inicializando base de datos")
    init_database()
    conn=get_connection()
    try:
        log("actualizando catalogo desde Mercado Libre")
        meli=ingest_marketplaces(conn)
        log(f"Mercado Libre: {meli} productos procesados")

        log("intentando recuperar metadatos de Amazon existentes")
        amazon_refresh=refresh_amazon_metadata(conn)
        log(f"Amazon: {amazon_refresh} productos actualizados")

        score_catalog(conn)
        published=publish_products(conn)
        log(f"publicaciones creadas/actualizadas: {published}")

        active,images,published,amazon,meli=audit(conn)
        print("")
        print("=== RESULTADO ===")
        print(f"Activos: {active}")
        print(f"Con imagen: {images}")
        print(f"Publicados: {published}")
        print(f"Amazon: {amazon}")
        print(f"Mercado Libre: {meli}")
        if active == 0:
            raise SystemExit("No se pudo recuperar ningun producto. Revisa la conectividad/API de Mercado Libre.")
        if published == 0:
            raise SystemExit("No hay publicaciones activas.")
        log("reparacion terminada")
    finally:
        conn.close()


if __name__=="__main__":
    main()
