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
        response.headers["Location"] = "https://api-sg.aliexpress.com/sync"
        response._content = b""
        return response

    def close(self):
        pass


class MarketplaceSafetyTests(unittest.TestCase):
    def test_allows_public_product_page_get_requests(self):
        cases = (
            "https://www.amazon.com/dp/B000000001",
            "https://www.amazon.co.uk/gp/product/B000000001",
            "https://www.aliexpress.com/item/123456789.html",
            "https://www.aliexpress.us/item/123456789.html",
            "https://amzn.to/example",
            "https://s.click.aliexpress.com/e/example",
        )
        for url in cases:
            with self.subTest(url=url):
                session = requests.Session()
                session.mount("https://", StaticAdapter())
                response = session.get(url, timeout=1)
                self.assertEqual(response.status_code, 200)

    def test_blocks_marketplace_apis_search_and_non_image_cdn_requests(self):
        cases = (
            ("GET", "https://creatorsapi.amazon/catalog/v1/searchItems"),
            ("GET", "https://api.amazon.com/auth/o2/token"),
            ("GET", "https://api-sg.aliexpress.com/sync"),
            ("GET", "https://eco.taobao.com/router/rest"),
            ("GET", "https://m.media-amazon.com/api/photo"),
            ("GET", "https://ae-pic-a1.aliexpress-media.com/api/image"),
            ("GET", "https://ae01.alicdn.com/api/image"),
            ("POST", "https://m.media-amazon.com/images/I/photo.jpg"),
            ("POST", "https://ae01.alicdn.com/kf/photo.jpg"),
            ("GET", "https://www.amazon.com/s?k=headphones"),
            ("POST", "https://www.amazon.com/dp/B000000001"),
            ("POST", "https://www.aliexpress.com/item/123456789.html"),
        )
        for method, url in cases:
            with self.subTest(method=method, url=url):
                request = requests.Request(method, url).prepare()
                with self.assertRaises(MarketplaceAccessBlocked):
                    requests.Session().send(request)

    def test_allows_only_get_requests_for_marketplace_image_cdn_paths(self):
        cases = (
            "https://m.media-amazon.com/images/I/photo.jpg",
            "https://images-na.ssl-images-amazon.com/images/I/photo.webp",
            "https://ae-pic-a1.aliexpress-media.com/kf/photo.jpg",
            "https://ae01.alicdn.com/kf/photo.png",
        )
        for url in cases:
            with self.subTest(url=url):
                session = requests.Session()
                session.mount("https://", StaticAdapter())
                response = session.get(url, timeout=1)
                self.assertEqual(response.status_code, 200)

    def test_blocks_redirects_to_marketplace_api_before_request(self):
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

    def test_frontends_load_marketplace_images_without_server_proxy(self):
        frontend = Path(__file__).resolve().parents[1] / "frontend"
        admin = (frontend / "index.html").read_text(encoding="utf-8")
        store = (frontend / "tienda.html").read_text(encoding="utf-8")
        self.assertIn("isMarketplaceImage", admin)
        self.assertIn("function imageProxy", store)
        self.assertIn('parsed.protocol==="https:"', store)
        self.assertIn("parsed.username&&!parsed.password", store)
        self.assertNotIn("/api/v1/media/image?url=", admin)
        self.assertNotIn("/api/v1/media/image?url=", store)
        showcase = (Path(__file__).resolve().parents[1] / "headphones" / "src" / "App.tsx").read_text(encoding="utf-8")
        self.assertIn("const isMarketplace = marketplaceDomains.some", showcase)


if __name__ == "__main__":
    unittest.main()
