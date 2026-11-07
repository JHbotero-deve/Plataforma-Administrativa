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

    def test_discovery_runs_in_a_background_thread(self):
        with (
            patch.object(main.threading, "Thread") as thread,
            patch.object(main, "_run_marketplace_discovery_safely") as discovery,
        ):
            main._start_marketplace_discovery()
            thread.assert_called_once()
            thread.call_args.kwargs["target"]()
        discovery.assert_called_once()
    def test_discovery_logs_provider_errors_without_secrets(self):
        result = {
            "amazon": {
                "received": 0,
                "verified_and_saved": 0,
                "pending_public_verification": 0,
                "status": "error",
                "errors": ["RuntimeError: invalid_scope app_secret=TOPSECRET"],
            }
        }
        with (
            patch.object(main, "run_marketplace_ingestion", return_value=result),
            patch("builtins.print") as output,
        ):
            main._run_marketplace_discovery_safely()
        messages = " ".join(str(call.args[0]) for call in output.call_args_list)
        self.assertIn("invalid_scope", messages)
        self.assertIn("[redacted]", messages)
        self.assertNotIn("TOPSECRET", messages)

    def test_discovery_failure_does_not_stop_worker(self):
        with patch.object(main, "run_marketplace_ingestion", side_effect=RuntimeError("API unavailable")):
            main._run_marketplace_discovery_safely()


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
