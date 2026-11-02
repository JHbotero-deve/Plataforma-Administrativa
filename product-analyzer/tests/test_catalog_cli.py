import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "importar-lote-central.py"
SPEC = importlib.util.spec_from_file_location("central_batch_cli", SCRIPT)
central_batch_cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(central_batch_cli)


class CatalogBatchCliTests(unittest.TestCase):
    def test_normalize_maps_common_export_fields(self):
        product = central_batch_cli.normalize({
            "product_url": "https://www.aliexpress.com/item/123.html",
            "item_id": "123",
            "name": "Bolso",
            "current_price": "19,90",
            "original_price": "29,90",
            "currency_code": "USD",
            "main_image": "https://img.example/bolso.jpg",
            "images": ["https://img.example/bolso-2.jpg"],
            "review_count": "120",
            "category_name": "Shoes",
            "sales": "500",
        })
        self.assertEqual(product["url"], "https://www.aliexpress.com/item/123.html")
        self.assertEqual(product["external_id"], "123")
        self.assertEqual(product["category"], "calzado")
        self.assertEqual(product["price"], 19.9)
        self.assertEqual(product["previous_price"], 29.9)
        self.assertEqual(product["reviews_count"], 120)
        self.assertEqual(product["sales_estimate"], 500)
        self.assertEqual(product["gallery_urls"], ["https://img.example/bolso.jpg", "https://img.example/bolso-2.jpg"])

    def test_normalize_rejects_rows_without_product_url(self):
        with self.assertRaisesRegex(ValueError, "falta url"):
            central_batch_cli.normalize({"title": "Sin URL"})


if __name__ == "__main__":
    unittest.main()
