import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

APP_PATH = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_PATH))

import main
import marketplace_ingestion


class IndependentMarketplaceWorkerTests(unittest.TestCase):
    def test_pipeline_uses_local_catalog_without_marketplace_discovery(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []

        with (
            patch.object(main, "expire_catalog") as expire,
            patch.object(main, "get_connection", return_value=connection),
            patch.object(main, "creator_configured", return_value=False) as creator,
            patch.object(marketplace_ingestion, "run_marketplace_ingestion") as ingest,
            patch.object(marketplace_ingestion, "_search_amazon_candidates") as amazon_search,
            patch.object(marketplace_ingestion, "_search_aliexpress_candidates") as aliexpress_search,
        ):
            main.run_pipeline()

        expire.assert_called_once()
        creator.assert_called_once()
        ingest.assert_not_called()
        amazon_search.assert_not_called()
        aliexpress_search.assert_not_called()
        connection.close.assert_called_once()

    def test_amazon_search_paginates_to_100_unique_candidates(self):
        def page(_query, _category, limit, page):
            return [
                {"external_id": f"A{page:02d}{index:08d}"}
                for index in range(limit)
            ]

        with patch.object(marketplace_ingestion, "search_amazon", side_effect=page) as search:
            products, pages = marketplace_ingestion._search_amazon_candidates(100)

        self.assertEqual(len(products), 100)
        self.assertEqual(pages, 10)
        self.assertEqual(search.call_count, 10)
        self.assertTrue(all(call.kwargs["limit"] == 10 for call in search.call_args_list))

    def test_aliexpress_search_requests_two_pages_of_50_candidates(self):
        def page(_category, _keyword, page_no, review, minimum_discount):
            return [
                {"external_id": f"AE{page_no:02d}{index:08d}"}
                for index in range(50)
            ]

        with patch.object(marketplace_ingestion, "_search_bucket", side_effect=page) as search:
            products, pages = marketplace_ingestion._search_aliexpress_candidates(100)

        self.assertEqual(len(products), 100)
        self.assertEqual(pages, 2)
        self.assertEqual(search.call_count, 2)
        self.assertTrue(all(call.kwargs["minimum_discount"] == 40 for call in search.call_args_list))

    def test_telegram_failure_does_not_undo_saved_or_published_product(self):
        connection = MagicMock()
        product = {
            "external_id": "B000000001",
            "title": "Producto de prueba",
            "price": 50,
            "previous_price": 100,
            "currency": "USD",
            "image_url": "https://images.example/product.jpg",
            "product_url": "https://www.amazon.com/dp/B000000001",
            "affiliate_url": "https://www.amazon.com/dp/B000000001",
            "source_metadata": {},
            "seller": {"name": "Tienda de prueba"},
        }

        with (
            patch.object(marketplace_ingestion, "get_connection", return_value=connection),
            patch.object(marketplace_ingestion, "upsert_product", return_value=9),
            patch.object(marketplace_ingestion, "score_product", return_value=80),
            patch.object(marketplace_ingestion, "build_customer_offer", return_value={"discount_pct": 50}),
            patch.object(marketplace_ingestion, "publish_discounted_offer", return_value={"sale_price": 60, "discount_pct": 40}),
            patch.object(marketplace_ingestion, "send_telegram_publication", side_effect=RuntimeError("Telegram offline")),
        ):
            result = marketplace_ingestion._persist_candidates("amazon", [product])

        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["published"], 1)
        self.assertEqual(result["telegram_sent"], 0)
        self.assertTrue(any("Telegram" in error for error in result["errors"]))
        connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
