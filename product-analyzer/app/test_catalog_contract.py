import ast
import unittest
from unittest.mock import patch
import inspect
import requests
from pathlib import Path

from api import _sync_store_catalog
from url_import import detect_platform, import_url

from catalog_rules import discount_percentage
from amazon_api import (
    _access_token,
    _previous_offer,
    fetch_amazon_product_by_asin,
    search_amazon_products,
    search_products,
)
from store_orders import _public_order_summary, create_store_order
from tiktok_creator import _normalize
from wompi import create_cart_checkout, create_product_checkout


class AmazonAffiliateUrlTests(unittest.TestCase):
    def test_creators_api_uses_only_the_credential_version_endpoint(self):
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"access_token":"test-access-token","expires_in":3600}'
        with patch("amazon_api._TOKEN", None), patch("amazon_api._TOKEN_EXPIRES_AT", 0), \
             patch("amazon_api.CREDENTIAL_VERSION", "3.1"), \
             patch("amazon_api.CREDENTIAL_ID", "test-id"), \
             patch("amazon_api.CREDENTIAL_SECRET", "test-secret"), \
             patch("amazon_api.PARTNER_TAG", "test-20"), \
             patch("amazon_api.requests.post", return_value=response) as post:
            self.assertEqual(_access_token(), "test-access-token")

        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], "https://api.amazon.com/auth/o2/token")
        self.assertEqual(post.call_args.kwargs["json"]["scope"], "creatorsapi::default")

    def test_creators_api_rejects_unknown_credential_version_without_trying_other_regions(self):
        with patch("amazon_api._TOKEN", None), patch("amazon_api._TOKEN_EXPIRES_AT", 0), \
             patch("amazon_api.CREDENTIAL_VERSION", "unknown"), \
             patch("amazon_api.CREDENTIAL_ID", "test-id"), \
             patch("amazon_api.CREDENTIAL_SECRET", "test-secret"), \
             patch("amazon_api.PARTNER_TAG", "test-20"), \
             patch("amazon_api.requests.post") as post:
            with self.assertRaisesRegex(RuntimeError, "AMAZON_CREDENTIAL_VERSION"):
                _access_token()
        post.assert_not_called()

    def test_getitems_uses_the_asin_and_current_offer_resources(self):
        item = {
            "asin": "B0HJB69GJL",
            "detailPageURL": "https://www.amazon.com/dp/B0HJB69GJL",
            "itemInfo": {"title": {"displayValue": "Auriculares"}},
            "offersV2": {"listings": [{
                "price": {"amount": 199.99, "currency": "USD"},
            }]},
        }
        with patch(
            "amazon_api._request",
            return_value={"itemsResult": {"items": [item]}},
        ) as api_request:
            product = fetch_amazon_product_by_asin("b0hjb69gjl", "electronica")

        self.assertEqual(api_request.call_args.args[0], "getItems")
        self.assertEqual(api_request.call_args.args[1]["itemIds"], ["B0HJB69GJL"])
        self.assertEqual(api_request.call_args.args[1]["itemIdType"], "ASIN")
        self.assertIn("offersV2.listings.price", api_request.call_args.args[1]["resources"])
        self.assertEqual(product["price"], 199.99)

    def test_catalog_sync_searches_amazon_only_with_official_partner_credentials(self):
        source = inspect.getsource(_sync_store_catalog)
        self.assertIn("search_amazon_products", source)
        self.assertIn("AMAZON_CREDENTIAL_ID", source)
        self.assertIn("AMAZON_PARTNER_TAG", source)
        self.assertIn("prime_only=True", source)
        self.assertIn("official_only=True", source)
        self.assertIn("min_saving_percent=int(threshold)", source)

    def test_prime_search_uses_official_api_filters_and_minimum_savings(self):
        response = {
            "searchResult": {
                "items": [{
                    "asin": "B012345678",
                    "detailPageURL": "https://www.amazon.com/dp/B012345678",
                    "itemInfo": {"title": {"displayValue": "Prime headphones"}},
                    "images": {"primary": {"large": {"url": "https://images.example.com/a.jpg"}}},
                    "offersV2": {"listings": [{
                        "price": {"amount": 50, "currency": "USD"},
                        "savingBasis": {"money": {"amount": 100, "currency": "USD"}},
                    }]},
                }]
            }
        }
        with patch("amazon_api._AUTH_FAILURE", False), patch(
            "amazon_api._request", return_value=response
        ) as api_request, patch("amazon_api._search_amazon_html") as html_fallback, patch(
            "amazon_api._search_amazon_web"
        ) as web_fallback:
            products = search_amazon_products(
                "headphones",
                prime_only=True,
                min_saving_percent=40,
                official_only=True,
            )

        payload = api_request.call_args.args[1]
        self.assertEqual(payload["deliveryFlags"], ["Prime"])
        self.assertEqual(payload["minSavingPercent"], 40)
        self.assertEqual(payload["availability"], "Available")
        self.assertEqual(payload["condition"], "New")
        self.assertEqual(products[0]["previous_price"], 100)
        html_fallback.assert_not_called()
        web_fallback.assert_not_called()

    def test_official_prime_search_does_not_scrape_when_no_eligible_results(self):
        with patch("amazon_api._AUTH_FAILURE", False), patch(
            "amazon_api._request", return_value={"searchResult": {"items": []}}
        ), patch("amazon_api._search_amazon_html") as html_fallback, patch(
            "amazon_api._search_amazon_web"
        ) as web_fallback:
            self.assertEqual(
                search_amazon_products("headphones", prime_only=True, official_only=True),
                [],
            )
        html_fallback.assert_not_called()
        web_fallback.assert_not_called()

    def test_official_amazon_api_errors_do_not_fall_back_to_scraping(self):
        with patch("amazon_api._AUTH_FAILURE", False), patch(
            "amazon_api._request", side_effect=requests.RequestException("API unavailable")
        ), patch("amazon_api._search_amazon_html") as html_fallback, patch(
            "amazon_api._search_amazon_web"
        ) as web_fallback:
            with self.assertRaisesRegex(RuntimeError, "Amazon Creators API falló"):
                search_products("headphones", official_only=True, delivery_flags=["Prime"])
        html_fallback.assert_not_called()
        web_fallback.assert_not_called()


    def test_import_preserves_supplied_amazon_affiliate_url_and_manual_real_data(self):
        affiliate_url = "https://www.amazon.com/dp/B0ABCDEF12?tag=partner-20"
        with patch("url_import._public_url"), patch(
            "url_import._metadata", side_effect=requests.RequestException("lectura bloqueada")
        ):
            product = import_url(
                affiliate_url,
                "electronica",
                title="Audífonos reales",
                price=129.99,
                currency="USD",
                image_url="https://images.example.com/audifonos.jpg",
                require_image=False,
            )
        self.assertEqual(product["product_url"], affiliate_url)
        self.assertEqual(product["affiliate_url"], affiliate_url)
        self.assertEqual(product["price"], 129.99)
        self.assertEqual(product["image_url"], "https://images.example.com/audifonos.jpg")

    def test_amazon_url_import_uses_creators_api_asin_lookup(self):
        url = "https://www.amazon.com/-/es/product/dp/B0HJB69GJL?tag=partner-20"
        api_product = {
            "product_url": "https://www.amazon.com/dp/B0HJB69GJL?tag=partner-20",
            "title": "Auriculares inalámbricos",
            "description": "Producto devuelto por la API",
            "brand": "Amazon Brand",
            "price": 199.99,
            "currency": "USD",
            "image_url": "https://images.example.com/airpods.jpg",
            "gallery_urls": ["https://images.example.com/airpods.jpg"],
            "rating": None,
            "reviews_count": 0,
            "seller": {"name": "Amazon"},
        }
        with patch("url_import._public_url"), patch(
            "url_import.creators_api_configured", return_value=True
        ), patch("url_import.fetch_amazon_product_by_asin", return_value=api_product) as lookup, patch(
            "url_import._metadata"
        ) as scraper:
            product = import_url(url, "electronica")

        lookup.assert_called_once_with("B0HJB69GJL", "electronica")
        scraper.assert_not_called()
        self.assertEqual(product["external_id"], "B0HJB69GJL")
        self.assertEqual(product["title"], "Auriculares inalámbricos")
        self.assertEqual(product["price"], 199.99)
        self.assertEqual(product["affiliate_url"], url)
        self.assertEqual(product["source_metadata"]["metadata_source"], "amazon_creators_api")

    def test_amazon_creators_api_failure_falls_back_to_public_metadata(self):
        url = "https://www.amazon.com/dp/B0HJB69GJL"
        page = {
            "resolved_url": url,
            "title": "Auriculares desde metadatos",
            "description": "Descripción pública",
            "price": 79.99,
            "currency": "USD",
            "image_url": "https://images.example.com/headphones.jpg",
            "gallery_urls": ["https://images.example.com/headphones.jpg"],
            "metadata_source": "open_graph+jsonld",
        }
        with patch("url_import._public_url"), patch(
            "url_import.creators_api_configured", return_value=True
        ), patch(
            "url_import.fetch_amazon_product_by_asin", side_effect=RuntimeError("API no disponible")
        ), patch("url_import._metadata", return_value=page) as scraper:
            product = import_url(url, "electronica")
        scraper.assert_called_once_with(url)
        self.assertEqual(product["title"], "Auriculares desde metadatos")
        self.assertEqual(product["price"], 79.99)
        self.assertIn("API no disponible", product["source_metadata"]["import_warning"])

    def test_amazon_503_without_creators_api_preserves_url_as_draft(self):
        response = requests.Response()
        response.status_code = 503
        error = requests.HTTPError("503 Server Error", response=response)
        with patch("url_import._public_url"), patch(
            "url_import.creators_api_configured", return_value=False
        ), patch("url_import._metadata", side_effect=error):
            product = import_url("https://www.amazon.com/dp/B0HJB69GJL", "electronica")
        self.assertEqual(product["platform"], "amazon")
        self.assertTrue(product["source_metadata"]["draft"])
        self.assertIn("No se pudieron leer los metadatos de la página", product["source_metadata"]["import_warning"])
        self.assertEqual(product["product_url"], "https://www.amazon.com/dp/B0HJB69GJL")

    def test_mercado_libre_urls_are_rejected_as_disabled_source(self):
        with self.assertRaisesRegex(ValueError, "Mercado Libre ya no está habilitado"):
            detect_platform("https://articulo.mercadolibre.com.co/MCO-123456")

    def test_generic_public_store_url_is_imported_as_personal_draft_or_product(self):
        page = '''
        <html><head>
        <meta property="og:title" content="Audífonos de prueba">
        <meta property="og:image" content="https://shop.example/images/item.jpg">
        <meta property="product:price:amount" content="129900">
        <meta property="product:price:currency" content="COP">
        </head></html>
        '''
        with patch("url_import._public_url"), patch(
            "url_import.requests.get"
        ) as get:
            response = requests.Response()
            response.status_code = 200
            response.url = "https://shop.example/products/headphones"
            response._content = page.encode()
            response.encoding = "utf-8"
            get.return_value = response
            product = import_url("https://shop.example/products/headphones", "electronica")
        self.assertEqual(product["platform"], "personal")
        self.assertEqual(product["title"], "Audífonos de prueba")
        self.assertEqual(product["price"], 129900.0)
        self.assertEqual(product["currency"], "COP")
        self.assertEqual(product["image_url"], "https://shop.example/images/item.jpg")

    def test_import_preserves_manual_aliexpress_affiliate_url_when_store_blocks_reading(self):
        affiliate_url = "https://s.click.aliexpress.com/e/_example"
        image_url = "https://images.example.com/product.jpg"
        with patch("url_import._public_url"), patch(
            "url_import._metadata", side_effect=requests.RequestException("lectura bloqueada")
        ):
            product = import_url(
                affiliate_url,
                "electronica",
                title="Audífonos reales",
                price=12.49,
                currency="USD",
                image_url=image_url,
            )

        self.assertEqual(product["platform"], "aliexpress")
        self.assertEqual(product["affiliate_url"], affiliate_url)
        self.assertEqual(product["price"], 12.49)
        self.assertEqual(product["image_url"], image_url)


