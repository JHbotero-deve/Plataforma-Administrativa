import hashlib
import hmac
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
        canonical = "".join(
            key + str(value)
            for key, value in sorted(params.items())
            if key != "sign"
        )
        expected = hmac.new(
            b"app-secret", canonical.encode("utf-8"), hashlib.sha256
        ).hexdigest().upper()
        self.assertEqual(params["sign"], expected)
        self.assertEqual(len(params["sign"]), 64)
        self.assertEqual(params["sign_method"], "sha256")
        self.assertTrue(params["timestamp"].isdigit())
        self.assertEqual(len(params["timestamp"]), 13)

    def test_insufficient_permission_falls_back_to_official_top_gateway(self):
        denied = unittest.mock.MagicMock()
        denied.json.return_value = {
            "error_response": {
                "code": "InsufficientPermission",
                "msg": "App does not have permission to access this api",
            }
        }
        accepted = unittest.mock.MagicMock()
        accepted.json.return_value = {
            "aliexpress_affiliate_productdetail_get_response": {
                "resp_result": {"resp_code": 200}
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
            patch.object(affiliate_links.requests, "post", side_effect=[denied, accepted]) as post,
        ):
            result = affiliate_links.aliexpress_api_request(
                "aliexpress.affiliate.productdetail.get",
                {"product_ids": "1005001234567890", "tracking_id": "v1"},
            )
        self.assertIn("aliexpress_affiliate_productdetail_get_response", result)
        self.assertEqual(post.call_args_list[0].args[0], affiliate_links.ALIEXPRESS_API_URL)
        self.assertEqual(post.call_args_list[1].args[0], affiliate_links.ALIEXPRESS_LEGACY_API_URL)
        params = post.call_args_list[1].kwargs["data"]
        self.assertEqual(params["sign_method"], "hmac")
        self.assertEqual(len(params["sign"]), 32)
        canonical = "".join(
            key + str(value)
            for key, value in sorted(params.items())
            if key != "sign"
        )
        expected = hmac.new(b"app-secret", canonical.encode("utf-8"), hashlib.md5).hexdigest().upper()
        self.assertEqual(params["sign"], expected)
        self.assertEqual(len(params["timestamp"]), 19)

    def test_api_error_includes_safe_code_and_message_for_diagnostics(self):
        response = unittest.mock.MagicMock()
        response.json.return_value = {
            "error_response": {
                "code": "InvalidAppKey",
                "msg": "App key is not authorized",
            }
        }
        with (
            patch.dict(os.environ, {
                "ALIEXPRESS_APP_KEY": "app-key",
                "ALIEXPRESS_APP_SECRET": "app-secret",
            }, clear=True),
            patch.object(affiliate_links.requests, "post", return_value=response),
        ):
            with self.assertRaisesRegex(RuntimeError, "InvalidAppKey: App key is not authorized"):
                affiliate_links.aliexpress_api_request("aliexpress.affiliate.product.query", {})

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

    def test_promotion_campaign_response_is_not_mistaken_for_product_results(self):
        payload = {
            "code": "0",
            "resp_result": {
                "result": {
                    "current_record_count": "50",
                    "promos": [
                        {
                            "promo_name": "single days big sale",
                            "promo_desc": "single days big sale",
                            "product_num": "100",
                        }
                    ],
                },
                "resp_code": "200",
                "resp_msg": "success",
            },
        }

        with self.assertRaisesRegex(RuntimeError, "campañas promocionales, no productos"):
            promotion_bots._product_results(payload)

    def test_normalizes_documented_affiliate_response_and_upgrades_official_http_link(self):
        payload = {
            "code": "0",
            "resp_result": {
                "resp_code": "200",
                "resp_msg": "success",
                "result": {
                    "products": [
                        {
                            "product_id": "33006951782",
                            "shop_id": "3255036",
                            "shop_url": "https://www.aliexpress.com/store/3255036",
                            "product_title": "Vestido",
                            "product_detail_url": "https://www.aliexpress.com/item/33006951782.html",
                            "promotion_link": "http://s.click.aliexpress.com/e/xxxxx",
                            "product_main_image_url": "https://ae01.alicdn.com/kf/image.jpg",
                            "product_small_image_urls": [],
                            "target_sale_price": "350",
                            "target_sale_price_currency": "USD",
                            "original_price": "100",
                            "discount": "50%",
                            "evaluate_rate": "89.2%",
                            "lastest_volume": "300",
                            "hot_product_commission_rate": "60%",
                            "commission_rate": "3.5%",
                            "relevant_market_commission_rate": "10%",
                            "promo_code_info": {
                                "promo_code": "GMG20207",
                                "code_promotionurl": "https://s.click.aliexpress.com/e/_xxxxx",
                            },
                        }
                    ]
                },
            },
        }

        [raw] = promotion_bots._product_results(payload)
        product = promotion_bots._normalize_product(raw, "ropa")

        self.assertEqual(product["affiliate_url"], "https://s.click.aliexpress.com/e/_xxxxx")
        self.assertEqual(product["seller"], {"external_id": "3255036", "name": "Tienda AliExpress #3255036"})
        self.assertEqual(product["source_metadata"]["seller_url"], "https://www.aliexpress.com/store/3255036")
        self.assertEqual(product["commission_rate"], 10)
        self.assertEqual(product["gallery_urls"], [])
        self.assertFalse(promotion_bots._review_product(product))

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

        result = db._affiliate_url(product, "aliexpress")

        self.assertEqual(result, product["affiliate_url"])


if __name__ == "__main__":
    unittest.main()
