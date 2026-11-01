import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


APP_PATH = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_PATH))

import affiliate_links
import db
import promotion_bots


class AliExpressPromotionBotTests(unittest.TestCase):
    def test_signed_affiliate_request_uses_configured_tracking_id(self):
        response = unittest.mock.MagicMock()
        response.json.return_value = {"ok": True}
        with (
            patch.dict(
                os.environ,
                {
                    "ALIEXPRESS_APP_KEY": "app-key",
                    "ALIEXPRESS_APP_SECRET": "app-secret",
                    "ALIEXPRESS_TRACKING_ID": "v1",
                },
                clear=True,
            ),
            patch.object(affiliate_links.requests, "post", return_value=response) as post,
        ):
            result = affiliate_links.aliexpress_api_request(
                "aliexpress.affiliate.product.query",
                {"keywords": "ropa", "tracking_id": "v1"},
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(post.call_args.args[0], affiliate_links.ALIEXPRESS_API_URL)
        params = post.call_args.kwargs["data"]
        self.assertEqual(params["tracking_id"], "v1")
        self.assertEqual(params["method"], "aliexpress.affiliate.product.query")
        self.assertEqual(len(params["sign"]), 32)

    def test_product_query_response_extracts_official_result_list(self):
        payload = {
            "aliexpress_affiliate_product_query_response": {
                "resp_result": {
                    "resp_code": 200,
                    "result": {
                        "products": {
                            "product": [
                                {"product_id": "123"},
                                {"product_id": "456"},
                            ]
                        }
                    },
                }
            }
        }

        products = promotion_bots._product_results(payload)

        self.assertEqual([item["product_id"] for item in products], ["123", "456"])

    def test_reviewer_accepts_a_verified_discount_with_sales_and_affiliate_link(self):
        raw = {
            "product_id": "1005001234567890",
            "product_title": "Bolso de viaje",
            "product_detail_url": "https://www.aliexpress.com/item/1005001234567890.html",
            "promotion_link": "https://s.click.aliexpress.com/e/abc123",
            "product_main_image_url": "https://ae-pic-a1.aliexpress-media.com/image.jpg",
            "target_sale_price": "12.00",
            "target_sale_price_currency": "USD",
            "target_original_price": "20.00",
            "discount": "40%",
            "evaluate_rate": "96.5%",
            "lastest_volume": "1200",
            "commission_rate": "5%",
        }

        product = promotion_bots._normalize_product(raw, "accesorios")

        self.assertIsNotNone(product)
        self.assertTrue(promotion_bots._review_product(product))
        self.assertEqual(product["affiliate_url"], raw["promotion_link"])
        self.assertEqual(product["sales_estimate"], 1200)

    def test_normalizes_image_gallery_as_list_string_or_api_string_container(self):
        raw = {
            "product_id": "1005001234567890",
            "product_title": "Bolso de viaje",
            "product_detail_url": "https://www.aliexpress.com/item/1005001234567890.html",
            "promotion_link": "https://s.click.aliexpress.com/e/abc123",
            "product_main_image_url": "https://ae-pic-a1.aliexpress-media.com/image.jpg",
            "product_small_image_urls": {
                "string": [
                    "https://ae-pic-a1.aliexpress-media.com/one.jpg",
                    "https://example.com/untrusted.jpg",
                ]
            },
            "target_sale_price": "12.00",
            "target_original_price": "20.00",
            "discount": "40%",
            "evaluate_rate": "96.5%",
            "lastest_volume": "1200",
            "commission_rate": "5%",
        }

        product = promotion_bots._normalize_product(raw, "accesorios")

        self.assertEqual(
            product["gallery_urls"],
            ["https://ae-pic-a1.aliexpress-media.com/one.jpg"],
        )
        raw["product_small_image_urls"] = (
            "https://ae-pic-a1.aliexpress-media.com/single.jpg"
        )
        product = promotion_bots._normalize_product(raw, "accesorios")
        self.assertEqual(
            product["gallery_urls"],
            ["https://ae-pic-a1.aliexpress-media.com/single.jpg"],
        )
        raw["product_small_image_urls"] = None
        self.assertEqual(
            promotion_bots._normalize_product(raw, "accesorios")["gallery_urls"],
            [],
        )

    def test_link_generation_returns_the_official_promotion_not_original_url(self):
        response = unittest.mock.MagicMock()
        response.json.return_value = {
            "result": {
                "promotion_links": {
                    "promotion_link": [
                        {
                            "source_value": "https://www.aliexpress.com/item/123.html",
                            "promotion_link": "https://s.click.aliexpress.com/e/abc123",
                        }
                    ]
                }
            }
        }
        with (
            patch.dict(
                os.environ,
                {
                    "ALIEXPRESS_APP_KEY": "app-key",
                    "ALIEXPRESS_APP_SECRET": "app-secret",
                    "ALIEXPRESS_TRACKING_ID": "v1",
                },
                clear=True,
            ),
            patch.object(affiliate_links.requests, "post", return_value=response),
        ):
            result = affiliate_links.generate_aliexpress_affiliate_url(
                "https://www.aliexpress.com/item/123.html"
            )

        self.assertEqual(result, "https://s.click.aliexpress.com/e/abc123")

    def test_reviewer_rejects_low_discount_and_untrusted_affiliate_url(self):
        raw = {
            "product_id": "1005001234567890",
            "product_title": "Bolso",
            "product_detail_url": "https://www.aliexpress.com/item/1005001234567890.html",
            "promotion_link": "https://example.com/affiliate",
            "product_main_image_url": "https://ae-pic-a1.aliexpress-media.com/image.jpg",
            "target_sale_price": "12.00",
            "discount": "10%",
            "evaluate_rate": "99%",
            "lastest_volume": "1200",
            "commission_rate": "5%",
        }

        self.assertIsNone(promotion_bots._normalize_product(raw, "accesorios"))

    def test_database_preserves_official_aliexpress_promotion_link(self):
        product = {
            "product_url": "https://www.aliexpress.com/item/1005001234567890.html",
            "affiliate_url": "https://s.click.aliexpress.com/e/abc123",
        }

        with patch.object(db, "generate_aliexpress_affiliate_url") as generate:
            result = db._affiliate_url(product, "aliexpress")

        self.assertEqual(result, product["affiliate_url"])
        generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