class TelegramCatalogContractTests(unittest.TestCase):
    def test_telegram_catalog_queries_show_publishable_products_with_ids(self):
        path = Path(__file__).with_name("telegram_alert.py")
        source = path.read_text(encoding="utf-8")
        module = ast.parse(source)
        functions = {
            node.name: ast.get_source_segment(source, node)
            for node in module.body
            if isinstance(node, ast.FunctionDef)
        }
        for name in ("_top_products", "_catalog_products", "_search_products"):
            with self.subTest(query=name):
                self.assertIn("p.id AS product_id", functions[name])
                self.assertIn("p.current_price > 0", functions[name])
                self.assertIn("p.catalog_expires_at IS NULL OR p.catalog_expires_at > NOW()", functions[name])
        self.assertIn("'tiktok'", functions["_catalog_products"])
        self.assertIn("Publicar:", functions["_format_product"])
        self.assertIn("products = search_external_products(term, limit=8)", functions["_handle"])
        self.assertNotIn("_search_products(term)", functions["_handle"])
        self.assertIn("search_external_products", source)

    def test_telegram_status_counts_only_active_unexpired_products(self):
        path = Path(__file__).with_name("telegram_alert.py")
        source = path.read_text(encoding="utf-8")
        module = ast.parse(source)
        handler = next(
            node for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == "_handle"
        )
        source = ast.get_source_segment(source, handler)
        self.assertIn("Ofertas activas con precio:", source)
        self.assertIn("p.is_blocked = FALSE", source)
        self.assertIn("p.current_price > 0", source)
        self.assertIn("p.catalog_expires_at IS NULL OR p.catalog_expires_at > NOW()", source)

    def test_telegram_help_lists_url_import_for_all_supported_platforms(self):
        source = Path(__file__).with_name("telegram_alert.py").read_text(encoding="utf-8")
        self.assertIn("Amazon, TikTok Shop o AliExpress", source)
        self.assertNotIn("Amazon, Mercado Libre", source)
        self.assertIn("/inyectar", source)

    def test_store_search_uses_only_central_publications(self):
        source = (Path(__file__).resolve().parents[1] / "frontend" / "tienda.html").read_text(encoding="utf-8")
        self.assertIn('API+"/publications?limit="+pageSize+"&offset="+offset', source)
        self.assertIn('data-mode="discount">🔥 40%+ descuento', source)
        self.assertIn("discountPercent(p)>=40", source)
        self.assertIn("verified_discount_pct", source)
        self.assertNotIn("meta.promotion_discount_pct", source)

    def test_store_discount_badge_requires_verified_ingestion_metadata(self):
        from publications import list_publications

        source = inspect.getsource(list_publications)
        self.assertIn("p.source_metadata->>'catalog_ingestion' = 'verified_discount_search'", source)
        self.assertIn("AS verified_discount_pct", source)

    def test_telegram_publication_rejects_invalid_or_expired_products(self):
        source = Path(__file__).with_name("telegram_alert.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        publish = next(
            node for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == "_publish_product"
        )
        source = ast.get_source_segment(source, publish)
        self.assertIn("p.is_blocked=FALSE", source)
        self.assertIn("p.current_price>0", source)
        self.assertIn("p.catalog_expires_at IS NULL OR p.catalog_expires_at>NOW()", source)


