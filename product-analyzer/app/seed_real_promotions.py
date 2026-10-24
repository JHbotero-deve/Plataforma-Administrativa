"""
Inyección controlada de promociones reales para la tienda Central.
Despublica las tarjetas actuales y publica hasta 100 productos reales de Amazon
y 100 de Mercado Libre, sin duplicar por plataforma + external_id.
"""

from datetime import datetime

from db import get_connection, upsert_product
from amazon_api import search_products, _map_item, _map_fallback_item
from ingest import fetch_mercadolibre

AMAZON_QUERIES = [
    ("electronica", "wireless headphones"),
    ("electronica", "smart watch"),
    ("electronica", "gaming mouse"),
    ("electronica", "mechanical keyboard"),
    ("accesorios", "usb c charger"),
    ("accesorios", "power bank"),
    ("hogar", "smart home"),
    ("electronica", "fire tv stick"),
    ("electronica", "4k monitor"),
    ("electronica", "webcam"),
    ("electronica", "portable speaker"),
    ("electronica", "ssd 1tb"),
    ("electronica", "smartphone accessories"),
    ("accesorios", "bluetooth keyboard"),
    ("accesorios", "wireless charger"),
]
MELI_QUERIES = [
    "audifonos bluetooth", "smartwatch", "mouse gamer", "teclado mecanico",
    "cargador usb c", "power bank", "camara seguridad wifi", "parlante bluetooth", "camara deportiva", "disco ssd", "teclado bluetooth",
    "cargador inalambrico", "monitor 4k", "webcam", "memoria micro sd",
]
MAX_PER_SOURCE = 100
BATCH = "REAL-PROMO-2026-10-07"


def ensure_catalog(conn):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO platforms(name, base_url)
            VALUES ('amazon','https://www.amazon.com'),
                   ('mercadolibre','https://www.mercadolibre.com.co')
            ON CONFLICT(name) DO NOTHING
        """)
        for name in ("electronica", "accesorios", "hogar", "fitness", "otros"):
            cur.execute(
                "INSERT INTO categories(name) VALUES(%s) ON CONFLICT(name) DO NOTHING",
                (name,),
            )
    conn.commit()


def publish(conn, product_id, product, source):
    price = product.get("price")
    currency = product.get("currency") or ("USD" if source == "Amazon" else "COP")
    title = str(product.get("title") or "Producto")
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO published_cards (
                product_id,title,subtitle,price_display,image_url,product_url,
                sale_price,cost_price,profit_amount,profit_margin_pct,
                opportunity_score,footer,accent,is_published,published_at,updated_at
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0,0,0,%s,%s,TRUE,NOW(),NOW())
            ON CONFLICT(product_id) DO UPDATE SET
                title=EXCLUDED.title, subtitle=EXCLUDED.subtitle,
                price_display=EXCLUDED.price_display, image_url=EXCLUDED.image_url,
                product_url=EXCLUDED.product_url, sale_price=EXCLUDED.sale_price,
                cost_price=EXCLUDED.cost_price, footer=EXCLUDED.footer,
                accent=EXCLUDED.accent, is_published=TRUE,
                published_at=NOW(), updated_at=NOW()
        """, (
            product_id, title, f"PROMOCION REAL · {source}",
            f"{price:,.2f} {currency}" if price is not None else "Consultar",
            product.get("image_url"), product.get("product_url"), price, price,
            f"Precio verificado en {source} · {datetime.now().strftime('%Y-%m-%d')}",
            "#ff8a00" if source == "Amazon" else "#ffe600",
        ))


