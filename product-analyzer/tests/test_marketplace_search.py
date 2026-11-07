import sys
import unittest
from unittest.mock import patch
from pathlib import Path

APP_PATH = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_PATH))

import marketplace_search


class MarketplaceSearchTests(unittest.TestCase):
    def test_external_search_uses_official_marketplace_importer(self):
        imported = {
            "products": [{"id": 7, "verified": True}],
            "errors": [],
        }
        with patch("marketplace_ingestion.search_and_import", return_value=imported) as search:
            result = marketplace_search.search_and_publish("wireless headphones")
        search.assert_called_once_with("wireless headphones", limit=8)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["found"], 1)
        self.assertEqual(result["verified"], 1)
        self.assertEqual(result["published"], 0)

    def test_legacy_search_queue_remains_inert(self):
        self.assertEqual(marketplace_search.process_marketplace_search_requests(), 0)


if __name__ == "__main__":
    unittest.main()