class StoreOrderSecurityTests(unittest.TestCase):
    def test_public_order_summary_does_not_expose_cost_or_profit(self):
        order = {
            "id": 1, "reference": "REQ-TEST", "total_amount": 50000,
            "estimated_profit": 20000, "cost_unit_price": 30000,
            "currency": "COP", "status": "PENDING", "payment_status": "PENDING",
        }
        result = _public_order_summary(order)
        self.assertEqual(set(result), {"id", "reference", "total_amount", "currency", "status", "payment_status"})
        self.assertNotIn("estimated_profit", result)
        self.assertNotIn("cost_unit_price", result)

    def test_store_orders_only_accept_visible_unblocked_unexpired_products(self):
        import inspect
        source = inspect.getsource(create_store_order)
        self.assertIn("JOIN published_cards pc", source)
        self.assertNotIn("LEFT JOIN published_cards pc", source)
        self.assertIn("p.is_blocked=FALSE", source)
        self.assertIn("p.catalog_expires_at > NOW()", source)


class WompiCheckoutContractTests(unittest.TestCase):
    def test_product_checkout_requires_a_live_published_product(self):
        source = inspect.getsource(create_product_checkout)
        self.assertIn("JOIN published_cards pc", source)
        self.assertIn("pc.is_published=TRUE", source)
        self.assertIn("p.is_blocked=FALSE", source)
        self.assertIn("p.current_price>0", source)
        self.assertIn("p.catalog_expires_at IS NULL OR p.catalog_expires_at>NOW()", source)

    def test_cart_checkout_requires_live_published_products(self):
        source = inspect.getsource(create_cart_checkout)
        self.assertIn("JOIN published_cards pc", source)
        self.assertIn("pc.is_published=TRUE", source)
        self.assertIn("p.is_blocked=FALSE", source)
        self.assertIn("p.current_price>0", source)
        self.assertIn("p.catalog_expires_at IS NULL OR p.catalog_expires_at>NOW()", source)


