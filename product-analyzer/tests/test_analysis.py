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


if __name__ == "__main__":
    unittest.main()
