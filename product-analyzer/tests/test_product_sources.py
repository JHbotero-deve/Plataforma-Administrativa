import unittest
from unittest.mock import MagicMock, patch

import product_sources
from url_import import import_url


class ProductSourcesTests(unittest.TestCase):
    def test_url_only_import_keeps_unknown_product_data_empty(self):
        url = "https://www.amazon.com/dp/B012345678"
        with patch.object(product_sources, "_resolve_product_url", return_value=url) as resolve:
            product = import_url(url)
        resolve.assert_called_once_with(url, "amazon")
        self.assertEqual(product["title"], "Producto Amazon B012345678")
        self.assertIsNone(product["category"])
        self.assertIsNone(product["price"])
        self.assertIsNone(product["image_url"])
        self.assertEqual(product["gallery_urls"], [])
        self.assertIsNone(product["rating"])
        self.assertEqual(product["reviews_count"], 0)
        self.assertIsNone(product["sales_estimate"])
        self.assertEqual(product["source_metadata"]["missing_fields"], ["title", "price", "category", "image_url"])

    def test_url_import_preserves_only_cli_metadata_that_was_supplied(self):
        url = "https://www.amazon.com/dp/B012345678"
        with patch.object(product_sources, "_resolve_product_url", return_value=url):
            product = import_url(
                url,
                category="calzado",
                title="Zapatillas de prueba",
                price=49.90,
                image_url="https://images.example/zapatillas.jpg",
            )
        self.assertEqual(product["title"], "Zapatillas de prueba")
        self.assertEqual(product["category"], "calzado")
        self.assertEqual(product["price"], 49.90)
        self.assertEqual(product["image_url"], "https://images.example/zapatillas.jpg")
        self.assertEqual(product["gallery_urls"], ["https://images.example/zapatillas.jpg"])
        self.assertIsNone(product["rating"])
        self.assertEqual(product["reviews_count"], 0)
        self.assertIsNone(product["sales_estimate"])
        self.assertEqual(product["source_metadata"]["missing_fields"], [])


    def test_amazon_public_jsonld_extracts_product_metadata_without_api(self):
        response = MagicMock()
        response.status_code = 200
        response.text = '''<html><meta property="og:title" content="Tenis deportivos"><meta property="og:image" content="https://images.example/tenis.jpg"><script type="application/ld+json">{"@type":"Product","name":"Tenis deportivos","offers":{"price":"49.99","priceCurrency":"USD"}}</script></html>'''
        with patch.object(product_sources.requests, "get", return_value=response):
            result = product_sources._public_product_page("https://www.amazon.com/dp/B012345678", "amazon")
        self.assertEqual(result["title"], "Tenis deportivos")
        self.assertEqual(result["price"], 49.99)
        self.assertEqual(result["currency"], "USD")
        self.assertEqual(result["image_url"], "https://images.example/tenis.jpg")
        response.close.assert_called_once()

    def test_aliexpress_product_detail_accepts_core_fields_without_promotion_link_or_discount(self):
        payload = {
            "aliexpress_affiliate_productdetail_get_response": {
                "resp_result": {
                    "resp_code": 200,
                    "result": {
                        "products": {
                            "product": [{
                                "product_id": "1005012999805372",
                                "product_title": "Miniexcavadora de 2.5 toneladas",
                                "product_detail_url": "https://www.aliexpress.com/item/1005012999805372.html",
                                "product_main_image_url": "https://ae01.alicdn.com/kf/excavator.jpg",
                                "product_small_image_urls": {"string": ["https://ae01.alicdn.com/kf/excavator.jpg"]},
                                "target_sale_price": "1299.00",
                                "target_sale_price_currency": "USD",
                                "target_original_price": "1599.00",
                                "shop_id": "shop-123",
                                "shop_url": "https://www.aliexpress.com/store/123",
                                "lastest_volume": "18"
                            }]
                        }
                    }
                }
            }
        }
        with patch.dict("os.environ", {"ALIEXPRESS_APP_KEY": "key", "ALIEXPRESS_APP_SECRET": "secret", "ALIEXPRESS_TRACKING_ID": "central"}):
            with patch("affiliate_links.aliexpress_api_request", return_value=payload) as api:
                product = product_sources._aliexpress_product_data("1005012999805372")
        api.assert_called_once()
        self.assertEqual(api.call_args.args[0], "aliexpress.affiliate.productdetail.get")
        self.assertEqual(api.call_args.args[1]["country"], "CO")
        self.assertEqual(product["title"], "Miniexcavadora de 2.5 toneladas")
        self.assertEqual(product["price"], 1299.0)
        self.assertEqual(product["previous_price"], 1599.0)
        self.assertEqual(product["image_url"], "https://ae01.alicdn.com/kf/excavator.jpg")
        self.assertEqual(product["sales_estimate"], 18)
        self.assertEqual(product["seller"]["name"], "Tienda AliExpress #shop-123")
        self.assertEqual(product["source_metadata"]["metadata_source"], "aliexpress_affiliate_api")

    def test_aliexpress_public_jsonld_extracts_product_metadata(self):
        response = MagicMock()
        response.status_code = 200
        response.text = '''<html><script type="application/ld+json">{"@type":"Product","name":"Bolso de viaje","image":"https://img.alicdn.com/bolso.jpg","offers":{"price":"25.50","priceCurrency":"USD"}}</script></html>'''
        with patch.object(product_sources.requests, "get", return_value=response):
            result = product_sources._public_product_page("https://www.aliexpress.com/item/1005001234567890.html", "aliexpress")
        self.assertEqual(result["title"], "Bolso de viaje")
        self.assertEqual(result["price"], 25.5)
        self.assertEqual(result["currency"], "USD")
        self.assertEqual(result["image_url"], "https://img.alicdn.com/bolso.jpg")

    def test_public_page_extracts_seller_name_and_store_url(self):
        response = MagicMock()
        response.status_code = 200
        response.text = '''<html><script type="application/ld+json">{"@type":"Product","name":"Bolso","offers":{"price":"25.50","priceCurrency":"USD","seller":{"@type":"Organization","name":"Tienda Central","url":"https://www.aliexpress.com/store/123"}}}</script></html>'''
        with patch.object(product_sources.requests, "get", return_value=response):
            result = product_sources._public_product_page("https://www.aliexpress.com/item/123.html", "aliexpress")
        self.assertEqual(result["seller"]["name"], "Tienda Central")
        self.assertEqual(result["seller"]["external_id"], "https://www.aliexpress.com/store/123")
        self.assertEqual(result["seller_url"], "https://www.aliexpress.com/store/123")

    def test_public_page_extracts_original_price_and_description(self):
        response = MagicMock()
        response.status_code = 200
        response.text = '''<html><meta property="og:title" content="Bolso"><meta property="og:description" content="Bolso resistente"><meta property="og:price:amount" content="60"><meta property="og:price:standard_amount" content="100"><meta property="product:price:currency" content="USD"><meta property="og:image" content="https://img.example/bolso.jpg"></html>'''
        with patch.object(product_sources.requests, "get", return_value=response):
            result = product_sources._public_product_page("https://www.aliexpress.com/item/123.html", "aliexpress")
        self.assertEqual(result["price"], 60)
        self.assertEqual(result["previous_price"], 100)
        self.assertEqual(result["description"], "Bolso resistente")

    def test_public_page_rejects_untrusted_host(self):
        with self.assertRaises(ValueError):
            product_sources._public_product_page("https://evil.example/product", "amazon")

    def test_short_amazon_url_resolves_only_inside_allowed_hosts(self):
        first = MagicMock()
        first.status_code = 302
        first.headers = {"Location": "https://www.amazon.com/dp/B012345678"}
        second = MagicMock()
        second.status_code = 200
        second.text = "<html><meta property='og:title' content='Producto Amazon'></html>"
        with patch.object(product_sources.requests, "get", side_effect=[first, second]):
            result = product_sources._public_product_page("https://amzn.to/example", "amazon")
        self.assertEqual(result["resolved_url"], "https://www.amazon.com/dp/B012345678")
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_link_amazon_short_link_resolves_to_amazon_product(self):
        first = MagicMock()
        first.status_code = 302
        first.headers = {"Location": "https://www.amazon.com/dp/B012345678"}
        second = MagicMock()
        second.status_code = 200
        second.text = '<html><meta property="og:title" content="Producto Amazon"></html>'
        with patch.object(product_sources.requests, "get", side_effect=[first, second]) as request:
            result = product_sources._public_product_page("https://link.amazon/B06t7Yht8", "amazon")
        self.assertEqual(result["resolved_url"], "https://www.amazon.com/dp/B012345678")
        self.assertEqual(request.call_count, 2)
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_amazon_url_import_never_resolves_or_queries_marketplace(self):
        product = {
            "platform": "amazon",
            "external_id": "URL-SHORT-LINK",
            "product_url": "https://link.amazon/B06t7Yht8",
            "title": "Producto Amazon URL-SHORT-LINK",
            "source_metadata": {},
        }
        with (
            patch.object(product_sources, "_resolve_product_url") as resolve,
            patch.object(product_sources, "_official_product_data") as official,
            patch.object(product_sources, "_public_product_page") as public_page,
        ):
            result = product_sources.enrich_product(product)
        resolve.assert_not_called()
        official.assert_not_called()
        public_page.assert_not_called()
        self.assertEqual(result["product_url"], product["product_url"])
        self.assertEqual(result["external_id"], "URL-SHORT-LINK")
        self.assertTrue(result["source_metadata"]["network_enrichment_disabled"])
        self.assertEqual(result["source_metadata"]["enrichment_status"], "paused")

    def test_amazon_asin_is_extracted_locally_without_network(self):
        product = {
            "platform": "amazon",
            "product_url": "https://www.amazon.com/dp/B012345678",
            "title": "Producto Amazon B012345678",
            "source_metadata": {},
        }
        with (
            patch.object(product_sources, "_resolve_product_url") as resolve,
            patch.object(product_sources, "_official_product_data") as official,
            patch.object(product_sources, "_public_product_page") as public_page,
        ):
            result = product_sources.enrich_product(product)
        resolve.assert_not_called()
        official.assert_not_called()
        public_page.assert_not_called()
        self.assertEqual(result["external_id"], "B012345678")
        self.assertEqual(result["product_url"], product["product_url"])
        self.assertEqual(result["source_metadata"]["metadata_source"], "url_only")

    def test_pending_enrichment_pauses_without_network_or_affiliate_generation(self):
        row = {
            "id": 7, "external_id": "URL-SHORT-LINK", "platform": "amazon",
            "product_url": "https://link.amazon/B06t7Yht8",
            "title": "Producto Amazon URL-SHORT-LINK", "current_price": None,
            "currency": "USD", "image_url": None, "image_gallery": [],
            "rating": None, "reviews_count": 0, "category": None,
            "source_metadata": {}, "seller_external_id": None, "seller_name": None,
        }
        select_cursor = MagicMock()
        select_cursor.fetchall.return_value = [row]
        update_cursor = MagicMock()
        conn = MagicMock()
        conn.cursor.side_effect = [
            MagicMock(__enter__=MagicMock(return_value=select_cursor), __exit__=MagicMock(return_value=False)),
            MagicMock(__enter__=MagicMock(return_value=update_cursor), __exit__=MagicMock(return_value=False)),
        ]
        with (
            patch.object(product_sources, "_resolve_product_url") as resolve,
            patch.object(product_sources, "_official_product_data") as official,
            patch.object(product_sources, "_public_product_page") as public_page,
            patch.object(product_sources, "_affiliate_url") as affiliate,
        ):
            recovered = product_sources.enrich_pending_products(conn, limit=1)
        self.assertEqual(recovered, 0)
        resolve.assert_not_called()
        official.assert_not_called()
        public_page.assert_not_called()
        affiliate.assert_not_called()
        values = update_cursor.execute.call_args.args[1]
        self.assertIsNone(values[3])
        self.assertIsNone(values[4])
        self.assertIn('"network_enrichment_disabled": true', values[-2])
        conn.commit.assert_called_once()

    def test_link_amazon_redirect_outside_allowed_hosts_is_rejected(self):
        response = MagicMock()
        response.status_code = 302
        response.headers = {"Location": "https://redirector.example/product"}
        with patch.object(product_sources.requests, "get", return_value=response):
            with self.assertRaisesRegex(ValueError, "fuera de los dominios permitidos"):
                product_sources._public_product_page("https://link.amazon/B06t7Yht8", "amazon")
        response.close.assert_called_once()

    def test_aliexpress_short_link_resolves_after_more_than_six_redirects(self):
        redirects = []
        for index in range(7):
            response = MagicMock()
            response.status_code = 302
            response.headers = {"Location": f"https://www.aliexpress.com/redirect/{index}"}
            redirects.append(response)
        final = MagicMock()
        final.status_code = 200
        final.text = '''<html><script type="application/ld+json">{"@type":"Product","name":"Producto AliExpress","offers":{"price":"19.90","priceCurrency":"USD"}}</script></html>'''
        with patch.object(product_sources.requests, "get", side_effect=redirects + [final]) as request:
            result = product_sources._public_product_page("https://s.click.aliexpress.com/e/_mOOSCcP", "aliexpress")
        self.assertEqual(result["title"], "Producto AliExpress")
        self.assertEqual(result["price"], 19.9)
        self.assertEqual(request.call_count, 8)
        for response in redirects:
            response.close.assert_called_once()
        final.close.assert_called_once()

    def test_price_parser_handles_common_formats(self):
        self.assertEqual(product_sources._number("$1,299.00"), 1299.0)
        self.assertEqual(product_sources._number("1.299,00 €"), 1299.0)
        self.assertIsNone(product_sources._number("Consultar"))

    def test_redirect_cycle_fails_fast_without_repeating_login_requests(self):
        first = MagicMock()
        first.status_code = 302
        first.headers = {"Location": "https://login.aliexpress.us/check"}
        second = MagicMock()
        second.status_code = 302
        second.headers = {"Location": "https://login.aliexpress.com/check"}
        third = MagicMock()
        third.status_code = 302
        third.headers = {"Location": "https://login.aliexpress.us/check"}
        with patch.object(product_sources.requests, "get", side_effect=[first, second, third]) as request:
            with self.assertRaisesRegex(RuntimeError, "ciclo de redirección"):
                product_sources._public_product_page("https://s.click.aliexpress.com/e/_mOOSCcP", "aliexpress")
        self.assertEqual(request.call_count, 3)
        first.close.assert_called_once()
        second.close.assert_called_once()
        third.close.assert_called_once()

    def test_category_is_only_assigned_when_metadata_identifies_one(self):
        self.assertEqual(product_sources._category("Shoes"), "calzado")
        self.assertEqual(product_sources._category("Women's clothing"), "ropa")
        self.assertEqual(product_sources._category("Phone case"), "accesorios")
        self.assertIsNone(product_sources._category("Uncategorized product"))

    def test_url_import_pauses_enrichment_without_retrying_marketplace(self):
        product = {
            "platform": "aliexpress",
            "product_url": "https://s.click.aliexpress.com/e/_mOOSCcP",
            "title": "Producto AliExpress URL-123",
            "source_metadata": {},
        }
        with (
            patch.object(product_sources, "_resolve_product_url") as resolve,
            patch.object(product_sources, "_official_product_data") as official,
            patch.object(product_sources, "_public_product_page") as public_page,
        ):
            result = product_sources.enrich_product(product)
        resolve.assert_not_called()
        official.assert_not_called()
        public_page.assert_not_called()
        self.assertEqual(result["product_url"], product["product_url"])
        self.assertEqual(result["source_metadata"]["enrichment_status"], "paused")
        self.assertTrue(result["source_metadata"]["network_enrichment_disabled"])
        self.assertFalse(product_sources._retry_due(result["source_metadata"]))

    def test_marketplace_api_403_is_never_requested_during_url_import(self):
        product = {
            "platform": "aliexpress",
            "external_id": "1005012999805372",
            "product_url": "https://www.aliexpress.com/item/1005012999805372.html",
            "title": "Producto AliExpress 1005012999805372",
            "source_metadata": {},
        }
        with (
            patch.object(product_sources, "_official_product_data", side_effect=AssertionError("API call attempted")) as official,
            patch.object(product_sources, "_public_product_page", side_effect=AssertionError("HTTP call attempted")) as public_page,
        ):
            result = product_sources.enrich_product(product)
        official.assert_not_called()
        public_page.assert_not_called()
        self.assertEqual(result["source_metadata"]["enrichment_status"], "paused")
        self.assertTrue(result["source_metadata"]["network_enrichment_disabled"])

    def test_pending_enrichment_does_not_fetch_marketplace_data(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [{
            "id": 12, "title": "Producto AliExpress URL-123", "current_price": None,
            "currency": "USD", "image_url": None, "image_gallery": [], "rating": None,
            "reviews_count": 0, "product_url": "https://www.aliexpress.com/item/123.html",
            "platform": "aliexpress", "category": None, "source_metadata": {},
            "external_id": None, "seller_external_id": None, "seller_name": None,
        }]
        with (
            patch.object(product_sources, "_official_product_data") as official,
            patch.object(product_sources, "_public_product_page") as public_page,
            patch.object(product_sources, "_aliexpress_product_by_title") as title_search,
        ):
            recovered = product_sources.enrich_pending_products(connection, limit=1)
        self.assertEqual(recovered, 0)
        official.assert_not_called()
        public_page.assert_not_called()
        title_search.assert_not_called()
        update = cursor.execute.call_args_list[-1]
        self.assertIn('"network_enrichment_disabled": true', update.args[1][-2])
        connection.commit.assert_called_once()

    def test_complete_cli_product_uses_title_category_without_fetching_page(self):
        product = {
            "platform": "aliexpress",
            "product_url": "https://www.aliexpress.com/item/123.html",
            "title": "Men running shoes",
            "price": 19.9,
            "image_url": "https://img.example/shoes.jpg",
            "source_metadata": {"metadata_source": "cli"},
        }
        with patch.object(product_sources, "_public_product_page") as fetch:
            result = product_sources.enrich_product(product)
        fetch.assert_not_called()
        self.assertEqual(result["category"], "calzado")
        self.assertEqual(result["source_metadata"]["category_source"], "title_inference")

    def test_login_html_is_not_mistaken_for_product_metadata(self):
        response = MagicMock()
        response.status_code = 200
        response.url = "https://login.aliexpress.com/"
        response.text = '<html><meta property="og:title" content="Sign in to AliExpress"></html>'
        with patch.object(product_sources.requests, "get", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "exige iniciar sesión"):
                product_sources._public_product_page("https://www.aliexpress.com/item/123.html", "aliexpress")
        response.close.assert_called_once()



    def test_amazon_url_import_does_not_call_creators_api(self):
        product = {
            "platform": "amazon",
            "external_id": "B012345678",
            "product_url": "https://www.amazon.com/dp/B012345678",
            "title": "Producto Amazon B012345678",
            "source_metadata": {},
        }
        with (
            patch("amazon_creators.get_item_by_asin") as api,
            patch.object(product_sources, "_public_product_page") as public_page,
        ):
            result = product_sources.enrich_product(product)
        api.assert_not_called()
        public_page.assert_not_called()
        self.assertEqual(result["title"], "Producto Amazon B012345678")
        self.assertEqual(result["external_id"], "B012345678")
        self.assertTrue(result["source_metadata"]["network_enrichment_disabled"])

    def test_aliexpress_product_detail_uses_official_affiliate_api(self):
        raw = {
            "product_id": "1005001234567890",
            "product_title": "Smartphone Android",
            "first_level_category_name": "Electronics",
            "product_main_image_url": "https://ae01.alicdn.com/product.jpg",
            "target_sale_price": "29.90",
            "target_sale_price_currency": "USD",
        }
        payload = {
            "aliexpress_affiliate_productdetail_get_response": {
                "resp_result": {
                    "resp_code": 200,
                    "result": {"products": {"product": [raw]}},
                }
            }
        }
        with patch("affiliate_links.aliexpress_api_request", return_value=payload) as api:
            result = product_sources._aliexpress_product_data("1005001234567890")
        api.assert_called_once()
        self.assertEqual(api.call_args.args[0], "aliexpress.affiliate.productdetail.get")
        self.assertEqual(api.call_args.args[1]["country"], "CO")
        self.assertEqual(result["title"], "Smartphone Android")
        self.assertEqual(result["price"], 29.9)
        self.assertEqual(result["category"], "tecnologia")
        self.assertEqual(result["source_metadata"]["metadata_source"], "aliexpress_affiliate_api")

    def test_short_aliexpress_link_resolves_even_when_product_page_is_blocked(self):
        blocked = MagicMock(
            status_code=403,
            url="https://www.aliexpress.com/item/1005001234567890.html",
            headers={},
            text="",
        )
        with patch.object(product_sources.requests, "get", return_value=blocked) as request:
            resolved = product_sources._resolve_product_url("https://s.click.aliexpress.com/e/_mqUkrBZ", "aliexpress")
        self.assertEqual(resolved, "https://www.aliexpress.com/item/1005001234567890.html")
        request.assert_called_once()
        self.assertTrue(request.call_args.kwargs["allow_redirects"])
        blocked.close.assert_called_once()

    def test_aliexpress_short_link_import_does_not_search_or_resolve(self):
        product = {
            "platform": "aliexpress",
            "external_id": "URL-SHORT-LINK",
            "product_url": "https://s.click.aliexpress.com/e/_mLyQD6R",
            "title": "DAREVIE Maillot de Ciclismo Jersey transpirable",
            "source_metadata": {},
        }
        with (
            patch("affiliate_links.aliexpress_api_request") as api,
            patch.object(product_sources, "_resolve_product_url") as resolve,
            patch.object(product_sources, "_public_product_page") as public_page,
        ):
            result = product_sources.enrich_product(product)
        api.assert_not_called()
        resolve.assert_not_called()
        public_page.assert_not_called()
        self.assertEqual(result["product_url"], product["product_url"])
        self.assertEqual(result["external_id"], "URL-SHORT-LINK")
        self.assertTrue(result["source_metadata"]["network_enrichment_disabled"])

    def test_paused_url_only_products_are_not_retried(self):
        future = (product_sources.datetime.now(product_sources.timezone.utc) + product_sources.timedelta(hours=24)).isoformat()
        self.assertTrue(product_sources._retry_due({"enrichment_version": 5, "enrichment_retry_after": future}))
        self.assertFalse(product_sources._retry_due({"enrichment_version": 6, "enrichment_retry_after": future}))
        self.assertFalse(product_sources._retry_due({"network_enrichment_disabled": True}))

if __name__ == "__main__":
    unittest.main()
