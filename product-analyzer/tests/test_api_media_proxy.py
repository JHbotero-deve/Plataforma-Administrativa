import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API_SOURCE = (ROOT / "app" / "api.py").read_text(encoding="utf-8")
STORE_SOURCE = (ROOT / "frontend" / "tienda.html").read_text(encoding="utf-8")
ADMIN_SOURCE = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")


class ImageLoadingSecurityTests(unittest.TestCase):
    def test_backend_does_not_expose_arbitrary_url_image_proxy(self):
        self.assertNotIn('"/media/image"', API_SOURCE)
        self.assertNotIn("proxy_media_image", API_SOURCE)
        self.assertNotIn("HTTPSConnectionPool", API_SOURCE)
        self.assertNotIn("requests.get(current", API_SOURCE)

    def test_storefront_loads_remote_images_in_browser_without_backend_proxy(self):
        self.assertNotIn("/api/v1/media/image?url=", STORE_SOURCE)
        self.assertIn('parsed.protocol==="https:"', STORE_SOURCE)
        self.assertIn("function imageSrc(p)", STORE_SOURCE)
        self.assertIn("parsed.username&&!parsed.password", STORE_SOURCE)
        self.assertIn("function modelSrc(p)", STORE_SOURCE)
        self.assertIn("parsed.pathname", STORE_SOURCE)

    def test_admin_does_not_send_image_urls_to_backend_proxy(self):
        self.assertNotIn("/api/v1/media/image?url=", ADMIN_SOURCE)

    def test_admin_uses_http_product_gallery_instead_of_mongo_scheme(self):
        self.assertNotIn('return "mongo://" + mongo', ADMIN_SOURCE)
        self.assertIn("const productImages = p =>", ADMIN_SOURCE)
        self.assertIn("radar-gallery-image", ADMIN_SOURCE)
        self.assertIn("data-gallery-step", ADMIN_SOURCE)

    def test_top_opportunities_returns_all_product_images(self):
        route = API_SOURCE.split('@core_router.get("/opportunities/top"', 1)[1]
        self.assertIn("p.product_url, p.image_url, p.image_gallery", route)

    def test_storefront_cards_can_browse_and_fallback_between_images(self):
        self.assertIn("function imageList(p)", STORE_SOURCE)
        self.assertIn("product-gallery-image", STORE_SOURCE)
        self.assertIn("data-product-gallery-step", STORE_SOURCE)
        self.assertIn("feature-gallery-image", STORE_SOURCE)
        self.assertIn('document.addEventListener("error"', STORE_SOURCE)


if __name__ == "__main__":
    unittest.main()
