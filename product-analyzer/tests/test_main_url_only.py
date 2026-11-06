import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

APP_PATH = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_PATH))

import main
import marketplace_ingestion


class URLFirstWorkerTests(unittest.TestCase):
    def test_pipeline_does_not_call_marketplace_discovery(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        with (
            patch.object(main, "expire_catalog"),
            patch.object(main, "get_connection", return_value=connection),
            patch.object(main, "enrich_pending_products", return_value=0),
            patch.object(main, "publish_catalog_product", return_value=None),
            patch.object(main, "creator_configured", return_value=False),
            patch.object(marketplace_ingestion, "run_marketplace_ingestion") as discovery,
        ):
            main.run_pipeline()
        discovery.assert_not_called()
        connection.close.assert_called_once()

    def test_legacy_marketplace_ingestion_is_disabled(self):
        result = marketplace_ingestion.run_marketplace_ingestion()
        self.assertEqual(result["status"], "disabled")
        self.assertIn("URLs", result["reason"])


    def test_expired_catalog_products_are_marked_stale_without_deactivation(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.rowcount = 12
        with patch.object(main, "get_connection", return_value=connection):
            main.expire_catalog()
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertIn("catalog_status", statements[0])
        self.assertNotIn("SET is_active = FALSE", statements[0])
        self.assertIn("UPDATE published_cards", statements[1])
        self.assertIn("price_display = 'Precio pendiente de verificación'", statements[1])
        self.assertIn("sale_price = NULL", statements[1])
        self.assertIn("profit_amount = NULL", statements[1])
        self.assertIn("opportunity_score = 0", statements[1])
        connection.commit.assert_called_once()
        connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
