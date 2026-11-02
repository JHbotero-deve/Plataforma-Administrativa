import unittest

from storefront_sections import DEFAULT_SECTIONS, normalize_target_count


class StorefrontSectionTests(unittest.TestCase):
    def test_ten_sections_start_with_one_hundred_products(self):
        self.assertEqual(len(DEFAULT_SECTIONS), 10)
        self.assertTrue(all(section[5] == 100 for section in DEFAULT_SECTIONS))

    def test_target_count_accepts_only_integers_between_one_hundred_and_five_hundred(self):
        self.assertEqual(normalize_target_count(100), 100)
        self.assertEqual(normalize_target_count(500), 500)
        for value in (99, 501, 100.5, True, "100"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_target_count(value)


if __name__ == "__main__":
    unittest.main()
