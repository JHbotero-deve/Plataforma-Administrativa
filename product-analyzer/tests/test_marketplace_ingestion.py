import sys
import unittest
from pathlib import Path
from unittest.mock import patch

APP_PATH = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_PATH))

import marketplace_ingestion


class MarketplaceIngestionTests(unittest.TestCase):
    def setUp(self):
        self.candidate = {
            "external_id": "B000000001",
            "title": "Auriculares Bluetooth",
            "product_url": "https://www.amazon.com/dp/B000000001",
            "price": 29.99,
            "currency": "USD",
            "image_url": "https://m.media-amazon.com/images/I/photo.jpg",
            "source_metadata": {"import_method": "amazon_creators_api"},
        }

    def test_search_keeps_aliexpress_failure_independent_from_amazon(self):
        verified = {
            **self.candidate,
            "source_metadata": {
                "enrichment_status": "complete",
                "catalog_refresh_verified": True,
            },
        }
        with (
            patch.object(marketplace_ingestion, "_search_candidates", side_effect=[[self.candidate], RuntimeError("401")]),
            patch.object(marketplace_ingestion, "enrich_product", return_value=verified),
            patch.object(marketplace_ingestion, "_persist_verified", return_value=(17, 78.5)) as persist,
        ):
            result = marketplace_ingestion.search_and_import("auriculares", limit=2)
        self.assertEqual(len(result["products"]), 1)
        self.assertTrue(result["products"][0]["verified"])
        self.assertEqual(result["products"][0]["id"], 17)
        self.assertTrue(any(error.startswith("aliexpress:") for error in result["errors"]))
        persist.assert_called_once()

    def test_unverified_public_page_is_returned_but_never_persisted(self):
        partial = {
            **self.candidate,
            "source_metadata": {
                "enrichment_status": "partial",
                "catalog_refresh_verified": False,
            },
        }
        with (
            patch.object(marketplace_ingestion, "_search_candidates", side_effect=[[self.candidate], []]),
            patch.object(marketplace_ingestion, "enrich_product", return_value=partial),
            patch.object(marketplace_ingestion, "_persist_verified", return_value=None) as persist,
        ):
            result = marketplace_ingestion.search_and_import("auriculares", limit=2)
        self.assertEqual(len(result["products"]), 1)
        self.assertFalse(result["products"][0]["verified"])
        self.assertIsNone(result["products"][0]["id"])
        persist.assert_not_called()


if __name__ == "__main__":
    unittest.main()