class DiscountRuleTests(unittest.TestCase):
    def test_accepts_real_discount(self):
        self.assertEqual(discount_percentage(50, 100), 50.0)

    def test_rejects_no_discount_or_invalid_price(self):
        self.assertIsNone(discount_percentage(100, 100))
        self.assertIsNone(discount_percentage(110, 100))
        self.assertIsNone(discount_percentage(None, 100))
        self.assertIsNone(discount_percentage(0, 100))

    def test_amazon_reference_price_is_only_used_when_higher(self):
        item = {"offersV2": {"listings": [{
            "price": {"amount": 50, "currency": "USD"},
            "savingBasis": {"money": {"amount": 100, "currency": "USD"}}
        }]}}
        self.assertEqual(_previous_offer(item), 100.0)

    def test_normalize_preserves_all_shop_images_and_affiliate_link(self):
        product = {
            "product_id": "shop-123",
            "title": "Producto TikTok",
            "price": {"sale_price": {"amount": "19.99", "currency": "USD"}},
            "images": [
                {"url": "https://cdn.example.com/a.jpg"},
                {"url_list": ["https://cdn.example.com/b.jpg"]},
                {"url": "https://cdn.example.com/a.jpg"},
                {},
            ],
            "product_url": "https://shop.example.com/item/123",
            "affiliate_url": "https://affiliate.example.com/item/123",
        }
        normalized = _normalize(product)
        self.assertEqual(
            normalized["gallery_urls"],
            ["https://cdn.example.com/a.jpg", "https://cdn.example.com/b.jpg"],
        )
        self.assertEqual(normalized["image_url"], "https://cdn.example.com/a.jpg")
        self.assertEqual(normalized["affiliate_url"], "https://affiliate.example.com/item/123")


if __name__ == "__main__":
    unittest.main()



class CatalogPublicationPolicyTests(unittest.TestCase):
    def test_auto_publication_policy_uses_score_and_card_limit(self):
        from api import _publish_external_product
        source = inspect.getsource(_publish_external_product)
        self.assertIn('CATALOG_MIN_DISCOUNT_PCT", "40"', source)
        self.assertIn("CATALOG_MAX_PUBLISHED_CARDS", source)
        self.assertIn('CATALOG_EXPIRY_DAYS", "7"', source)

    def test_expiry_waits_for_a_successful_complete_provider_sync(self):
        source = inspect.getsource(_sync_store_catalog)
        self.assertIn('"CATALOG_SYNC_MAX_PRODUCTS", "500"', source)
        self.assertIn('"CATALOG_MIN_DISCOUNT_PCT", "40"', source)
        self.assertIn("catalog_expires_at<=NOW()", source)
        self.assertIn("pg_try_advisory_lock", source)
