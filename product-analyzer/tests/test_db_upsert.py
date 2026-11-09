import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import db


class ProductUpsertTests(unittest.TestCase):
    def _run_upsert(self, metadata):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [{"id": 1}, None, {"id": 42}]
        product = {
            "external_id": "B000000001",
            "title": "Producto Amazon B000000001",
            "product_url": "https://www.amazon.com/dp/B000000001",
            "price": None,
            "currency": None,
            "image_url": None,
            "gallery_urls": [],
            "source_metadata": metadata,
        }
        with patch.object(db, "_affiliate_url", return_value=None):
            self.assertEqual(db.upsert_product(connection, "amazon", None, product), 42)
        query = cursor.execute.call_args_list[2].args[0]
        connection.commit.assert_called_once()
        return query

    def test_url_only_reimport_preserves_existing_verified_product_data(self):
        query = self._run_upsert({
            "import_method": "authorized_cli_url",
            "metadata_source": "url_only",
            "original_url": "https://www.amazon.com/dp/B000000001",
            "missing_fields": ["title", "price", "category", "image_url", "currency"],
        })
        self.assertIn("category_id = COALESCE(EXCLUDED.category_id, products.category_id)", query)
        self.assertIn("THEN products.title", query)
        self.assertIn("'last_import_url'", query)
        self.assertIn("COALESCE(EXCLUDED.current_price, products.current_price)", query)
        self.assertIn("COALESCE(EXCLUDED.image_url, products.image_url)", query)

    def test_partial_manual_import_merges_field_sources_without_erasing_verified_fields(self):
        query = self._run_upsert({
            "import_method": "authorized_cli_url",
            "metadata_source": "cli",
            "field_sources": {"category": "user"},
            "missing_fields": ["title", "price", "image_url", "currency"],
        })
        self.assertIn("jsonb_set(", query)
        self.assertIn("'{field_sources}'", query)
        self.assertIn("COALESCE(products.source_metadata->'field_sources'", query)
        self.assertIn("'{missing_fields}'", query)
        self.assertIn(") - ARRAY(", query)

    def test_non_url_import_keeps_its_existing_metadata_replacement_behavior(self):
        query = self._run_upsert({"metadata_source": "official_marketplace"})
        self.assertIn("ELSE EXCLUDED.source_metadata", query)


if __name__ == "__main__":
    unittest.main()
