import unittest
from unittest.mock import MagicMock
from app.ingest import safe_ingest_product

class TestProductIngestion(unittest.TestCase):
    def test_safe_ingest_creates_or_updates(self):
        mock_db = MagicMock()
        mock_db.query().filter_by().first.return_value = None
        product_data = {"external_id": "MLA123456", "title": "iPhone Test", "price": 1000.0}
        result = safe_ingest_product(mock_db, product_data)
        self.assertTrue(result)
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

if __name__ == '__main__':
    unittest.main()
