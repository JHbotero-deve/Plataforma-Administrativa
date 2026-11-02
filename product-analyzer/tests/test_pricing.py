import unittest
from unittest.mock import MagicMock

from pricing import build_customer_offer, publish_discounted_offer


class CustomerOfferTests(unittest.TestCase):
    def test_builds_customer_price_with_forty_percent_discount(self):
        offer = build_customer_offer(50, 100)
        self.assertEqual(offer["sale_price"], 60)
        self.assertEqual(offer["discount_pct"], 40)
        self.assertEqual(offer["profit_amount"], 10)
        self.assertEqual(offer["profit_margin_pct"], 16.67)

    def test_rejects_offer_without_positive_margin(self):
        self.assertIsNone(build_customer_offer(70, 100))

    def test_rejects_missing_or_invalid_reference_price(self):
        self.assertIsNone(build_customer_offer(20, None))
        self.assertIsNone(build_customer_offer(20, 0))
        self.assertIsNone(build_customer_offer(float("nan"), 100))

    def test_publishes_offer_with_private_cost_and_margin(self):
        connection = MagicMock()
        product = {"current_price": 50, "previous_price": 100, "currency": "USD", "title": "Bolso", "image_url": "https://img.example/bolso.jpg", "product_url": "https://www.aliexpress.com/item/123.html"}
        offer = publish_discounted_offer(connection, 9, product, 82)
        query = connection.cursor.return_value.__enter__.return_value.execute.call_args
        self.assertIn("profit_margin_pct", query.args[0])
        self.assertEqual(query.args[1][6:10], (60, 50, 10, 16.67))
        self.assertEqual(offer["discount_pct"], 40)
        connection.commit.assert_called_once()

    def test_unpublishes_products_without_qualifying_margin(self):
        connection = MagicMock()
        offer = publish_discounted_offer(connection, 10, {"current_price": 70, "previous_price": 100, "title": "Bolso", "image_url": "https://img.example/bolso.jpg"})
        self.assertIsNone(offer)
        query = connection.cursor.return_value.__enter__.return_value.execute.call_args
        self.assertIn("is_published = FALSE", query.args[0])
        connection.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
