import unittest

from analysis import compute_demand_score, compute_price_score


class OpportunityScoreTests(unittest.TestCase):
    def test_verified_discount_improves_market_price_score(self):
        market_score = compute_price_score(80, 100)
        discount_score = compute_price_score(80, 100, 100)
        self.assertGreater(discount_score, market_score)
        self.assertEqual(discount_score, 66.4)

    def test_missing_demand_data_uses_neutral_score(self):
        self.assertEqual(compute_demand_score(None, 0, None), 50.0)

    def test_real_demand_data_is_scored(self):
        self.assertEqual(compute_demand_score(5, 150, 300), 100.0)


    def test_score_ignores_legacy_marketplace_metrics_without_provenance(self):
        from unittest.mock import MagicMock
        from analysis import score_product
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {
            "current_price": 20,
            "previous_price": 40,
            "rating": 4.8,
            "reviews_count": 128,
            "sales_estimate": 450,
            "source_metadata": {"metadata_source": "legacy_marketplace_api"},
        }
        cursor.fetchall.return_value = []
        score_product(connection, 7, 20)
        params = cursor.execute.call_args.args[1]
        self.assertEqual(params[1], 50.0)
        self.assertEqual(params[2], 50.0)

    def test_score_uses_rating_reviews_and_sales_only_when_marked_as_verified(self):
        from unittest.mock import MagicMock
        from analysis import score_product
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {
            "current_price": 20,
            "previous_price": None,
            "rating": 5,
            "reviews_count": 150,
            "sales_estimate": 300,
            "source_metadata": {"field_sources": {
                "rating": "public_product_page",
                "reviews_count": "public_product_page",
                "sales_estimate": "user",
            }},
        }
        cursor.fetchall.return_value = []
        score_product(connection, 7, 20)
        params = cursor.execute.call_args.args[1]
        self.assertEqual(params[2], 100.0)


if __name__ == "__main__":
    unittest.main()
