import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

APP_PATH = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_PATH))

import main


class URLFirstWorkerTests(unittest.TestCase):
    def test_worker_has_no_marketplace_discovery_path(self):
        self.assertFalse(hasattr(main, "run_marketplace_ingestion"))
        self.assertFalse(hasattr(main, "_start_marketplace_discovery"))
        self.assertFalse(hasattr(main, "_run_marketplace_discovery_safely"))

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
