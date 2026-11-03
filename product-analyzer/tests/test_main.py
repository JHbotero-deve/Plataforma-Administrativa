import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

APP_PATH = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_PATH))

import main


class WorkerPipelineTests(unittest.TestCase):
    def test_worker_enriches_pending_urls_and_scores_eligible_products(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.side_effect = [
            [{"category": "accesorios", "currency": "USD", "avg_price": 30}],
            [{"id": 7}],
        ]
        cursor.fetchone.side_effect = [
            {"category": "accesorios", "currency": "USD"},
            {"title": "Producto real", "current_price": 20, "product_url": "https://www.amazon.com/dp/B000000001", "platform": "amazon"},
        ]

        with (
            patch.object(main, "get_connection", return_value=connection),
            patch.object(main, "expire_catalog"),
            patch.object(main, "enrich_pending_products", return_value=12) as enrich,
            patch.object(main, "score_product", return_value=80) as score,
            patch.object(main, "send_telegram_alert") as alert,
            patch.object(main, "creator_configured", return_value=False),
            patch.object(main, "PROMOTION_API_ENABLED", False),
            patch.object(main, "STOREFRONT_AUTO_REFILL_ENABLED", True),
            patch.object(main, "run_amazon_storefront_refill", return_value={"published": 0}),
            patch.object(main, "run_storefront_refill", return_value={"published": 0}),
        ):
            main.run_pipeline()

        enrich.assert_called_once_with(connection, limit=20)
        score.assert_called_once_with(connection, 7, 30.0)
        alert.assert_called_once()
        self.assertTrue(any("SELECT p.id" in call.args[0] for call in cursor.execute.call_args_list))
        connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