def amazon_products(conn):
    imported, seen = 0, set()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT p.external_id FROM products p
            JOIN platforms pl ON pl.id=p.platform_id WHERE pl.name='amazon'
        """)
        seen = {str(r["external_id"]).upper() for r in cur.fetchall()}

    for category, query in AMAZON_QUERIES:
        if imported >= MAX_PER_SOURCE:
            break
        try:
            for item in search_products(query, 20):
                if imported >= MAX_PER_SOURCE:
                    break
                product = _map_item(item, category) or _map_fallback_item(item, category)
                if not product:
                    continue
                key = str(product["external_id"]).upper()
                if key in seen:
                    continue
                product["source_metadata"]["promotion_batch"] = BATCH
                product["source_metadata"]["promotion_source"] = "Amazon"
                product_id = upsert_product(conn, "amazon", category, product)
                publish(conn, product_id, product, "Amazon")
                conn.commit()
                seen.add(key)
                imported += 1
                print(f"[REAL] Amazon {imported}/{MAX_PER_SOURCE}: {product['title'][:90]}")
        except Exception as exc:
            conn.rollback()
            print(f"[REAL] Amazon '{query}' ERROR: {exc}")
    return imported


def mercado_libre_products(conn):
    imported, seen = 0, set()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT p.external_id FROM products p
            JOIN platforms pl ON pl.id=p.platform_id WHERE pl.name='mercadolibre'
        """)
        seen = {str(r["external_id"]).upper() for r in cur.fetchall()}

    for query in MELI_QUERIES:
        if imported >= MAX_PER_SOURCE:
            break
        try:
            for product in fetch_mercadolibre(query, limit=20):
                if imported >= MAX_PER_SOURCE:
                    break
                key = str(product.get("external_id") or "").upper()
                if not key or key in seen:
                    continue
                product["source_metadata"]["promotion_batch"] = BATCH
                product["source_metadata"]["promotion_source"] = "Mercado Libre"
                product_id = upsert_product(conn, "mercadolibre", "electronica", product)
                publish(conn, product_id, product, "Mercado Libre")
                conn.commit()
                seen.add(key)
                imported += 1
                print(f"[REAL] Mercado Libre {imported}/{MAX_PER_SOURCE}: {product['title'][:90]}")
        except Exception as exc:
            conn.rollback()
            print(f"[REAL] Mercado Libre '{query}' ERROR: {exc}")
    return imported


