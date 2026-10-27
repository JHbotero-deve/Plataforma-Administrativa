from db import get_connection, upsert_product

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
    seeded = {"amazon":0,"aliexpress":0,"personal":0}
    try:
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
