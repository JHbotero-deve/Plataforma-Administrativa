from app.db import SessionLocal, Product, Base, engine
from app.ingest import safe_ingest_product

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

db = SessionLocal()

print("--- PRUEBA 1: Ingesta con 30% de descuento (Debe ser rechazada) ---")
prod_invalid = {
    "external_id": "ALI-001",
    "platform": "aliexpress",
    "title": "Smartwatch Test",
    "price": 20.0,
    "original_price": 28.5,
    "discount_percentage": 30.0,
    "image_url": "https://img.test/1.jpg"
}
res1 = safe_ingest_product(db, prod_invalid)
print(f"Resultado ingesta 30%: {res1} (Esperado: False)")

print("--- PRUEBA 2: Ingesta con 60% de descuento (Debe ser aceptada) ---")
prod_valid = {
    "external_id": "ALI-002",
    "platform": "aliexpress",
    "title": "Audifonos TWS Pro",
    "price": 10.0,
    "original_price": 25.0,
    "discount_percentage": 60.0,
    "image_url": "https://img.test/2.jpg"
}
res2 = safe_ingest_product(db, prod_valid)
print(f"Resultado ingesta 60%: {res2} (Esperado: True)")

all_products = db.query(Product).all()
print(f"Total productos en BD: {len(all_products)} (Esperado: 1)")
for p in all_products:
    print(f" - [{p.platform}] {p.title} | Descuento: {p.discount_percentage}%")

db.close()
