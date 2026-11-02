import unittest
from unittest.mock import MagicMock, patch

import product_sources


class ProductSourcesTests(unittest.TestCase):
    def test_amazon_public_jsonld_extracts_product_metadata_without_api(self):
        response = MagicMock()
        response.status_code = 200
        response.text = '''<html><meta property="og:title" content="Tenis deportivos"><meta property="og:image" content="https://images.example/tenis.jpg"><script type="application/ld+json">{"@type":"Product","name":"Tenis deportivos","offers":{"price":"49.99","priceCurrency":"USD"}}</script></html>'''
        with patch.object(product_sources.requests, "get", return_value=response):
            result = product_sources._public_product_page("https://www.amazon.com/dp/B012345678", "amazon")
        self.assertEqual(result["title"], "Tenis deportivos")
        self.assertEqual(result["price"], 49.99)
        self.assertEqual(result["currency"], "USD")
        self.assertEqual(result["image_url"], "https://images.example/tenis.jpg")
        response.close.assert_called_once()

    def test_aliexpress_public_jsonld_extracts_product_metadata(self):
        response = MagicMock()
        response.status_code = 200
        response.text = '''<html><script type="application/ld+json">{"@type":"Product","name":"Bolso de viaje","image":"https://img.alicdn.com/bolso.jpg","offers":{"price":"25.50","priceCurrency":"USD"}}</script></html>'''
        with patch.object(product_sources.requests, "get", return_value=response):
            result = product_sources._public_product_page("https://www.aliexpress.com/item/1005001234567890.html", "aliexpress")
        self.assertEqual(result["title"], "Bolso de viaje")
        self.assertEqual(result["price"], 25.5)
        self.assertEqual(result["currency"], "USD")
        self.assertEqual(result["image_url"], "https://img.alicdn.com/bolso.jpg")

    def test_public_page_rejects_untrusted_host(self):
        with self.assertRaises(ValueError):
            product_sources._public_product_page("https://evil.example/product", "amazon")

    def test_short_amazon_url_resolves_only_inside_allowed_hosts(self):
        first = MagicMock()
        first.status_code = 302
        first.headers = {"Location": "https://www.amazon.com/dp/B012345678"}
        second = MagicMock()
        second.status_code = 200
        second.text = "<html><meta property='og:title' content='Producto Amazon'></html>"
        with patch.object(product_sources.requests, "get", side_effect=[first, second]):
            result = product_sources._public_product_page("https://amzn.to/example", "amazon")
        self.assertEqual(result["resolved_url"], "https://www.amazon.com/dp/B012345678")
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_aliexpress_short_link_resolves_after_more_than_six_redirects(self):
        redirects = []
        for index in range(7):
            response = MagicMock()
            response.status_code = 302
            response.headers = {"Location": f"https://www.aliexpress.com/redirect/{index}"}
            redirects.append(response)
        final = MagicMock()
        final.status_code = 200
        final.text = '''<html><script type="application/ld+json">{"@type":"Product","name":"Producto AliExpress","offers":{"price":"19.90","priceCurrency":"USD"}}</script></html>'''
        with patch.object(product_sources.requests, "get", side_effect=redirects + [final]) as request:
            result = product_sources._public_product_page("https://s.click.aliexpress.com/e/_mOOSCcP", "aliexpress")
        self.assertEqual(result["title"], "Producto AliExpress")
        self.assertEqual(result["price"], 19.9)
        self.assertEqual(request.call_count, 8)
        for response in redirects:
            response.close.assert_called_once()
        final.close.assert_called_once()

    def test_price_parser_handles_common_formats(self):
        self.assertEqual(product_sources._number("$1,299.00"), 1299.0)
        self.assertEqual(product_sources._number("1.299,00 €"), 1299.0)
        self.assertIsNone(product_sources._number("Consultar"))

    def test_redirect_cycle_fails_fast_without_repeating_login_requests(self):
        first = MagicMock()
        first.status_code = 302
        first.headers = {"Location": "https://login.aliexpress.us/check"}
        second = MagicMock()
        second.status_code = 302
        second.headers = {"Location": "https://login.aliexpress.com/check"}
        with patch.object(product_sources.requests, "get", side_effect=[first, second]) as request:
            with self.assertRaisesRegex(RuntimeError, "ciclo de redirección"):
                product_sources._public_product_page("https://s.click.aliexpress.com/e/_mOOSCcP", "aliexpress")
        self.assertEqual(request.call_count, 2)
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_category_is_only_assigned_when_metadata_identifies_one(self):
        self.assertEqual(product_sources._category("Shoes"), "calzado")
        self.assertEqual(product_sources._category("Women's clothing"), "ropa")
        self.assertEqual(product_sources._category("Phone case"), "accesorios")
        self.assertIsNone(product_sources._category("Uncategorized product"))

    def test_enrichment_failure_records_retry_without_replacing_original_url(self):
        product = {
            "platform": "aliexpress",
            "product_url": "https://s.click.aliexpress.com/e/_mOOSCcP",
            "title": "Producto AliExpress URL-123",
            "source_metadata": {},
        }
        with patch.object(product_sources, "_public_product_page", side_effect=RuntimeError("La tienda devolvió un ciclo de redirección")):
            result = product_sources.enrich_product(product)
        self.assertEqual(result["product_url"], "https://s.click.aliexpress.com/e/_mOOSCcP")
        self.assertEqual(result["source_metadata"]["enrichment_status"], "blocked")
        self.assertEqual(result["source_metadata"]["enrichment_error"], "login_or_redirect_required")
        self.assertTrue(result["source_metadata"]["enrichment_retry_after"])

    def test_enrich_pending_persists_image_price_category_and_retry_metadata(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [{
            "id": 12, "title": "Producto AliExpress URL-123", "current_price": None,
            "currency": "USD", "image_url": None, "image_gallery": [], "rating": None,
            "reviews_count": 0, "product_url": "https://www.aliexpress.com/item/123.html",
            "platform": "aliexpress", "category": None, "source_metadata": {},
        }]
        fresh = {
            "title": "Bolso de viaje", "price": 19.9, "currency": "USD",
            "image_url": "https://img.alicdn.com/bolso.jpg",
            "gallery_urls": ["https://img.alicdn.com/bolso.jpg"],
            "rating": 4.8, "reviews_count": 120, "category": "accesorios",
            "resolved_url": "https://www.aliexpress.com/item/123.html",
        }
        with patch.object(product_sources, "_public_product_page", return_value=fresh):
            recovered = product_sources.enrich_pending_products(connection, limit=1)
        self.assertEqual(recovered, 1)
        update = cursor.execute.call_args_list[-1]
        self.assertIn("image_gallery", update.args[0])
        self.assertIn("rating=COALESCE", update.args[0])
        self.assertEqual(update.args[1][0], "accesorios")
        self.assertIn("enrichment_status", update.args[1][-2])
        connection.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
