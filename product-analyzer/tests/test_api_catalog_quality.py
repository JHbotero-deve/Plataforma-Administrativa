import os
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
        self.assertIn("THEN p.title ELSE NULL END AS title", query)
        self.assertIn("THEN p.description ELSE NULL END AS description", query)
        self.assertIn("THEN seller.name ELSE NULL END AS seller_name", query)
        self.assertIn("THEN COALESCE(p.image_gallery, '[]'::jsonb) ELSE '[]'::jsonb END AS image_gallery", query)
        self.assertIn("THEN p.stock ELSE NULL END AS stock", query)
        self.assertIn("THEN c.name ELSE NULL END AS category", query)
        self.assertIn("AS data_quality", query)
        connection.close.assert_called_once()

    def test_top_opportunities_excludes_incomplete_unverified_and_stale_products(self):
        connection, cursor = self._connection()
        with patch.object(api, "get_connection", return_value=connection):
            self.assertEqual(api.top_opportunities(limit=12), [])
        query, parameters = cursor.execute.call_args.args
        self.assertIn("field_sources", query)
        self.assertIn("missing_fields", query)
        self.assertIn("field_sources'->>'title' IN ('public_product_page', 'user')", query)
        self.assertIn("field_sources'->>'price' IN ('public_product_page', 'user')", query)
        self.assertIn("field_sources'->>'image_url' IN ('public_product_page', 'user')", query)
        self.assertIn("LOWER(COALESCE(p.title, '')) NOT LIKE 'producto amazon %%'", query)
        self.assertIn("p.catalog_expires_at IS NULL OR p.catalog_expires_at > NOW()", query)
        self.assertIn("%enlace-de-la-imagen.jpg%", query)
        self.assertIn("p.product_url IS NOT NULL", query)
        self.assertEqual(parameters, (12,))
        connection.close.assert_called_once()


    def test_products_endpoint_preserves_gallery_for_admin_cards(self):
        connection, cursor = self._connection()
        with patch.object(api, "get_connection", return_value=connection):
            self.assertEqual(api.list_products(limit=50, offset=0), [])
        query = cursor.execute.call_args.args[0]
        self.assertIn("THEN COALESCE(p.image_gallery, '[]'::jsonb) ELSE '[]'::jsonb END AS image_gallery", query)
        self.assertIn("field_sources'->>'image_url'", query)

    def test_public_product_detail_rejects_stale_cards_and_hides_unverified_discounts(self):
        connection, cursor = self._connection()
        cursor.fetchone.return_value = None
        with patch.object(api, "get_connection", return_value=connection):
            with self.assertRaises(api.HTTPException):
                api.public_product(123)
        query = cursor.execute.call_args.args[0]
        self.assertIn("catalog_status", query)
        self.assertIn("missing_fields", query)
        self.assertIn("field_sources", query)
        self.assertIn("previous_price", query)
        self.assertIn("THEN p.description ELSE NULL END AS description", query)
        self.assertIn("THEN seller.name ELSE NULL END AS seller_name", query)
        self.assertIn("THEN COALESCE(p.image_gallery, '[]'::jsonb) ELSE '[]'::jsonb END AS image_gallery", query)
        self.assertIn("THEN p.stock ELSE NULL END AS stock", query)
        connection.close.assert_called_once()

    def test_affiliate_click_and_sitemap_exclude_stale_cards(self):
        connection, cursor = self._connection()
        cursor.fetchone.return_value = None
        with patch.object(api, "get_connection", return_value=connection):
            with self.assertRaises(api.HTTPException):
                api.affiliate_click({"product_id": 123})
        click_query = cursor.execute.call_args.args[0]
        self.assertIn("catalog_status", click_query)
        self.assertIn("missing_fields", click_query)
        connection.close.assert_called_once()

        connection, cursor = self._connection()
        with patch.object(api, "get_connection", return_value=connection):
            api.public_sitemap()
        sitemap_query = cursor.execute.call_args.args[0]
        self.assertIn("catalog_status", sitemap_query)
        self.assertIn("missing_fields", sitemap_query)
        connection.close.assert_called_once()



    def test_telegram_polling_is_opt_in_for_the_api_service(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(api._telegram_polling_enabled())
        with patch.dict(os.environ, {"ENABLE_TELEGRAM_BOT": "true"}, clear=True):
            self.assertTrue(api._telegram_polling_enabled())

    def test_pipeline_metrics_does_not_report_a_synthetic_successful_run(self):
        connection, cursor = self._connection()
        rows = [{"platform": "amazon", "product_count": 2, "with_url": 4, "stale": 1, "last_update": None}]
        cursor.fetchall.return_value = rows
        with patch.object(api, "get_connection", return_value=connection):
            result = api.pipeline_metrics()
        self.assertIsNone(result["latest"])
        self.assertEqual(result["platforms"], rows)
        query = cursor.execute.call_args.args[0]
        self.assertIn("AS with_url", query)
        self.assertIn("AS stale", query)
        connection.close.assert_called_once()

if __name__ == "__main__":
    unittest.main()
