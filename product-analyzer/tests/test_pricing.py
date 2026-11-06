import unittest
from unittest.mock import MagicMock

from pricing import build_customer_offer, publish_catalog_product, publish_discounted_offer


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

    def test_admin_exclusion_prevents_automatic_republication(self):
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.fetchone.return_value = {
            "storefront_excluded": True
        }
        product = {"current_price": 50, "previous_price": 100, "title": "Bolso", "image_url": "https://img.example/bolso.jpg"}

        offer = publish_discounted_offer(connection, 10, product, 80)

        self.assertIsNone(offer)
        query = connection.cursor.return_value.__enter__.return_value.execute
        self.assertIn("SELECT storefront_excluded", query.call_args.args[0])
        self.assertEqual(query.call_count, 1)
        connection.commit.assert_not_called()

    def test_unpublishes_products_without_qualifying_margin(self):
        connection = MagicMock()
        offer = publish_discounted_offer(connection, 10, {"current_price": 70, "previous_price": 100, "title": "Bolso", "image_url": "https://img.example/bolso.jpg"})
        self.assertIsNone(offer)
        query = connection.cursor.return_value.__enter__.return_value.execute.call_args
        self.assertIn("is_published = FALSE", query.args[0])
        connection.commit.assert_called_once()


    def test_publishes_complete_catalog_product_at_source_price_without_fake_margin(self):
        connection = MagicMock()
        product = {
            "title": "Tenis deportivos",
            "current_price": 49.99,
            "currency": "USD",
            "image_url": "https://images.example/tenis.jpg",
            "product_url": "https://www.amazon.com/dp/B012345678",
            "platform": "amazon",
        }
        result = publish_catalog_product(connection, 11, product, 72)
        calls = connection.cursor.return_value.__enter__.return_value.execute.call_args_list
        insert = next(call for call in calls if "INSERT INTO published_cards" in call.args[0])
        self.assertEqual(insert.args[1][6], 49.99)
        self.assertIn("NULL,NULL,NULL", insert.args[0].replace(" ", ""))
        self.assertEqual(insert.args[1][7], 72)
        self.assertEqual(insert.args[1][8], "Precio de referencia del comercio · compra externa")
        self.assertEqual(insert.args[1][9], "#b6f23a")
        self.assertEqual(result["publication_type"], "catalog")
        connection.commit.assert_called_once()

    def test_does_not_publish_catalog_product_without_real_image_or_price(self):
        connection = MagicMock()
        result = publish_catalog_product(connection, 12, {
            "title": "Producto Amazon B012345678",
            "current_price": None,
            "image_url": None,
            "product_url": "https://www.amazon.com/dp/B012345678",
        })
        self.assertIsNone(result)
        connection.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
