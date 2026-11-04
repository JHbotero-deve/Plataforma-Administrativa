import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

APP_PATH = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_PATH))

import main


class UrlOnlyWorkerTests(unittest.TestCase):
    def test_worker_does_not_load_marketplace_api_search_or_refill(self):
        self.assertFalse(hasattr(main, "run_amazon_storefront_refill"))
        self.assertFalse(hasattr(main, "run_storefront_refill"))
        self.assertFalse(hasattr(main, "run_promotion_cycle"))
        self.assertFalse(hasattr(main, "process_marketplace_search_requests"))

    def test_pipeline_enriches_saved_urls_without_marketplace_api_calls(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        output = io.StringIO()

        with (
            patch.object(main, "expire_catalog"),
            patch.object(main, "get_connection", return_value=connection),
            patch.object(main, "enrich_pending_products", return_value=2) as enrich,
            patch.object(main, "creator_configured", return_value=False),
            contextlib.redirect_stdout(output),
        ):
            main.run_pipeline()

        enrich.assert_called_once_with(connection, limit=20)
        self.assertIn("modo URL", output.getvalue())
        self.assertIn("productos enriquecidos desde URLs públicas: 2", output.getvalue())
        connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