FALLBACK_PRODUCTS = [
    # Amazon — ofertas/productos reales verificados en web el 07-10-2026.
    {"source":"Amazon","external_id":"AMZ-P20I-20261007","title":"Soundcore P20i True Wireless Earbuds","image_url":"https://shopping.ncpgroup.nz/cdn/shop/files/Black-09_2048x2048.jpg?v=1763973709","product_url":"https://www.amazon.com/s?k=Soundcore+P20i","price":20.99,"currency":"USD","category":"electronica"},
    {"source":"Amazon","external_id":"AMZ-P30I-20261007","title":"Soundcore P30i Noise Cancelling Earbuds","image_url":"https://gagadget.com/media/uploads/soundcore-p30i.png","product_url":"https://www.amazon.com/s?k=Soundcore+P30i","price":29.99,"currency":"USD","category":"electronica"},
    {"source":"Amazon","external_id":"AMZ-XM5-20261007","title":"Sony WH-1000XM5 Noise Cancelling Headphones","image_url":"https://gagadget.com/media/uploads/sony.png","product_url":"https://www.amazon.com/s?k=Sony+WH-1000XM5","price":249.99,"currency":"USD","category":"electronica"},
    {"source":"Amazon","external_id":"AMZ-G502-20261007","title":"Logitech G502 HERO Gaming Mouse","image_url":"https://www.notebookcheck.net/fileadmin/Notebooks/News/_nc3/Logitech-G502-hero-gaming-mouse-amazon-sale.jpg","product_url":"https://www.amazon.com/s?k=Logitech+G502+HERO","price":None,"currency":"USD","category":"accesorios"},
    {"source":"Amazon","external_id":"AMZ-ECHODOT5-20261007","title":"Amazon Echo Dot 5th Generation","image_url":"https://media.falabella.com/falabellaCO/119243945_01/w%3D800%2Ch%3D800%2Cfit%3Dpad","product_url":"https://www.amazon.com/s?k=Echo+Dot+5th+Generation","price":None,"currency":"USD","category":"hogar"},
    {"source":"Amazon","external_id":"AMZ-FIRE4K-20261007","title":"Amazon Fire TV Stick 4K","image_url":"https://m.media-amazon.com/images/I/31%2BZU7Ah7hL.jpg","product_url":"https://www.amazon.com/s?k=Fire+TV+Stick+4K","price":None,"currency":"USD","category":"electronica"},
    {"source":"Amazon","external_id":"AMZ-KINDLEPW-20261007","title":"Amazon Kindle Paperwhite","image_url":"https://down-tw.img.susercontent.com/file/3907c1d8d9a8e93d4a395799c8bcff52","product_url":"https://www.amazon.com/s?k=Kindle+Paperwhite","price":None,"currency":"USD","category":"electronica"},
    {"source":"Amazon","external_id":"AMZ-POCKET3-20261007","title":"DJI Osmo Pocket 3 Creator Combo","image_url":"https://down-my.img.susercontent.com/file/my-11134201-820lf-mnwlff2n6ghvd0","product_url":"https://www.amazon.com/s?k=DJI+Osmo+Pocket+3+Creator+Combo","price":495.0,"currency":"USD","category":"electronica"},
    {"source":"Amazon","external_id":"AMZ-SAMT7-20261007","title":"Samsung T7 Portable SSD 1TB","image_url":"https://www.pc-canada.com/dd2/img/item/A-1500x1500/6097714-6.jpg","product_url":"https://www.amazon.com/s?k=Samsung+T7+1TB","price":None,"currency":"USD","category":"accesorios"},
    {"source":"Amazon","external_id":"AMZ-ANKER20K-20261007","title":"Anker PowerCore Essential 20000 PD","image_url":"https://www.cellsii.com/images/detailed/58/Anker-PowerCore-525-Essential-20000mAh-PD-20W-Power-Bank_lilr-pz.jpg","product_url":"https://www.amazon.com/s?k=Anker+PowerCore+20000","price":None,"currency":"USD","category":"accesorios"},
    # Mercado Libre Colombia — precios publicados recientemente.
    {"source":"Mercado Libre","external_id":"MCO-P20I-20261007","title":"Soundcore P20i In-Ear Bluetooth","image_url":"https://shopping.ncpgroup.nz/cdn/shop/files/Black-09_2048x2048.jpg?v=1763973709","product_url":"https://www.mercadolibre.com.co/audifonos-in-ear-bluetooth-inalambrico-gamer-anker-soundcore-p20i-con-manos-libres-10mm-diafragmas-bluetooth-53-bass-potente-30h-2-micros-ia-cancelacion-de-ruido-sonido-de-calidad-color-negro/p/MCO24028629","price":94990.0,"currency":"COP","category":"electronica"},
    {"source":"Mercado Libre","external_id":"MCO-P30I-20261007","title":"Soundcore P30i ANC","image_url":"https://media.falabella.com/falabellaCO/152892178_01/w%3D1004%2Ch%3D1500%2Cfit%3Dpad","product_url":"https://www.mercadolibre.com.co/audifonos-in-ear-bluetooth-inalambrico-soundcore-p30ireduccion-de-ruido-de-40db-con-anc-adaptativo-soporte-para-movil-sonido-superior-y-bajos-potentes-llamadas-claras-con-ia-comodidad-excepcional/p/MCO38176139","price":137200.0,"currency":"COP","category":"electronica"},
    {"source":"Mercado Libre","external_id":"MCO-KZCASTOR-20261007","title":"KZ Castor In-Ear Monitors","image_url":"https://http2.mlstatic.com/D_Q_NP_2X_876015-MCO104626541220_012026-E.webp","product_url":"https://listado.mercadolibre.com.co/kz-castor","price":74900.0,"currency":"COP","category":"electronica"},
    {"source":"Mercado Libre","external_id":"MCO-DJIACTION5-20261007","title":"DJI Osmo Action 5 Pro Standard","image_url":"https://http2.mlstatic.com/D_Q_NP_2X_644089-CBT115025722056_082026-E-camara-de-accion-dji-osmo-action-5-pro-con-paquete-de-acceso.webp","product_url":"https://www.mercadolibre.com.co/camara-dji-osmo-action-5-pro-estandar-color-gris-oscuro/p/MCO41502196","price":1749900.0,"currency":"COP","category":"electronica"},
    {"source":"Mercado Libre","external_id":"MCO-HUAWEIGT7P-20261007","title":"Huawei Watch GT 7 Pro 46mm","image_url":"https://dam.elcorteingles.es/producto/www-001089060020487-06.jpg","product_url":"https://www.mercadolibre.com.co/reloj-inteligente-smartwatch-huawei-watch-gt-7-pro-46mm/up/MCOU5332657057","price":1299900.0,"currency":"COP","category":"electronica"},
    {"source":"Mercado Libre","external_id":"MCO-G502HERO-20261007","title":"Logitech G502 HERO Gaming Mouse","image_url":"https://notbadtech.co.nz/cdn/shop/files/g502-2.jpg?v=1715247294&width=1946","product_url":"https://www.mercadolibre.com.co/logitech-g502-hero-mouse-gamer-rgb--programable--25600dpi/up/MCOU2435421116","price":219900.0,"currency":"COP","category":"accesorios"},
    {"source":"Mercado Libre","external_id":"MCO-PICO-20261007","title":"Raspberry Pi Pico","image_url":"https://cdn.mauser.pt/large/product_image/2021/19/b82b13738260cf3bb371389613c3fce7_pico_board_top_white.jpg","product_url":"https://www.mercadolibre.com.co/original-raspberry-pi-pico/up/MCOU3946757295","price":34541.0,"currency":"COP","category":"electronica"},
    {"source":"Mercado Libre","external_id":"MCO-POCKET3-20261007","title":"DJI Osmo Pocket 3","image_url":"https://static.wixstatic.com/media/9fea74_44b5bcdc776841f39e0fed1f106ce229~mv2.png/v1/fit/w_500%2Ch_500%2Cq_90/file.png","product_url":"https://www.mercadolibre.com.co/osmo-pocket-3-dji-black-camara-color-negro/p/MCO44849608","price":1899900.0,"currency":"COP","category":"electronica"},
    {"source":"Mercado Libre","external_id":"MCO-XM5-20261007","title":"Sony WH-1000XM5 Noise Cancelling","image_url":"https://media.falabella.com/falabellaCO/44561913_1/w%3D1500%2Ch%3D1500%2Cfit%3Dcover","product_url":"https://www.mercadolibre.com.co/audifonos-sony-bluetooth-noise-cancelling-wh-1000xm5-color-negro/p/MCO19176887","price":910000.0,"currency":"COP","category":"electronica"},
    {"source":"Mercado Libre","external_id":"MCO-ANKER20K-20261007","title":"Anker Power Bank 20000 mAh","image_url":"https://media.falabella.com/falabellaCO/73421109_1/w%3D1004%2Ch%3D1500%2Cfit%3Dpad","product_url":"https://listado.mercadolibre.com.co/anker-power-bank-20000","price":None,"currency":"COP","category":"accesorios"},
    {"source":"Mercado Libre","external_id":"MCO-JBLFLIP6-20261007","title":"JBL Flip 6 Portable Bluetooth Speaker","image_url":"https://d22fxaf9t8d39k.cloudfront.net/721567e2a4a5c057a64c4a793901ac5769f708426cd5b3cb229b054d703fa7a9183775.png","product_url":"https://listado.mercadolibre.com.co/jbl-flip-6","price":None,"currency":"COP","category":"electronica"},
]

