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


if __name__ == "__main__":
    unittest.main()
