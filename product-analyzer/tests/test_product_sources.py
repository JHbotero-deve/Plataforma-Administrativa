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


if __name__ == "__main__":
    unittest.main()
