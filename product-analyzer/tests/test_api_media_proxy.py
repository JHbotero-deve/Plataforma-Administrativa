import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API_SOURCE = (ROOT / "app" / "api.py").read_text(encoding="utf-8")
STORE_SOURCE = (ROOT / "frontend" / "tienda.html").read_text(encoding="utf-8")
ADMIN_SOURCE = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
PUBLICATION_SOURCE = (ROOT / "app" / "publications.py").read_text(encoding="utf-8")


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

    def test_admin_product_cards_use_all_images_and_retry_failed_sources(self):
        self.assertIn("const productImages = p =>", ADMIN_SOURCE)
        self.assertIn("data-gallery-images", ADMIN_SOURCE)
        self.assertIn("data-gallery-step", ADMIN_SOURCE)
        self.assertIn("setGalleryImage(gallery", ADMIN_SOURCE)
        self.assertNotIn('return "mongo://" + mongo', ADMIN_SOURCE)

    def test_storefront_cards_and_featured_product_use_image_gallery(self):
        self.assertIn("function imageImages(p)", STORE_SOURCE)
        self.assertIn("imageGalleryMarkup(p,images", STORE_SOURCE)
        self.assertIn("data-gallery-images", STORE_SOURCE)
        self.assertIn("data-gallery-step", STORE_SOURCE)
        self.assertIn("data-image-index", STORE_SOURCE)
        self.assertIn("setGalleryImage(gallery", STORE_SOURCE)

    def test_publication_api_returns_the_complete_product_gallery(self):
        self.assertIn("p.image_gallery", PUBLICATION_SOURCE)


if __name__ == "__main__":
    unittest.main()
