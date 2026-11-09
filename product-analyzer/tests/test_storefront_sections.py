import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from storefront_sections import DEFAULT_SECTIONS, normalize_target_count


class StorefrontSectionTests(unittest.TestCase):
    def test_ten_sections_start_with_one_hundred_products(self):
        self.assertEqual(len(DEFAULT_SECTIONS), 10)
        self.assertTrue(all(section[5] == 100 for section in DEFAULT_SECTIONS))

    def test_storefront_renders_featured_section_and_does_not_hide_it_without_category(self):
        html = (Path(__file__).resolve().parents[1] / "frontend" / "tienda.html").read_text(encoding="utf-8")
        self.assertIn('section.slug==="ofertas-destacadas"', html)
        self.assertIn('const sections=sectionSettings.filter', html)

    def test_empty_api_response_clears_stale_cache_and_cache_has_ttl(self):
        html = (Path(__file__).resolve().parents[1] / "frontend" / "tienda.html").read_text(encoding="utf-8")
        self.assertIn("central_products_cache_saved_at", html)
        self.assertIn("const CACHE_TTL_MS=21600000", html)
        self.assertNotIn("!list.length", html)

    def test_active_hero_image_loads_eagerly_and_secondary_images_are_lazy(self):
        html = (Path(__file__).resolve().parents[1] / "frontend" / "tienda.html").read_text(encoding="utf-8")
        self.assertIn('loading="eager" fetchpriority="high"', html)
        self.assertIn('loading="lazy"', html)

    def test_public_cards_require_verified_category_and_reject_api_provenance(self):
        import storefront_sections
        quality_filter = storefront_sections.PUBLIC_CARD_QUALITY_FILTER
        self.assertNotIn("'aliexpress_affiliate_api'", quality_filter)
        self.assertIn("field_sources'->>'title' IN ('public_product_page', 'user')", quality_filter)
        self.assertIn("field_sources'->>'category'", quality_filter)
        self.assertIn("'category'", quality_filter)

    def test_target_count_accepts_only_integers_between_one_hundred_and_five_hundred(self):
        self.assertEqual(normalize_target_count(100), 100)
        self.assertEqual(normalize_target_count(500), 500)
        for value in (99, 501, 100.5, True, "100"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_target_count(value)

    def test_featured_section_counts_all_current_publications_not_only_margin_products(self):
        import storefront_sections
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [
            {"slug": "ofertas-destacadas", "title": "Ofertas destacadas", "category_name": None, "category_names": [], "target_count": 100, "is_active": True, "sort_order": 100},
            {"slug": "tecnologia", "title": "Tecnología", "category_name": "tecnologia", "category_names": ["tecnologia"], "target_count": 100, "is_active": True, "sort_order": 10},
        ]
        cursor.fetchone.return_value = {"total": 0}
        with patch.object(storefront_sections, "get_connection", return_value=connection):
            result = storefront_sections.get_storefront_sections()
        queries = [call.args[0] for call in cursor.execute.call_args_list[1:]]
        self.assertEqual(len(result), 2)
        self.assertTrue(all("catalog_status" in query and "missing_fields" in query for query in queries))
        self.assertFalse(any("profit_margin_pct" in query for query in queries))
        connection.close.assert_called_once()



if __name__ == "__main__":
    unittest.main()
