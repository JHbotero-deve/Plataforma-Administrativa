import os
import unittest
from unittest.mock import MagicMock, patch

import product_sources


class ProductSourcesTests(unittest.TestCase):
    def test_amazon_item_normalizes_title_image_and_offer(self):
        item = {
            "asin": "B012345678",
            "detailPageURL": "https://www.amazon.com/dp/B012345678",
            "itemInfo": {"title": {"displayValue": "Tenis deportivos"}},
            "images": {"primary": {"large": {"url": "https://images.example/item.jpg"}}},
            "offersV2": {"listings": [{"price": {"money": {"amount": "49.99", "currency": "USD"}}}]},
        }
        product = product_sources._amazon_product(item, "calzado")
        self.assertEqual(product["title"], "Tenis deportivos")
        self.assertEqual(product["price"], 49.99)
        self.assertEqual(product["currency"], "USD")
        self.assertEqual(product["image_url"], "https://images.example/item.jpg")
        self.assertEqual(product["category"], "calzado")

    def test_aliexpress_public_jsonld_extracts_product_metadata(self):
        response = MagicMock()
        response.status_code = 200
        response.text = '''<html><script type="application/ld+json">{"@type":"Product","name":"Bolso de viaje","image":"https://img.alicdn.com/bolso.jpg","offers":{"price":"25.50","priceCurrency":"USD"}}</script></html>'''
        with patch.object(product_sources.requests, "get", return_value=response):
            result = product_sources._public_aliexpress_page("https://www.aliexpress.com/item/1005001234567890.html")
        self.assertEqual(result["title"], "Bolso de viaje")
        self.assertEqual(result["price"], 25.5)
        self.assertEqual(result["currency"], "USD")
        self.assertEqual(result["image_url"], "https://img.alicdn.com/bolso.jpg")

    def test_aliexpress_metadata_rejects_untrusted_host(self):
        with self.assertRaises(ValueError):
            product_sources._public_aliexpress_page("https://evil.example/product")

    def test_amazon_credentials_are_read_with_whitespace_normalization(self):
        with patch.dict(os.environ, {" AMAZON_CREDENTIAL_ID ": "id", "AMAZON_CREDENTIAL_SECRET": "secret", " AMAZON_CREDENTIAL_VERSION": "3.1", "AMAZON_PARTNER_TAG": "central-20"}, clear=True):
            config = product_sources._amazon_config()
        self.assertEqual(config[0], "id")
        self.assertEqual(config[3], "central-20")


if __name__ == "__main__":
    unittest.main()
