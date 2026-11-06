import unittest
from unittest.mock import MagicMock, patch

import publications


class PublicationQualityTests(unittest.TestCase):
    def test_public_endpoint_hides_stale_incomplete_and_unverified_cards(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        with patch.object(publications, "get_connection", return_value=connection):
            self.assertEqual(publications.list_publications(request=MagicMock(), limit=50), [])
        query = cursor.execute.call_args.args[0]
        self.assertIn("catalog_status", query)
        self.assertIn("missing_fields", query)
        self.assertIn("pc.sale_price > 0", query)
        self.assertIn("field_sources", query)
        self.assertNotIn("profit_margin_pct", query)
        connection.close.assert_called_once()

    def test_admin_unpublished_view_can_still_inspect_stale_cards(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        with (
            patch.object(publications, "get_connection", return_value=connection),
            patch.object(publications, "require_admin"),
        ):
            self.assertEqual(publications.list_publications(request=MagicMock(), limit=50, include_unpublished=True), [])
        query = cursor.execute.call_args.args[0]
        self.assertNotIn("catalog_status", query)
        self.assertNotIn("missing_fields", query)
        connection.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
