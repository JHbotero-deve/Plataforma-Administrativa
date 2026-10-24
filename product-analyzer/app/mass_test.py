from app.db import products_collection
from app.ingest import safe_ingest_product, purge_expired_products

def run_mass_insertion():
    purge_expired_products()
    
    sample_products = [
        {
            "external_id": "AMZ-101",
            "platform": "amazon",
            "title": "Echo Dot (5th Gen) Smart Speaker",
            "price": 24.99,
            "original_price": 59.99,
            "discount_percentage": 58.3,
            "image_url": "https://images.amazon.com/images/I/101.jpg"
        },
        {
            "external_id": "AMZ-102",
            "platform": "amazon",
            "title": "Kindle Paperwhite (16 GB)",
            "price": 69.99,
            "original_price": 149.99,
            "discount_percentage": 53.3,
            "image_url": "https://images.amazon.com/images/I/102.jpg"
        },
        {
            "external_id": "MELI-201",
            "platform": "mercadolibre",
            "title": "Audífonos Inalámbricos Bluetooth JBL",
            "price": 45000.0,
            "original_price": 110000.0,
            "discount_percentage": 59.0,
            "image_url": "https://http2.mlstatic.com/D_201.jpg"
        },
        {
            "external_id": "MELI-202",
            "platform": "mercadolibre",
            "title": "Smartwatch Reloj Inteligente Deportivo",
            "price": 35000.0,
            "original_price": 85000.0,
            "discount_percentage": 58.8,
            "image_url": "https://http2.mlstatic.com/D_202.jpg"
        },
        {
            "external_id": "ALI-301",
            "platform": "aliexpress",
            "title": "Proyector Portátil Mini LED 1080P",
            "price": 18.50,
            "original_price": 49.99,
            "discount_percentage": 63.0,
            "image_url": "https://ae01.alicdn.com/kf/301.jpg"
        },
        {
            "external_id": "ALI-302",
            "platform": "aliexpress",
            "title": "Consola Retro Portátil de Juegos",
            "price": 15.00,
            "original_price": 39.99,
            "discount_percentage": 62.5,
            "image_url": "https://ae01.alicdn.com/kf/302.jpg"
        },
        {
            "external_id": "AMZ-999",
            "platform": "amazon",
            "title": "Cargador USB Genérico (30% off - INVÁLIDO)",
            "price": 14.00,
            "original_price": 20.00,
            "discount_percentage": 30.0,
            "image_url": "https://images.amazon.com/images/I/999.jpg"
        }
    ]

    print("--- INICIANDO PRUEBA DE INGESTA MASIVA MULTIPLATAFORMA ---")
    success_count = 0
    rejected_count = 0

    for prod in sample_products:
        res = safe_ingest_product(prod)
        if res:
            success_count += 1
            print(f"[ACEPTADO] [{prod['platform'].upper()}] {prod['title']} ({prod['discount_percentage']}%)")
        else:
            rejected_count += 1
            print(f"[RECHAZADO] [{prod['platform'].upper()}] {prod['title']} ({prod['discount_percentage']}%) -> Filtro <50%")

    total_in_db = products_collection.count_documents({})
    print(f"\nResumen: {success_count} guardados, {rejected_count} rechazados.")
    print(f"Total registros activos en MongoDB: {total_in_db}")

if __name__ == "__main__":
    run_mass_insertion()
