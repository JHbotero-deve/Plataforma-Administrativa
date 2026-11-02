import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

APP_PATH = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_PATH))

import marketplace_search


class MarketplaceSearchTests(unittest.TestCase):
    def test_searches_both_marketplaces_and_publishes_only_qualified_offers(self):
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.fetchone.return_value = {"average": 60}
        amazon_product = {
            "platform": "amazon",
            "external_id": "B012345678",
            "title": "Auriculares Amazon",
            "price": 50,
            "previous_price": 100,
            "currency": "USD",
            "image_url": "https://m.media-amazon.com/images/I/photo.jpg",
            "product_url": "https://www.amazon.com/dp/B012345678",
        }
        aliexpress_product = {
            "platform": "aliexpress",
            "external_id": "1005001234567890",
            "title": "Auriculares AliExpress",
            "price": 30,
            "previous_price": 60,
            "currency": "USD",
            "image_url": "https://ae-pic-a1.aliexpress-media.com/image.jpg",
            "product_url": "https://www.aliexpress.com/item/1005001234567890.html",
            "affiliate_url": "https://s.click.aliexpress.com/e/abc123",
        }

        with (
            patch("marketplace_search.get_connection", return_value=connection),
            patch("marketplace_search.search_amazon", return_value=[amazon_product]) as amazon,
            patch("marketplace_search._search_bucket", return_value=[aliexpress_product]) as aliexpress,
            patch("marketplace_search.upsert_product", side_effect=[1, 2]) as upsert,
            patch("marketplace_search.score_product", return_value=80),
            patch("marketplace_search.publish_discounted_offer", side_effect=[{"sale_price": 60}, None]) as publish,
            patch.dict(os.environ, {
                "ALIEXPRESS_APP_KEY": "key",
                "ALIEXPRESS_APP_SECRET": "secret",
                "ALIEXPRESS_TRACKING_ID": "tracking",
            }, clear=True),
        ):
            result = marketplace_search.search_and_publish("auriculares")

        self.assertEqual(result, {"status": "completed", "found": 2, "published": 1, "errors": []})
        amazon.assert_called_once_with("auriculares", "busquedas", limit=10, page=1)
        aliexpress.assert_called_once_with("busquedas", "auriculares", 1, review=False)
        self.assertEqual(upsert.call_count, 2)
        self.assertEqual(publish.call_count, 2)
        connection.commit.assert_called_once()
        connection.close.assert_called_once()

    def test_processes_queued_request_and_saves_result_status(self):
        claimed = MagicMock()
        claimed_cursor = claimed.cursor.return_value.__enter__.return_value
        claimed_cursor.fetchall.return_value = [{"id": 7, "query": "audifonos"}]
        updated = MagicMock()
        update_cursor = updated.cursor.return_value.__enter__.return_value

        with (
            patch("marketplace_search.get_connection", side_effect=[claimed, updated]),
            patch("marketplace_search.search_and_publish", return_value={
                "status": "completed", "found": 5, "published": 2, "errors": []
            }),
        ):
            processed = marketplace_search.process_marketplace_search_requests(limit=1)

        self.assertEqual(processed, 1)
        self.assertIn("status='processing'", claimed_cursor.execute.call_args_list[2].args[0])
        saved = update_cursor.execute.call_args.args[1]
        self.assertEqual(saved, ("completed", 5, 2, None, 7))
        claimed.commit.assert_called_once()
        updated.commit.assert_called_once()
        claimed.close.assert_called_once()
        updated.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
