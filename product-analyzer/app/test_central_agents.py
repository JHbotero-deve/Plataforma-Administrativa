import unittest
from central_agents import audit_catalog, build_ad_copy


class CentralAgentTests(unittest.TestCase):
    def test_ad_copy_uses_verified_discount_only_when_previous_price_is_higher(self):
        draft = build_ad_copy({
            "title": "Audífonos Bluetooth",
            "platform": "amazon",
            "current_price": 80,
            "previous_price": 100,
            "currency": "USD",
            "product_url": "https://www.amazon.com/dp/example",
        })
        self.assertIn("ahorra hasta 20%", draft["headline"])
        self.assertTrue(draft["discount_verified"])
        self.assertIn("https://www.amazon.com/dp/example", draft["cta"])
        self.assertTrue(draft["seo_title"])
        self.assertLessEqual(len(draft["meta_description"]), 155)
        self.assertIn("audífonos", draft["keywords"])
        self.assertTrue(draft["social_caption"])
        self.assertFalse(draft.get("approved_for_publication", False))

    def test_ad_copy_does_not_invent_discount_without_reference_price(self):
        draft = build_ad_copy({
            "title": "Teclado mecánico",
            "platform": "personal",
            "current_price": 150,
            "currency": "COP",
            "product_url": "https://shop.example.com/teclado",
        })
        self.assertNotIn("%", draft["headline"])
        self.assertFalse(draft["discount_verified"])
        self.assertIn("150.00 COP", draft["body"])

    def test_ad_copy_does_not_emit_non_http_cta(self):
        draft = build_ad_copy({"title": "Producto", "product_url": "javascript:alert(1)"})
        self.assertNotIn("javascript:", draft["cta"])
        self.assertIn("Revisa los detalles", draft["cta"])

    def test_catalog_audit_is_read_only_and_reports_missing_fields(self):
        class Cursor:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def execute(self, *_args):
                return None
            def fetchall(self):
                return [{
                    "id": 7,
                    "title": "Producto sin precio",
                    "current_price": None,
                    "previous_price": None,
                    "currency": "COP",
                    "image_url": "",
                    "product_url": "",
                    "affiliate_url": "",
                    "is_active": True,
                    "is_blocked": False,
                    "catalog_expires_at": None,
                    "platform": "personal",
                    "published": False,
                }]
        class Connection:
            def cursor(self):
                return Cursor()

        report = audit_catalog(Connection())
        self.assertEqual(report["audited"], 1)
        self.assertEqual(report["with_issues"], 1)
        self.assertFalse(report["mutated_data"])
        self.assertIn("price_missing_or_invalid", report["issues"][0]["issues"])
        self.assertIn("image_missing", report["issues"][0]["issues"])
        self.assertIn("product_url_invalid", report["issues"][0]["issues"])


if __name__ == "__main__":
    unittest.main()
