import sys
import unittest
from pathlib import Path

import requests
from requests.adapters import BaseAdapter

APP_PATH = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_PATH))

from marketplace_safety import MarketplaceAccessBlocked, marketplace_for_url


class StaticAdapter(BaseAdapter):
    def send(self, request, **kwargs):
        response = requests.Response()
        response.status_code = 200
        response.url = request.url
        response.request = request
        response._content = b"ok"
        return response

    def close(self):
        pass


class RedirectAdapter(BaseAdapter):
    def send(self, request, **kwargs):
        response = requests.Response()
        response.status_code = 302
        response.url = request.url
        response.request = request
        response.headers["Location"] = "https://www.amazon.com/dp/B000000001"
        response._content = b""
        return response

    def close(self):
        pass


class MarketplaceSafetyTests(unittest.TestCase):
    def test_blocks_marketplace_sites_apis_shorteners_and_image_cdns(self):
        urls = [
            "https://www.amazon.com/dp/B000000001",
            "https://www.amazon.co.uk/",
            "https://creatorsapi.amazon/catalog/v1/searchItems",
            "https://api.amazon.com/auth/o2/token",
            "https://m.media-amazon.com/images/I/photo.jpg",
            "https://amzn.to/example",
            "https://api-sg.aliexpress.com/sync",
            "https://www.aliexpress.us/item/123.html",
            "https://eco.taobao.com/router/rest",
            "https://s.click.aliexpress.com/e/example",
            "https://ae-pic-a1.aliexpress-media.com/image.jpg",
            "https://ae01.alicdn.com/image.jpg",
        ]
        for url in urls:
            with self.subTest(url=url):
                request = requests.Request("GET", url).prepare()
                with self.assertRaises(MarketplaceAccessBlocked):
                    requests.Session().send(request)

    def test_blocks_redirects_before_requesting_marketplace_destination(self):
        session = requests.Session()
        session.mount("https://", RedirectAdapter())
        with self.assertRaises(MarketplaceAccessBlocked):
            session.get("https://images.example.test/redirect", timeout=1)

    def test_allows_non_marketplace_hosts(self):
        session = requests.Session()
        session.mount("https://", StaticAdapter())
        response = session.get("https://images.example.test/photo.jpg", timeout=1)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")

    def test_marketplace_host_matching_does_not_match_lookalikes(self):
        self.assertEqual(marketplace_for_url("https://shop.amazon.com/item"), "Amazon")
        self.assertEqual(marketplace_for_url("https://api-sg.aliexpress.com/sync"), "AliExpress")
        self.assertIsNone(marketplace_for_url("https://notamazon.com/"))
        self.assertIsNone(marketplace_for_url("https://example.com/aliexpress.com"))

    def test_frontends_do_not_load_marketplace_images_directly(self):
        frontend = Path(__file__).resolve().parents[1] / "frontend"
        admin = (frontend / "index.html").read_text(encoding="utf-8")
        store = (frontend / "tienda.html").read_text(encoding="utf-8")
        self.assertIn("isMarketplaceImage", admin)
        self.assertNotIn("this.src=a[i]", admin)
        self.assertIn("function imageProxy", store)
        self.assertIn("isMarketplaceImage", store)
        self.assertIn("imageProxy(x.image_url)", store)
        self.assertNotIn('esc(x.image_url||"")', store)
        showcase = (Path(__file__).resolve().parents[1] / "headphones" / "src" / "App.tsx").read_text(encoding="utf-8")
        self.assertIn("const isMarketplace = marketplaceDomains.some", showcase)
        self.assertIn('return !isMarketplace && (url.protocol === "https:" || url.protocol === "http:")', showcase)


if __name__ == "__main__":
    unittest.main()
