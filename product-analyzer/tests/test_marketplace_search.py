import sys
import unittest
from pathlib import Path

APP_PATH = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_PATH))

import marketplace_search


class MarketplaceSearchRetirementTests(unittest.TestCase):
    def test_external_marketplace_search_is_disabled(self):
        result = marketplace_search.search_and_publish("wireless headphones")
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(result["found"], 0)
        self.assertEqual(result["published"], 0)
        self.assertIn("URL", result["errors"][0])

    def test_legacy_search_queue_does_not_process_requests(self):
        self.assertEqual(marketplace_search.process_marketplace_search_requests(), 0)


if __name__ == "__main__":
    unittest.main()
