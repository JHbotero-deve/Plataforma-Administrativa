import unittest
from unittest.mock import patch

from product_search import _parse_aliexpress_search_page, search_external_products
from telegram_alert import _format_product, _handle


class ProductSearchTests(unittest.TestCase):
    def test_aliexpress_parser_requires_real_product_url_title_and_price(self):
        page = '''
        <ol>
          <li class="b_algo">
            <h2><a href="https://www.aliexpress.com/item/1005001234567890.html?spm=test">Wireless Earbuds - AliExpress</a></h2>
            <div>Current price: US $12.49</div>
          </li>
          <li class="b_algo">
            <h2><a href="https://www.aliexpress.com/item/1005009999999999.html">Listing without a price</a></h2>
            <div>Free shipping</div>
          </li>
        </ol>
        '''
        results = _parse_aliexpress_search_page(page, limit=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["platform"], "aliexpress")
        self.assertEqual(results[0]["external_id"], "1005001234567890")
        self.assertEqual(results[0]["price"], 12.49)
        self.assertEqual(results[0]["currency"], "USD")
        self.assertEqual(results[0]["product_url"], "https://www.aliexpress.com/item/1005001234567890.html")

    def test_external_search_uses_only_amazon_and_aliexpress(self):
        amazon = [{
            "external_id": "B012345678", "title": "Auriculares Amazon",
            "product_url": "https://www.amazon.com/dp/B012345678", "price": 19.99, "currency": "USD",
        }]
        aliexpress = [{
            "external_id": "1005001234567890", "title": "Auriculares AliExpress",
            "product_url": "https://www.aliexpress.com/item/1005001234567890.html", "price": 8.25, "currency": "USD",
        }]
        with patch("product_search.search_amazon_products", return_value=amazon) as amazon_search, \
             patch("product_search.search_aliexpress_products", return_value=aliexpress) as ali_search:
            results = search_external_products("auriculares", limit=8)
        self.assertEqual({item["platform"] for item in results}, {"amazon", "aliexpress"})
        self.assertEqual(len(results), 2)
        amazon_search.assert_called_once()
        ali_search.assert_called_once()

    def test_duplicate_products_are_removed(self):
        product = {
            "external_id": "same", "title": "Producto repetido",
            "product_url": "https://www.aliexpress.com/item/1005001234567890.html",
            "price": 10, "currency": "USD",
        }
        with patch("product_search.search_amazon_products", return_value=[]), \
             patch("product_search.search_aliexpress_products", return_value=[product]):
            results = search_external_products("producto", limit=8)
        self.assertEqual(len(results), 1)

    def test_telegram_search_uses_external_products_instead_of_central_catalog(self):
        external_product = {
            "title": "Auriculares nuevos", "price": 12.49, "currency": "USD",
            "product_url": "https://www.aliexpress.com/item/1005001234567890.html",
            "platform": "aliexpress",
        }
        with patch("telegram_alert._search_products") as catalog_search, \
             patch("telegram_alert.search_external_products", return_value=[external_product]) as external_search, \
             patch("telegram_alert._send", return_value=True), \
             patch("telegram_alert._send_product") as send_product:
            self.assertTrue(_handle("token", "chat", "/buscar auriculares"))

        catalog_search.assert_not_called()
        external_search.assert_called_once_with("auriculares", limit=8)
        send_product.assert_called_once_with("token", "chat", external_product)
        self.assertIn("Precio: 12.49 USD", _format_product(external_product))


if __name__ == "__main__":
    unittest.main()
