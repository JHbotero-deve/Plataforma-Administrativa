import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

APP_PATH = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_PATH))

import api


class ApiCatalogQualityTests(unittest.TestCase):
    def _connection(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        return connection, cursor

    def test_products_endpoint_hides_unverified_prices_images_metrics_and_scores(self):
        connection, cursor = self._connection()
        with patch.object(api, "get_connection", return_value=connection):
            self.assertEqual(api.list_products(limit=50, offset=0), [])
        query = cursor.execute.call_args.args[0]
        for field in ("price", "currency", "rating", "reviews_count", "sales_estimate", "image_url", "previous_price"):
            self.assertIn(f"field_sources'->>'{field}'", query)
        self.assertIn("missing_fields", query)
        connection.close.assert_called_once()

    def test_top_opportunities_excludes_incomplete_unverified_and_stale_products(self):
        connection, cursor = self._connection()
        with patch.object(api, "get_connection", return_value=connection):
            self.assertEqual(api.top_opportunities(limit=12), [])
        query, parameters = cursor.execute.call_args.args
        self.assertIn("field_sources", query)
        self.assertIn("missing_fields", query)
        self.assertIn("p.catalog_expires_at IS NULL OR p.catalog_expires_at > NOW()", query)
        self.assertIn("%enlace-de-la-imagen.jpg%", query)
        self.assertIn("p.product_url IS NOT NULL", query)
        self.assertEqual(parameters, (12,))
        connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
