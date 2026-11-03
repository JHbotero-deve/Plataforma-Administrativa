import unittest
from unittest.mock import MagicMock, patch

import product_sources


class ProductSourcesTests(unittest.TestCase):
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

    def test_link_amazon_enrichment_uses_resolved_asin_and_product_url(self):
        product = {
            "platform": "amazon",
            "external_id": "URL-SHORT-LINK",
            "product_url": "https://link.amazon/B06t7Yht8",
            "title": "Producto Amazon URL-SHORT-LINK",
            "source_metadata": {"original_url": "https://link.amazon/B06t7Yht8"},
        }
        fresh = {
            "title": "Audífonos inalámbricos",
            "price": 39.99,
            "currency": "USD",
            "image_url": "https://images.example/product.jpg",
            "gallery_urls": ["https://images.example/product.jpg"],
            "category": "accesorios",
            "resolved_url": "https://www.amazon.com/dp/B012345678",
        }
        with (
            patch.object(product_sources, "_resolve_product_url", return_value=fresh["resolved_url"]),
            patch.object(product_sources, "_official_product_data", return_value=None),
            patch.object(product_sources, "_public_product_page", return_value=fresh),
        ):
            result = product_sources.enrich_product(product)
        self.assertEqual(result["external_id"], "B012345678")
        self.assertEqual(result["product_url"], fresh["resolved_url"])
        self.assertEqual(result["source_metadata"]["original_url"], "https://link.amazon/B06t7Yht8")
        self.assertEqual(result["source_metadata"]["resolved_url"], fresh["resolved_url"])

    def test_link_amazon_without_resolved_asin_keeps_original_identity(self):
        product = {
            "platform": "amazon",
            "external_id": "URL-SHORT-LINK",
            "product_url": "https://link.amazon/B06t7Yht8",
            "title": "Producto Amazon URL-SHORT-LINK",
            "source_metadata": {},
        }
        fresh = {"title": "Producto", "resolved_url": "https://www.amazon.com/gp/redirect"}
        with (
            patch.object(product_sources, "_resolve_product_url", return_value=fresh["resolved_url"]),
            patch.object(product_sources, "_official_product_data", return_value=None),
            patch.object(product_sources, "_public_product_page", return_value=fresh),
        ):
            result = product_sources.enrich_product(product)
        self.assertEqual(result["external_id"], "URL-SHORT-LINK")
        self.assertEqual(result["product_url"], "https://link.amazon/B06t7Yht8")

    def test_pending_enrichment_persists_resolved_asin_url_and_affiliate(self):
        row = {
            "id": 7,
            "external_id": "URL-SHORT-LINK",
            "platform": "amazon",
            "product_url": "https://link.amazon/B06t7Yht8",
            "title": "Producto Amazon URL-SHORT-LINK",
            "current_price": None,
            "currency": "USD",
            "image_url": None,
            "image_gallery": [],
            "rating": None,
            "reviews_count": 0,
            "category": None,
            "source_metadata": {},
            "seller_external_id": None,
            "seller_name": None,
        }
        select_cursor = MagicMock()
        select_cursor.fetchall.return_value = [row]
        update_cursor = MagicMock()
        conn = MagicMock()
        conn.cursor.side_effect = [
            MagicMock(__enter__=MagicMock(return_value=select_cursor), __exit__=MagicMock(return_value=False)),
            MagicMock(__enter__=MagicMock(return_value=update_cursor), __exit__=MagicMock(return_value=False)),
        ]
        enriched = {
            "platform": "amazon",
            "external_id": "B012345678",
            "product_url": "https://www.amazon.com/dp/B012345678",
            "title": "Audífonos inalámbricos",
            "price": 39.99,
            "currency": "USD",
            "image_url": "https://images.example/product.jpg",
            "gallery_urls": ["https://images.example/product.jpg"],
            "rating": None,
            "reviews_count": 0,
            "category": "accesorios",
            "seller": None,
            "source_metadata": {"metadata_source": "public_product_page"},
        }
        with patch.object(product_sources, "_retry_due", return_value=True), patch.object(product_sources, "_enrich_pending_row", return_value=enriched), patch.object(product_sources, "_affiliate_url", return_value="https://www.amazon.com/dp/B012345678?tag=central-20"):
            recovered = product_sources.enrich_pending_products(conn)
        self.assertEqual(recovered, 1)
        sql = update_cursor.execute.call_args.args[0]
        values = update_cursor.execute.call_args.args[1]
        self.assertIn("external_id=CASE", sql)
        self.assertIn("product_url=COALESCE", sql)
        self.assertIn("affiliate_url=COALESCE", sql)
        self.assertEqual(values[:5], ("B012345678", "B012345678", "B012345678", enriched["product_url"], "https://www.amazon.com/dp/B012345678?tag=central-20"))
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

    def test_enrichment_failure_records_retry_without_replacing_original_url(self):
        product = {
            "platform": "aliexpress",
            "product_url": "https://s.click.aliexpress.com/e/_mOOSCcP",
            "title": "Producto AliExpress URL-123",
            "source_metadata": {},
        }
        with (
            patch.object(product_sources, "_resolve_product_url", side_effect=RuntimeError("La tienda devolvió un ciclo de redirección")),
            patch.object(product_sources, "_public_product_page", side_effect=RuntimeError("La tienda devolvió un ciclo de redirección")),
        ):
            result = product_sources.enrich_product(product)
        self.assertEqual(result["product_url"], "https://s.click.aliexpress.com/e/_mOOSCcP")
        self.assertEqual(result["source_metadata"]["enrichment_status"], "blocked")
        self.assertEqual(result["source_metadata"]["enrichment_error"], "login_or_redirect_required")
        self.assertTrue(result["source_metadata"]["enrichment_retry_after"])

    def test_enrich_pending_persists_image_price_category_and_retry_metadata(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [{
            "id": 12, "title": "Producto AliExpress URL-123", "current_price": None,
            "currency": "USD", "image_url": None, "image_gallery": [], "rating": None,
            "reviews_count": 0, "product_url": "https://www.aliexpress.com/item/123.html",
            "platform": "aliexpress", "category": None, "source_metadata": {},
        }]
        fresh = {
            "title": "Bolso de viaje", "price": 19.9, "currency": "USD",
            "image_url": "https://img.alicdn.com/bolso.jpg",
            "gallery_urls": ["https://img.alicdn.com/bolso.jpg"],
            "rating": 4.8, "reviews_count": 120, "category": "accesorios",
            "resolved_url": "https://www.aliexpress.com/item/123.html",
        }
        with (
            patch.object(product_sources, "_official_product_data", return_value=None),
            patch.object(product_sources, "_public_product_page", return_value=fresh),
        ):
            recovered = product_sources.enrich_pending_products(connection, limit=1)
        self.assertEqual(recovered, 1)
        update = cursor.execute.call_args_list[-1]
        self.assertIn("image_gallery", update.args[0])
        self.assertIn("rating=COALESCE", update.args[0])
        self.assertEqual(update.args[1][7], "accesorios")
        self.assertIn("enrichment_status", update.args[1][-2])
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



    def test_amazon_product_enrichment_uses_creators_get_items(self):
        fresh = {
            "external_id": "B012345678",
            "product_url": "https://www.amazon.com/dp/B012345678",
            "resolved_url": "https://www.amazon.com/dp/B012345678",
            "title": "Audífonos inalámbricos",
            "price": 39.99,
            "currency": "USD",
            "image_url": "https://m.media-amazon.com/images/I/product.jpg",
            "gallery_urls": ["https://m.media-amazon.com/images/I/product.jpg"],
            "category": "accesorios",
            "source_metadata": {"metadata_source": "amazon_creators_api"},
        }
        product = {
            "platform": "amazon",
            "external_id": "B012345678",
            "product_url": "https://www.amazon.com/dp/B012345678",
            "title": "Producto Amazon URL-123",
            "source_metadata": {},
        }
        with (
            patch("amazon_creators.get_item_by_asin", return_value=fresh) as api,
            patch.object(product_sources, "_public_product_page") as public_page,
        ):
            result = product_sources.enrich_product(product)
        api.assert_called_once_with("B012345678", None, "www.amazon.com")
        public_page.assert_not_called()
        self.assertEqual(result["title"], "Audífonos inalámbricos")
        self.assertEqual(result["price"], 39.99)
        self.assertEqual(result["source_metadata"]["metadata_source"], "amazon_creators_api")

    def test_aliexpress_product_detail_uses_official_affiliate_api(self):
        raw = {
            "product_id": "1005001234567890",
            "product_title": "Smartphone Android",
            "first_level_category_name": "Electronics",
        }
        payload = {
            "aliexpress_affiliate_productdetail_get_response": {
                "resp_result": {
                    "resp_code": 200,
                    "result": {"products": {"product": [raw]}},
                }
            }
        }
        normalized = {
            "external_id": "1005001234567890",
            "title": "Smartphone Android",
            "product_url": "https://www.aliexpress.com/item/1005001234567890.html",
            "affiliate_url": "https://s.click.aliexpress.com/e/example",
            "price": 29.9,
            "currency": "USD",
            "image_url": "https://ae01.alicdn.com/product.jpg",
            "gallery_urls": ["https://ae01.alicdn.com/product.jpg"],
            "category": "tecnologia",
            "seller": None,
            "source_metadata": {},
        }
        with (
            patch("affiliate_links.aliexpress_api_request", return_value=payload) as api,
            patch("promotion_bots._normalize_product", return_value=normalized) as normalize,
        ):
            result = product_sources._aliexpress_product_data("1005001234567890")
        api.assert_called_once()
        self.assertEqual(api.call_args.args[0], "aliexpress.affiliate.productdetail.get")
        self.assertEqual(api.call_args.args[1]["country"], "CO")
        normalize.assert_called_once_with(raw, "tecnologia")
        self.assertEqual(result["source_metadata"]["metadata_source"], "aliexpress_affiliate_api")

    def test_short_aliexpress_link_resolves_even_when_product_page_is_blocked(self):
        redirect = MagicMock(status_code=302, headers={"Location": "https://www.aliexpress.com/item/1005001234567890.html"})
        blocked = MagicMock(status_code=403, headers={})
        with patch.object(product_sources.requests, "get", side_effect=[redirect, blocked]) as request:
            resolved = product_sources._resolve_product_url("https://s.click.aliexpress.com/e/_mqUkrBZ", "aliexpress")
        self.assertEqual(resolved, "https://www.aliexpress.com/item/1005001234567890.html")
        self.assertEqual(request.call_count, 2)
        redirect.close.assert_called_once()
        blocked.close.assert_called_once()

    def test_new_enrichment_version_retries_previously_blocked_products(self):
        future = (product_sources.datetime.now(product_sources.timezone.utc) + product_sources.timedelta(hours=24)).isoformat()
        self.assertTrue(product_sources._retry_due({"enrichment_version": 1, "enrichment_retry_after": future}))
        self.assertFalse(product_sources._retry_due({"enrichment_version": product_sources.ENRICHMENT_VERSION, "enrichment_retry_after": future}))

if __name__ == "__main__":
    unittest.main()