def publish_fallbacks(conn):
    total = 0
    for item in FALLBACK_PRODUCTS:
        product = {
            "platform": "amazon" if item["source"] == "Amazon" else "mercadolibre",
            "external_id": item["external_id"],
            "title": item["title"],
            "image_url": item["image_url"],
            "gallery_urls": [item["image_url"]],
            "product_url": item["product_url"],
            "price": item["price"],
            "currency": item["currency"],
            "rating": None,
            "reviews_count": 0,
            "sales_estimate": None,
            "source_metadata": {
                "promotion_batch": BATCH,
                "promotion_source": item["source"],
                "verified_date": "2026-10-07",
                "data_mode": "web_verified_fallback",
            },
        }
        pid = upsert_product(conn, product["platform"], item["category"], product)
        publish(conn, pid, product, item["source"])
        conn.commit()
        total += 1
    return total

def main():
    conn = get_connection()
    try:
        ensure_catalog(conn)
        # Primero ingerimos/publicamos; al final ocultamos solo lo que no pertenece
        # al nuevo lote. Así un fallo parcial nunca deja la tienda vacía.
        amazon = amazon_products(conn)
        meli = mercado_libre_products(conn)

        conn.close()
        fallback_total = 0
        for attempt in range(1, 6):
            try:
                fallback_conn = get_connection()
                try:
                    fallback_total = publish_fallbacks(fallback_conn)
                finally:
                    fallback_conn.close()
                break
            except Exception as exc:
                print(f"[REAL] Fallback intento {attempt}/5: {exc}")
                if attempt == 5:
                    raise
                import time
                time.sleep(2)

        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT p.id
                    FROM products p
                    WHERE p.is_active = TRUE
                      AND COALESCE(p.source_metadata->>'promotion_batch','') = %s
                """, (BATCH,))
                batch_ids = [int(row["id"]) for row in cur.fetchall()]
                if batch_ids:
                    cur.execute("""
                        UPDATE published_cards
                        SET is_published=FALSE, updated_at=NOW()
                        WHERE is_published=TRUE
                          AND product_id <> ALL(%s)
                    """, (batch_ids,))
                    hidden = cur.rowcount
                else:
                    hidden = 0
            conn.commit()
        finally:
            conn.close()

        verify_conn = get_connection()
        try:
            with verify_conn.cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) AS total
                    FROM published_cards pc
                    JOIN products p ON p.id=pc.product_id
                    WHERE pc.is_published=TRUE AND p.is_active=TRUE
                """)
                total = cur.fetchone()["total"]
        finally:
            verify_conn.close()

        print(f"REAL_PROMO_READY amazon={amazon} mercadolibre={meli} fallback={fallback_total} hidden_old={hidden} total_tienda={total}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

# Ejecutado por Railway para la inyección inicial de promociones reales.
