import unittest
from unittest.mock import MagicMock, patch

import product_sources
from url_import import import_url


class ProductSourcesTests(unittest.TestCase):
    def test_url_import_preserves_original_short_url_without_external_api(self):
        url = "https://amzn.to/example"
        with patch.object(product_sources, "_public_product_page") as page:
            product = import_url(url)
        page.assert_not_called()
        self.assertEqual(product["product_url"], url)
        self.assertEqual(product["source_metadata"]["original_url"], url)
        self.assertIsNone(product["price"])
        self.assertIsNone(product["image_url"])

    def test_url_import_preserves_user_supplied_metadata(self):
        product = import_url(
            "https://www.amazon.com/dp/B012345678",
            category="calzado",
            title="Zapatillas de prueba",
            price=49.90,
            image_url="https://images.example/zapatillas.jpg",
        )
        self.assertEqual(product["title"], "Zapatillas de prueba")
        self.assertEqual(product["category"], "calzado")
        self.assertEqual(product["price"], 49.90)
        self.assertEqual(product["image_url"], "https://images.example/zapatillas.jpg")
        self.assertEqual(product["source_metadata"]["missing_fields"], [])

    def test_public_jsonld_extracts_product_fields_without_marketplace_api(self):
        response = MagicMock()
        response.status_code = 200
        response.text = '''<html><meta property="og:image" content="https://images.example/tenis.jpg"><script type="application/ld+json">{"@type":"Product","name":"Tenis deportivos","description":"Tenis para correr","offers":{"price":"49.99","priceCurrency":"USD"},"aggregateRating":{"ratingValue":"4.7","reviewCount":"120"}}</script></html>'''
        with patch.object(product_sources.requests, "get", return_value=response):
            result = product_sources._public_product_page("https://www.amazon.com/dp/B012345678", "amazon")
        self.assertEqual(result["title"], "Tenis deportivos")
        self.assertEqual(result["price"], 49.99)
        self.assertEqual(result["currency"], "USD")
        self.assertEqual(result["image_url"], "https://images.example/tenis.jpg")
        self.assertEqual(result["rating"], 4.7)
        self.assertEqual(result["reviews_count"], 120)
        response.close.assert_called_once()

    def test_enrich_product_uses_public_page_and_preserves_user_values(self):
        product = {
            "platform": "amazon",
            "external_id": "URL-SHORT",
            "product_url": "https://amzn.to/example",
            "title": "Producto Amazon URL-SHORT",
            "price": 60,
            "currency": "COP",
            "image_url": None,
            "category": None,
            "source_metadata": {"original_url": "https://amzn.to/example"},
        }
        page = {
            "resolved_url": "https://www.amazon.com/dp/B012345678",
            "title": "Tenis reales",
            "price": 49.99,
            "previous_price": 80,
            "currency": "USD",
            "image_url": "https://images.example/tenis.jpg",
            "gallery_urls": ["https://images.example/tenis.jpg", "https://images.example/tenis-2.jpg"],
            "description": "Descripción verificada",
            "rating": 4.5,
            "reviews_count": 25,
            "category": "calzado",
            "seller": {"external_id": "seller-1", "name": "Tienda"},
            "seller_url": "https://www.amazon.com/store/seller-1",
        }
        with patch.object(product_sources, "_public_product_page", return_value=page) as fetch:
            result = product_sources.enrich_product(product)
        fetch.assert_called_once_with("https://amzn.to/example", "amazon")
        self.assertEqual(result["title"], "Tenis reales")
        self.assertEqual(result["price"], 60)
        self.assertEqual(result["currency"], "COP")
        self.assertEqual(result["image_url"], "https://images.example/tenis.jpg")
        self.assertEqual(result["product_url"], "https://www.amazon.com/dp/B012345678")
        self.assertEqual(result["source_metadata"]["original_url"], "https://amzn.to/example")
        self.assertEqual(result["source_metadata"]["enrichment_status"], "complete")
        self.assertNotIn("network_enrichment_disabled", result["source_metadata"])

    def test_complete_user_supplied_product_does_not_refetch_page(self):
        product = {
            "platform": "aliexpress",
            "product_url": "https://www.aliexpress.com/item/123.html",
            "title": "Tenis deportivos",
            "price": 19.9,
            "image_url": "https://img.example/shoes.jpg",
            "category": "calzado",
            "source_metadata": {"enrichment_version": product_sources.ENRICHMENT_VERSION},
        }
        with patch.object(product_sources, "_public_product_page") as fetch:
            result = product_sources.enrich_product(product)
        fetch.assert_not_called()
        self.assertEqual(result["source_metadata"]["enrichment_status"], "complete")

    def test_failed_public_page_keeps_product_partial_and_retryable(self):
        product = {
            "platform": "amazon",
            "product_url": "https://www.amazon.com/dp/B012345678",
            "title": "Producto Amazon B012345678",
            "source_metadata": {},
        }
        with patch.object(product_sources, "_public_product_page", side_effect=RuntimeError("login required")):
            result = product_sources.enrich_product(product)
        self.assertEqual(result["source_metadata"]["enrichment_status"], "partial")
        self.assertIn("login required", result["source_metadata"]["enrichment_error"])
        self.assertIn("title", result["source_metadata"]["missing_fields"])
        self.assertFalse(product_sources._retry_due(result["source_metadata"]))
        result["source_metadata"]["enrichment_retry_after"] = "2000-01-01T00:00:00+00:00"
        self.assertTrue(product_sources._retry_due(result["source_metadata"]))

    def test_login_page_is_not_treated_as_product_metadata(self):
        response = MagicMock()
        response.status_code = 200
        response.text = '<html><meta property="og:title" content="Sign in to AliExpress"></html>'
        with patch.object(product_sources.requests, "get", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "exige iniciar sesión"):
                product_sources._public_product_page("https://www.aliexpress.com/item/123.html", "aliexpress")
        response.close.assert_called_once()

    def test_short_url_redirect_is_followed_only_to_allowed_marketplace_host(self):
        first = MagicMock()
        first.status_code = 302
        first.headers = {"Location": "https://www.amazon.com/dp/B012345678"}
        second = MagicMock()
        second.status_code = 200
        second.text = '<html><meta property="og:title" content="Tenis"></html>'
        with patch.object(product_sources.requests, "get", side_effect=[first, second]) as request:
            result = product_sources._public_product_page("https://amzn.to/example", "amazon")
        self.assertEqual(result["resolved_url"], "https://www.amazon.com/dp/B012345678")
        self.assertEqual(request.call_count, 2)
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_redirect_to_untrusted_host_is_rejected(self):
        response = MagicMock()
        response.status_code = 302
        response.headers = {"Location": "https://127.0.0.1/admin"}
        with patch.object(product_sources.requests, "get", return_value=response):
            with self.assertRaisesRegex(ValueError, "fuera de los dominios permitidos"):
                product_sources._public_product_page("https://amzn.to/example", "amazon")
        response.close.assert_called_once()

    def test_public_page_rejects_untrusted_initial_host(self):
        with self.assertRaises(ValueError):
            product_sources._public_product_page("https://evil.example/product", "amazon")

    def test_category_inference_requires_identifiable_product_type(self):
        self.assertEqual(product_sources._category(None, "Men running shoes"), "calzado")
        self.assertIsNone(product_sources._category(None, "Producto nuevo"))

    def test_price_parser_handles_common_formats(self):
        self.assertEqual(product_sources._number("$1,299.00"), 1299.0)
        self.assertEqual(product_sources._number("1.299,00 €"), 1299.0)
        self.assertIsNone(product_sources._number("Consultar"))

    def test_redirect_cycle_fails_fast(self):
        first = MagicMock()
        first.status_code = 302
        first.headers = {"Location": "https://www.aliexpress.com/redirect/2"}
        second = MagicMock()
        second.status_code = 302
        second.headers = {"Location": "https://www.aliexpress.com/redirect/1"}
        third = MagicMock()
        third.status_code = 302
        third.headers = {"Location": "https://www.aliexpress.com/redirect/2"}
        with patch.object(product_sources.requests, "get", side_effect=[first, second, third]) as request:
            with self.assertRaisesRegex(RuntimeError, "ciclo de redirección"):
                product_sources._public_product_page("https://www.aliexpress.com/redirect/1", "aliexpress")
        self.assertEqual(request.call_count, 2)
        first.close.assert_called_once()
        second.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
