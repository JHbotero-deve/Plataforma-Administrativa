import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

APP_PATH = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_PATH))

import amazon_creators


class AmazonCreatorsTests(unittest.TestCase):
    def setUp(self):
        amazon_creators.TOKEN_CACHE.update({"value": None, "expires_at": 0, "version": None})

    def _item(self):
        return {
            "asin": "B012345678",
            "detailPageURL": "https://www.amazon.com/dp/B012345678?tag=central-20",
            "itemInfo": {
                "title": {"displayValue": "Auriculares inalámbricos"},
                "features": {"displayValues": ["Cancelación de ruido", "Bluetooth 5.3"]},
                "byLineInfo": {"brand": {"displayValue": "Central Audio"}},
            },
            "images": {
                "primary": {"large": {"url": "https://m.media-amazon.com/images/I/photo.jpg"}},
                "variants": [{"large": {"url": "https://m.media-amazon.com/images/I/photo-2.jpg"}}],
            },
            "offersV2": {
                "listings": [{
                    "merchantInfo": {"id": "seller-1", "name": "Amazon Marketplace"},
                    "price": {
                        "money": {"amount": 30, "currency": "USD"},
                        "savingBasis": {
                            "money": {"amount": 60, "currency": "USD"},
                            "savingBasisType": "LIST_PRICE",
                        },
                    }
                }]
            },
        }

    def test_normalizes_real_amazon_data_and_preserves_original_price(self):
        product = amazon_creators.normalize_item(self._item(), "tecnologia")

        self.assertEqual(product["platform"], "amazon")
        self.assertEqual(product["external_id"], "B012345678")
        self.assertEqual(product["title"], "Auriculares inalámbricos")
        self.assertEqual(product["description"], "Marca: Central Audio · Cancelación de ruido · Bluetooth 5.3")
        self.assertEqual(product["source_metadata"]["brand"], "Central Audio")
        self.assertEqual(product["seller"], {"external_id": "seller-1", "name": "Amazon Marketplace"})
        self.assertEqual(product["price"], 30)
        self.assertEqual(product["previous_price"], 60)
        self.assertEqual(product["currency"], "USD")
        self.assertEqual(len(product["gallery_urls"]), 2)
        self.assertEqual(product["source_metadata"]["import_method"], "amazon_creators_api")

    def test_rejects_items_without_real_image_or_price(self):
        item = self._item()
        item["images"]["primary"]["large"]["url"] = "https://example.com/fake.jpg"
        self.assertIsNone(amazon_creators.normalize_item(item, "tecnologia"))
        item = self._item()
        item["offersV2"] = {}
        self.assertIsNone(amazon_creators.normalize_item(item, "tecnologia"))

    def test_marketplace_configuration_rejects_lookalike_domains(self):
        with patch.dict(os.environ, {
            "AMAZON_CREDENTIAL_ID": "id",
            "AMAZON_CREDENTIAL_SECRET": "secret",
            "AMAZON_PARTNER_TAG": "central-20",
            "AMAZON_MARKETPLACE": "evilamazon.com",
        }, clear=True):
            with self.assertRaisesRegex(RuntimeError, "AMAZON_MARKETPLACE"):
                amazon_creators._configuration()

    def test_oauth_token_is_cached_and_uses_versioned_official_endpoint(self):
        with patch.dict(os.environ, {
            "AMAZON_CREDENTIAL_ID": "credential-id",
            "AMAZON_CREDENTIAL_SECRET": "credential-secret",
            "AMAZON_PARTNER_TAG": "central-20",
            "AMAZON_CREDENTIAL_VERSION": "3.1",
            "AMAZON_MARKETPLACE": "www.amazon.com",
        }, clear=True):
            config = amazon_creators._configuration()
            response = MagicMock()
            response.ok = True
            response.json.return_value = {"access_token": "access-token", "expires_in": 3600}
            with patch.object(amazon_creators.requests, "post", return_value=response) as post:
                self.assertEqual(amazon_creators._access_token(config), "access-token")
                self.assertEqual(amazon_creators._access_token(config), "access-token")

        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.args[0], "https://api.amazon.com/auth/o2/token")
        self.assertEqual(post.call_args.kwargs["json"]["scope"], "creatorsapi::default")

    def test_search_uses_creators_api_and_requests_product_images_features_and_price(self):
        with patch.dict(os.environ, {
            "AMAZON_CREDENTIAL_ID": "credential-id",
            "AMAZON_CREDENTIAL_SECRET": "credential-secret",
            "AMAZON_PARTNER_TAG": "central-20",
            "AMAZON_CREDENTIAL_VERSION": "3.1",
            "AMAZON_MARKETPLACE": "www.amazon.com",
        }, clear=True):
            config = amazon_creators._configuration()
            response = MagicMock()
            response.ok = True
            response.json.return_value = {"searchResult": {"items": [self._item()]}}
            with (
                patch.object(amazon_creators, "_access_token", return_value="access-token"),
                patch.object(amazon_creators.requests, "post", return_value=response) as post,
            ):
                items = amazon_creators._api_search(config, "wireless headphones", 20, page=3)

        self.assertEqual(len(items), 1)
        self.assertEqual(post.call_args.args[0], "https://creatorsapi.amazon/catalog/v1/searchItems")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["keywords"], "wireless headphones")
        self.assertEqual(payload["itemCount"], 10)
        self.assertEqual(payload["itemPage"], 3)
        self.assertEqual(payload["partnerTag"], "central-20")
        self.assertIn("images.primary.large", payload["resources"])
        self.assertIn("itemInfo.features", payload["resources"])
        self.assertIn("itemInfo.byLineInfo", payload["resources"])
        self.assertIn("offersV2.listings.price", payload["resources"])
        self.assertIn("offersV2.listings.merchantInfo", payload["resources"])


if __name__ == "__main__":
    unittest.main()
