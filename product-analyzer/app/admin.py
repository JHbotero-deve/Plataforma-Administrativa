from fastapi import APIRouter, HTTPException
from app.db import products_collection

router = APIRouter(prefix="/admin", tags=["Admin"])

@router.get("/products")
def list_admin_products():
    try:
        products = list(products_collection.find({}, {"_id": 0}))
        return products
    except Exception:
        return []

@router.put("/products/{external_id}")
def update_product(external_id: str, data: dict):
    try:
        result = products_collection.update_one({"external_id": external_id}, {"$set": data})
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Producto no encontrado")
        return {"status": "success", "message": "Tarjeta corregida"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Base de datos no disponible: {str(e)}")

@router.delete("/products/{external_id}")
def delete_product(external_id: str):
    try:
        result = products_collection.delete_one({"external_id": external_id})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Producto no encontrado")
        return {"status": "success", "message": "Tarjeta eliminada"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Base de datos no disponible: {str(e)}")
