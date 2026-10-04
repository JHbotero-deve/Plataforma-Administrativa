from db import get_connection, upsert_product

AMAZON_PRODUCTS = [
    ("accesorios","B09HM94VDS","Logitech MX Master 3S Mouse"),
    ("accesorios","B0CFZK8M2S","Fitbit Charge 6 Fitness Tracker"),
    ("accesorios","B09YH79VBC","Sony WH-1000XM5 Headphones"),
    ("accesorios","B0BPC34J3D","LEGO Technic Ferrari Daytona SP3"),
    ("hogar","B07KXH57QJ","KitchenAid Artisan Stand Mixer"),
    ("electronica","B09VLHR4JC","Samsung T7 Shield Portable SSD 2TB"),
    ("electronica","B09NCMZNJK","Nintendo Switch OLED Model Mario Red"),
    ("electronica","B09RQ47HC8","JBL Flip 6 Portable Waterproof Speaker"),
    ("electronica","B0CJM1GNFQ","Amazon Fire TV Stick 4K"),
    ("accesorios","B0GJTFXNRX","Apple AirTag 2nd Generation"),
    ("accesorios","B0GJTXVN9Z","Apple AirTag 2nd Generation 4 Pack"),
    ("accesorios","B0D5M2KP2Z","Anker 737 Power Bank"),
    ("electronica","B0CN5HYDHZ","Soundcore Motion+ / Motion X500 Bluetooth Speaker"),
    ("electronica","B0C3ZR6JJL","Soundcore Life Note 3i True Wireless Earbuds"),
    ("hogar","B0BHZQGN26","Samsung T7 Shield Portable SSD 4TB"),
]

MERCADOLIBRE_PRODUCTS = [
    ("accesorios","MCO-SEARCH-SOPORTE-LINKON","Soporte para Celular Linkon 100% Aluminio Antideslizante","https://listado.mercadolibre.com.co/soporte-de-celular",41990),
    ("accesorios","MCO-SEARCH-SOPORTE-AUTO","Soporte Celular para Auto Rejilla Holder Ajustable","https://listado.mercadolibre.com.co/soporte-de-celular",18081),
    ("electronica","MCO-SEARCH-SONY-CH720N","Audífonos Sony WH-CH720N Noise Cancelling","https://listado.mercadolibre.com.co/audifonos-con-bluetooth",319899),
    ("electronica","MCO-SEARCH-REDRAGON-K552","Teclado Gamer Redragon Kumara K552 RGB","https://listado.mercadolibre.com.co/teclado-mecanico",259900),
    ("electronica","MCO-SEARCH-REDMI-BUDS-6","Auriculares Xiaomi Redmi Buds 6 Play","https://listado.mercadolibre.com.co/audifonos-de-bluetooth",54900),
]

PERSONAL_PRODUCTS = [
    ("otros","CENTRAL-PROP-001","Audífonos Urbanos Central","https://images.unsplash.com/photo-1546435770-a3e426bf472b?auto=format&fit=crop&w=900&q=85",129900),
    ("otros","CENTRAL-PROP-002","Reloj Minimal Central","https://images.unsplash.com/photo-1523275335684-37898b6baf30?auto=format&fit=crop&w=900&q=85",159900),
    ("otros","CENTRAL-PROP-003","Smartphone Central","https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?auto=format&fit=crop&w=900&q=85",699900),
]

def _publish(conn, product_id, title, subtitle, price_display, image_url, product_url, score=None):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO published_cards
              (product_id,title,subtitle,price_display,image_url,product_url,
               opportunity_score,footer,accent,is_published,published_at,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'Disponible en Central','#b6f23a',TRUE,NOW(),NOW())
            ON CONFLICT (product_id) DO UPDATE SET
              title=EXCLUDED.title, subtitle=EXCLUDED.subtitle,
              price_display=EXCLUDED.price_display, image_url=EXCLUDED.image_url,
              product_url=EXCLUDED.product_url, opportunity_score=EXCLUDED.opportunity_score,
              is_published=TRUE, updated_at=NOW()
        """,(product_id,title,subtitle,price_display,image_url,product_url,score))
    conn.commit()

def seed_catalog():
    conn = get_connection()
    seeded = {"amazon":0,"mercadolibre":0,"personal":0}
    try:
        for category, asin, title in AMAZON_PRODUCTS:
            image=f"https://m.media-amazon.com/images/P/{asin}.01._SL1000_.jpg"
            product={"external_id":asin,"title":title,"image_url":image,
                     "gallery_urls":[image],"product_url":f"https://www.amazon.com/dp/{asin}",
                     "price":None,"currency":"USD","rating":None,"reviews_count":0,
                     "source_metadata":{"import_method":"verified_catalog_seed","metadata_source":"public_amazon_listing"}}
            pid=upsert_product(conn,"amazon",category,product)
            _publish(conn,pid,title,"Producto real de Amazon","Consultar",image,product["product_url"])
            seeded["amazon"]+=1

        for category, external_id, title, url, price in MERCADOLIBRE_PRODUCTS:
            product={"external_id":external_id,"title":title,"image_url":None,"gallery_urls":[],
                     "product_url":url,"price":price,"currency":"COP","rating":None,"reviews_count":0,
                     "source_metadata":{"import_method":"verified_marketplace_seed","metadata_source":"public_mercadolibre_search"}}
            pid=upsert_product(conn,"mercadolibre",category,product)
            _publish(conn,pid,title,"Oferta encontrada en Mercado Libre",f"{price:,.0f} COP",None,url)
            seeded["mercadolibre"]+=1

        for category, external_id, title, image, price in PERSONAL_PRODUCTS:
            product={"external_id":external_id,"title":title,"image_url":image,"gallery_urls":[image],
                     "product_url":None,"price":price,"currency":"COP","rating":5,"reviews_count":0,
                     "source_metadata":{"import_method":"central_seed","metadata_source":"central"}}
            pid=upsert_product(conn,"personal",category,product)
            _publish(conn,pid,title,"Producto propio de Central",f"{price:,.0f} COP",image,None)
            seeded["personal"]+=1
        return seeded
    finally:
        conn.close()
