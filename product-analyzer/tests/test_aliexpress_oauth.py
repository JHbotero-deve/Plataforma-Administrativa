import sys
import unittest
from pathlib import Path

APP_PATH = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_PATH))

import aliexpress_oauth


class AliExpressOAuthRetirementTests(unittest.TestCase):
    def test_oauth_router_has_no_external_marketplace_routes(self):
        self.assertEqual(aliexpress_oauth.router.routes, [])

    def test_oauth_storage_is_not_initialized(self):
        self.assertIsNone(aliexpress_oauth.ensure_aliexpress_oauth_storage())


if __name__ == "__main__":
    unittest.main()
