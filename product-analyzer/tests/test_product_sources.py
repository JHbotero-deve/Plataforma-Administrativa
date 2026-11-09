import unittest
from unittest.mock import MagicMock, patch

import product_sources
from url_import import import_url


class ProductSourcesTests(unittest.TestCase):
    def test_url_import_preserves_original_short_url_without_external_api(self):
        url = "https://amzn.to/example"
        with patch.object(product_sources, "_public_product_page") as page:
            product = import_url(url)
        page.assert_not_called()
        self.assertEqual(product["product_url"], url)
        self.assertEqual(product["source_metadata"]["original_url"], url)
        self.assertIsNone(product["price"])
        self.assertIsNone(product["image_url"])

    def test_url_import_preserves_user_supplied_metadata(self):
        product = import_url(
            "https://www.amazon.com/dp/B012345678",
            category="calzado",
            title="Zapatillas de prueba",
            price=49.90,
            image_url="https://images.example/zapatillas.jpg",
        )
        self.assertEqual(product["title"], "Zapatillas de prueba")
        self.assertEqual(product["category"], "calzado")
        self.assertEqual(product["price"], 49.90)
        self.assertEqual(product["image_url"], "https://images.example/zapatillas.jpg")
        self.assertEqual(product["source_metadata"]["missing_fields"], ["currency"])
        self.assertIn("price", product["source_metadata"]["manual_fields"])
        self.assertIn("title", product["source_metadata"]["manual_fields"])

    def test_public_jsonld_extracts_product_fields_without_marketplace_api(self):
        response = MagicMock()
        response.status_code = 200
        response.text = '''<html><meta property="og:image" content="https://images.example/tenis.jpg"><script type="application/ld+json">{"@type":"Product","name":"Tenis deportivos","description":"Tenis para correr","offers":{"price":"49.99","priceCurrency":"USD"},"aggregateRating":{"ratingValue":"4.7","reviewCount":"120"}}</script></html>'''
        with patch.object(product_sources.requests, "get", return_value=response):
            result = product_sources._public_product_page("https://www.amazon.com/dp/B012345678", "amazon")
        self.assertEqual(result["title"], "Tenis deportivos")
        self.assertEqual(result["price"], 49.99)
        self.assertEqual(result["currency"], "USD")
        self.assertEqual(result["image_url"], "https://images.example/tenis.jpg")
        self.assertEqual(result["rating"], 4.7)
        self.assertEqual(result["reviews_count"], 120)
        response.close.assert_called_once()

    def test_enrich_product_uses_public_page_and_preserves_user_values(self):
        product = {
            "platform": "amazon",
            "external_id": "URL-SHORT",
            "product_url": "https://amzn.to/example",
            "title": "Producto Amazon URL-SHORT",
            "price": 60,
            "currency": "COP",
            "image_url": None,
            "category": None,
            "source_metadata": {"original_url": "https://amzn.to/example", "manual_fields": ["price", "currency"]},
        }
        page = {
            "resolved_url": "https://www.amazon.com/dp/B012345678",
            "title": "Tenis reales",
            "price": 49.99,
            "previous_price": 80,
            "currency": "USD",
            "image_url": "https://images.example/tenis.jpg",
            "gallery_urls": ["https://images.example/tenis.jpg", "https://images.example/tenis-2.jpg"],
            "description": "Descripción verificada",
            "rating": 4.5,
            "reviews_count": 25,
            "category": "calzado",
            "seller": {"external_id": "seller-1", "name": "Tienda"},
            "seller_url": "https://www.amazon.com/store/seller-1",
        }
        with patch.object(product_sources, "_public_product_page", return_value=page) as fetch:
            result = product_sources.enrich_product(product)
        fetch.assert_called_once_with("https://amzn.to/example", "amazon")
        self.assertEqual(result["title"], "Tenis reales")
        self.assertEqual(result["price"], 60)
        self.assertEqual(result["currency"], "COP")
        self.assertEqual(result["image_url"], "https://images.example/tenis.jpg")
        self.assertEqual(result["product_url"], "https://www.amazon.com/dp/B012345678")
        self.assertEqual(result["source_metadata"]["original_url"], "https://amzn.to/example")
        self.assertEqual(result["source_metadata"]["enrichment_status"], "complete")
        self.assertEqual(result["source_metadata"]["field_sources"]["title"], "public_product_page")
        self.assertEqual(result["source_metadata"]["field_sources"]["price"], "user")
        self.assertEqual(result["source_metadata"]["field_sources"]["image_url"], "public_product_page")
        self.assertEqual(result["source_metadata"]["field_sources"]["currency"], "user")
        self.assertNotIn("network_enrichment_disabled", result["source_metadata"])

    def test_complete_user_supplied_product_does_not_refetch_page(self):
        product = {
            "platform": "aliexpress",
            "product_url": "https://www.aliexpress.com/item/123.html",
            "title": "Tenis deportivos",
            "price": 19.9,
            "currency": "USD",
            "image_url": "https://img.example/shoes.jpg",
            "category": "calzado",
            "currency": "USD",
            "source_metadata": {"enrichment_version": product_sources.ENRICHMENT_VERSION, "manual_fields": ["title", "price", "currency", "image_url", "category"]},
        }
        with patch.object(product_sources, "_public_product_page") as fetch:
            result = product_sources.enrich_product(product)
        fetch.assert_not_called()
        self.assertEqual(result["source_metadata"]["enrichment_status"], "complete")

    def test_enrich_pending_products_retries_old_versions_and_stale_rows(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        self.assertEqual(product_sources.enrich_pending_products(connection, limit=3), 0)
        query, parameters = cursor.execute.call_args.args
        self.assertIn("enrichment_version", query)
        self.assertIn("catalog_status", query)
        self.assertIn("ORDER BY p.is_active DESC", query)
        self.assertEqual(parameters, (str(product_sources.ENRICHMENT_VERSION), 15))

    def test_old_enrichment_version_bypasses_retry_backoff(self):
        row = {
            "id": 91,
            "external_id": "URL-OLD",
            "title": "Producto Amazon URL-OLD",
            "current_price": None,
            "currency": None,
            "image_url": None,
            "image_gallery": [],
            "rating": None,
            "reviews_count": None,
            "product_url": "https://link.amazon/B012345678",
            "affiliate_url": None,
            "platform": "amazon",
            "category": None,
            "source_metadata": {
                "enrichment_version": "9",
                "enrichment_retry_after": "2099-01-01T00:00:00+00:00",
            },
            "seller_external_id": None,
            "seller_name": None,
            "is_active": True,
            "catalog_expired": False,
        }
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [row]
        partial = {
            **row,
            "gallery_urls": [],
            "source_metadata": {
                "enrichment_version": product_sources.ENRICHMENT_VERSION,
                "enrichment_status": "partial",
                "catalog_refresh_verified": False,
            },
        }
        with patch.object(product_sources, "_enrich_pending_row", return_value=partial) as enrich:
            self.assertEqual(product_sources.enrich_pending_products(connection, limit=1), 0)
        enrich.assert_called_once()
        self.assertTrue(enrich.call_args.args[1])

    def test_failed_public_page_keeps_product_partial_and_retryable(self):
        product = {
            "platform": "amazon",
            "product_url": "https://www.amazon.com/dp/B012345678",
            "title": "Producto Amazon B012345678",
            "source_metadata": {},
        }
        with patch.object(product_sources, "_public_product_page", side_effect=RuntimeError("login required")):
            result = product_sources.enrich_product(product)
        self.assertEqual(result["source_metadata"]["enrichment_status"], "partial")
        self.assertIn("login required", result["source_metadata"]["enrichment_error"])
        self.assertIn("title", result["source_metadata"]["missing_fields"])
        self.assertFalse(product_sources._retry_due(result["source_metadata"]))
        result["source_metadata"]["enrichment_retry_after"] = "2000-01-01T00:00:00+00:00"
        self.assertTrue(product_sources._retry_due(result["source_metadata"]))

    def test_login_page_is_not_treated_as_product_metadata(self):
        response = MagicMock()
        response.status_code = 200
        response.text = '<html><meta property="og:title" content="Sign in to AliExpress"></html>'
        with patch.object(product_sources.requests, "get", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "exige iniciar sesión"):
                product_sources._public_product_page("https://www.aliexpress.com/item/123.html", "aliexpress")
        response.close.assert_called_once()

    def test_short_url_redirect_is_followed_only_to_allowed_marketplace_host(self):
        first = MagicMock()
        first.status_code = 302
        first.headers = {"Location": "https://www.amazon.com/dp/B012345678"}
        second = MagicMock()
        second.status_code = 200
        second.text = '<html><meta property="og:title" content="Tenis"></html>'
        with patch.object(product_sources.requests, "get", side_effect=[first, second]) as request:
            result = product_sources._public_product_page("https://amzn.to/example", "amazon")
        self.assertEqual(result["resolved_url"], "https://www.amazon.com/dp/B012345678")
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[1].kwargs["headers"]["Referer"], "https://amzn.to/example")
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_shortlink_redirects_send_referer_to_official_destination(self):
        first = MagicMock()
        first.status_code = 302
        first.headers = {"Location": "https://www.aliexpress.com/redirect/123"}
        second = MagicMock()
        second.status_code = 302
        second.headers = {"Location": "https://www.aliexpress.com/item/123456789.html"}
        third = MagicMock()
        third.status_code = 200
        third.text = '<html><meta property="og:title" content="Organizador de cocina"></html>'
        with patch.object(product_sources.requests, "get", side_effect=[first, second, third]) as request:
            result = product_sources._public_product_page("https://a.aliexpress.com/_m0K2w0r", "aliexpress")
        self.assertEqual(result["resolved_url"], "https://www.aliexpress.com/item/123456789.html")
        self.assertEqual(request.call_args_list[1].kwargs["headers"]["Referer"], "https://a.aliexpress.com/_m0K2w0r")
        self.assertEqual(request.call_args_list[2].kwargs["headers"]["Referer"], "https://www.aliexpress.com/redirect/123")

    def test_http_redirect_to_official_marketplace_is_upgraded_to_https(self):
        first = MagicMock()
        first.status_code = 302
        first.headers = {"Location": "http://www.amazon.com/dp/B012345678"}
        second = MagicMock()
        second.status_code = 200
        second.text = '<html><meta property="og:title" content="Tenis"></html>'
        with patch.object(product_sources.requests, "get", side_effect=[first, second]) as request:
            result = product_sources._public_product_page("https://amzn.to/example", "amazon")
        self.assertEqual(result["resolved_url"], "https://www.amazon.com/dp/B012345678")
        self.assertEqual(request.call_args_list[1].args[0], "https://www.amazon.com/dp/B012345678")
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_redirect_to_untrusted_host_is_rejected(self):
        response = MagicMock()
        response.status_code = 302
        response.headers = {"Location": "https://127.0.0.1/admin"}
        with patch.object(product_sources.requests, "get", return_value=response):
            with self.assertRaisesRegex(ValueError, "host=127.0.0.1"):
                product_sources._public_product_page("https://amzn.to/example", "amazon")
        response.close.assert_called_once()

    def test_public_page_rejects_untrusted_initial_host(self):
        with self.assertRaises(ValueError):
            product_sources._public_product_page("https://evil.example/product", "amazon")

    def test_category_inference_requires_identifiable_product_type(self):
        self.assertEqual(product_sources._category(None, "Men running shoes"), "calzado")
        self.assertIsNone(product_sources._category(None, "Producto nuevo"))

    def test_price_parser_handles_common_formats(self):
        self.assertEqual(product_sources._number("$1,299.00"), 1299.0)
        self.assertEqual(product_sources._number("1.299,00 €"), 1299.0)
        self.assertEqual(product_sources._number("25,000"), 25000.0)
        self.assertEqual(product_sources._number("25.000"), 25000.0)
        self.assertIsNone(product_sources._number("Consultar"))

    def test_redirect_cycle_fails_fast(self):
        first = MagicMock()
        first.status_code = 302
        first.headers = {"Location": "https://www.aliexpress.com/redirect/2"}
        second = MagicMock()
        second.status_code = 302
        second.headers = {"Location": "https://www.aliexpress.com/redirect/1"}
        third = MagicMock()
        third.status_code = 302
        third.headers = {"Location": "https://www.aliexpress.com/redirect/2"}
        with patch.object(product_sources.requests, "get", side_effect=[first, second, third]) as request:
            with self.assertRaisesRegex(RuntimeError, "ciclo de redirección"):
                product_sources._public_product_page("https://www.aliexpress.com/redirect/1", "aliexpress")
        self.assertEqual(request.call_count, 2)
        first.close.assert_called_once()
        second.close.assert_called_once()


    def test_accepts_official_short_links_and_rejects_generic_or_spoofed_hosts(self):
        self.assertEqual(import_url("https://amzn.to/example")["platform"], "amazon")
        self.assertEqual(import_url("https://s.click.aliexpress.com/e/example")["platform"], "aliexpress")
        with self.assertRaises(ValueError):
            import_url("https://bit.ly/example")
        for url in (
            "https://amazon.com.evil.example/dp/B012345678",
            "https://api.amazon.com/catalog/search",
            "https://api-sg.aliexpress.com/sync",
            "http://www.amazon.com/dp/B012345678",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                import_url(url)


    def test_forced_refresh_updates_stale_page_price_currency_and_image(self):
        product = {
            "platform": "aliexpress",
            "external_id": "1005001234567890",
            "product_url": "https://www.aliexpress.com/item/1005001234567890.html",
            "title": "Auriculares inalámbricos",
            "price": 25000,
            "currency": "USD",
            "image_url": "https://images.example/old.jpg",
            "category": "tecnologia",
            "source_metadata": {"metadata_source": "public_product_page", "enrichment_status": "complete"},
        }
        page = {
            "resolved_url": product["product_url"],
            "title": "Auriculares inalámbricos",
            "price": 25,
            "currency": "CNY",
            "image_url": "https://images.example/new.jpg",
            "gallery_urls": ["https://images.example/new.jpg"],
            "description": "Producto actualizado",
            "category": "tecnologia",
        }
        with patch.object(product_sources, "_public_product_page", return_value=page) as fetch:
            result = product_sources.enrich_product(product, force=True)
        fetch.assert_called_once()
        self.assertEqual(result["price"], 25)
        self.assertEqual(result["currency"], "CNY")
        self.assertEqual(result["image_url"], "https://images.example/new.jpg")
        self.assertTrue(result["source_metadata"]["catalog_refresh_verified"])

    def test_forced_refresh_does_not_override_manual_price_and_marks_mismatch_unverified(self):
        product = {
            "platform": "amazon",
            "product_url": "https://www.amazon.com/dp/B012345678",
            "title": "Producto manual",
            "price": 60,
            "currency": "USD",
            "image_url": "https://images.example/manual.jpg",
            "category": "tecnologia",
            "source_metadata": {"metadata_source": "cli", "manual_fields": ["title", "price", "currency", "image_url"]},
        }
        page = {
            "resolved_url": product["product_url"],
            "title": "Producto real",
            "price": 49.99,
            "currency": "USD",
            "image_url": "https://images.example/page.jpg",
            "gallery_urls": ["https://images.example/page.jpg"],
        }
        with patch.object(product_sources, "_public_product_page", return_value=page):
            result = product_sources.enrich_product(product, force=True)
        self.assertEqual(result["price"], 60)
        self.assertEqual(result["title"], "Producto manual")
        self.assertFalse(result["source_metadata"]["catalog_refresh_verified"])


    def test_verified_public_price_replaces_legacy_url_only_placeholder(self):
        product = {
            "platform": "aliexpress",
            "external_id": "URL-OLD",
            "product_url": "https://www.aliexpress.com/item/123456789.html",
            "title": "Producto Aliexpress URL-OLD",
            "price": 25000,
            "currency": "COP",
            "image_url": None,
            "category": None,
            "source_metadata": {"metadata_source": "url_only", "original_url": "https://www.aliexpress.com/item/123456789.html"},
        }
        page = {
            "resolved_url": product["product_url"],
            "title": "Auriculares inalámbricos",
            "price": 39.99,
            "currency": "USD",
            "image_url": "https://images.example/headphones.jpg",
            "gallery_urls": ["https://images.example/headphones.jpg"],
            "description": "Descripción de la página",
            "category": "accesorios",
        }
        with patch.object(product_sources, "_public_product_page", return_value=page):
            result = product_sources.enrich_product(product)
        self.assertEqual(result["price"], 39.99)
        self.assertEqual(result["currency"], "USD")
        self.assertEqual(result["title"], "Auriculares inalámbricos")
        self.assertEqual(result["source_metadata"]["metadata_source"], "public_product_page")


    def test_rejects_generic_global_offer_as_a_real_product_title(self):
        self.assertTrue(product_sources._is_placeholder_title("Oferta Destacada Global · Producto Verificado con Envío Rápido"))
        self.assertTrue(product_sources._is_placeholder_title("Producto Amazon B000000001"))
        self.assertTrue(product_sources._is_placeholder_title("Producto Amazon"))
        self.assertTrue(product_sources._is_placeholder_title("Título real del producto"))
        self.assertFalse(product_sources._is_placeholder_title("Producto manual"))
        self.assertFalse(product_sources._is_placeholder_title("Auriculares inalámbricos Bluetooth"))
        self.assertTrue(product_sources._is_placeholder_image_url("https://enlace-de-la-imagen.jpg"))
        self.assertFalse(product_sources._is_placeholder_image_url("https://images.example/headphones.jpg"))


    def test_example_title_and_image_are_replaced_by_real_public_page_data(self):
        product = import_url(
            "https://www.amazon.com/dp/B012345678",
            category="tecnologia",
            title="Título real del producto",
            price=24.99,
            currency="USD",
            image_url="https://enlace-de-la-imagen.jpg",
        )
        self.assertIsNone(product["image_url"])
        self.assertNotIn("image_url", product["source_metadata"]["manual_fields"])
        page = {
            "resolved_url": product["product_url"],
            "title": "Auriculares inalámbricos",
            "price": 24.99,
            "currency": "USD",
            "image_url": "https://images.example/headphones.jpg",
            "gallery_urls": ["https://images.example/headphones.jpg"],
        }
        with patch.object(product_sources, "_public_product_page", return_value=page):
            result = product_sources.enrich_product(product)
        self.assertEqual(result["title"], "Auriculares inalámbricos")
        self.assertEqual(result["image_url"], "https://images.example/headphones.jpg")
        self.assertTrue(result["source_metadata"]["catalog_refresh_verified"])


    def test_complete_old_enrichment_version_is_queued_for_revalidation(self):
        self.assertTrue(product_sources._retry_due({
            "enrichment_status": "complete",
            "enrichment_version": product_sources.ENRICHMENT_VERSION - 1,
        }))


    def test_a_aliexpress_shortlink_redirect_is_allowed(self):
        first = MagicMock()
        first.status_code = 302
        first.headers = {"Location": "https://www.aliexpress.com/item/123.html"}
        second = MagicMock()
        second.status_code = 200
        second.text = '<html><meta property="og:title" content="Auriculares"></html>'
        with patch.object(product_sources.requests, "get", side_effect=[first, second]):
            result = product_sources._public_product_page("https://a.aliexpress.com/_m0K2w0r", "aliexpress")
        self.assertEqual(result["resolved_url"], "https://www.aliexpress.com/item/123.html")


    def test_inactive_products_with_old_enrichment_version_are_revalidated(self):
        row = {
            "id": 91, "external_id": "B000000091", "title": "Producto Amazon B000000091",
            "current_price": None, "currency": None, "image_url": None, "image_gallery": [],
            "rating": None, "reviews_count": None,
            "product_url": "https://www.amazon.com/dp/B000000091", "affiliate_url": None,
            "platform": "amazon", "category": None,
            "source_metadata": {"enrichment_version": product_sources.ENRICHMENT_VERSION - 1},
            "seller_external_id": None, "seller_name": None, "is_active": False,
            "catalog_expired": False,
        }
        enriched = {
            "external_id": row["external_id"], "product_url": row["product_url"],
            "title": "Auriculares recuperados", "price": 19.99, "currency": "USD",
            "image_url": "https://images.example/earbuds.jpg", "gallery_urls": [],
            "rating": None, "reviews_count": None, "category": "tecnologia", "seller": None,
            "source_metadata": {
                "enrichment_version": product_sources.ENRICHMENT_VERSION,
                "enrichment_status": "partial", "metadata_source": "public_product_page",
                "catalog_refresh_verified": False,
            },
        }
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [row]
        with patch.object(product_sources, "_enrich_pending_row", return_value=enriched) as enrich:
            recovered = product_sources.enrich_pending_products(connection, limit=1)
        query = cursor.execute.call_args_list[0].args[0]
        self.assertIn("COALESCE(p.source_metadata->>'enrichment_version', '') <> %s", query)
        self.assertNotIn("p.is_active=TRUE AND COALESCE(p.source_metadata->>'enrichment_version'", query)
        self.assertNotIn("p.is_active=TRUE AND (p.current_price IS NULL", query)
        enrich.assert_called_once()
        self.assertEqual(enrich.call_args.args[0]["id"], 91)
        self.assertEqual(recovered, 1)
        connection.commit.assert_called_once()

    def test_amazon_asin_wins_over_frontend_placeholder_external_id(self):
        product = import_url(
            "https://www.amazon.com/dp/B0H5FJXKRG/ref=cm_sw_r_as_gl_apa_gl_i_dl?tag=jh0c35-20",
            external_id="URL-9E182600AABCO3FEDCBFDBOF",
        )
        self.assertEqual(product["external_id"], "B0H5FJXKRG")
        self.assertEqual(product["source_metadata"]["external_id_source"], "url")
        self.assertEqual(product["source_metadata"]["original_url"], "https://www.amazon.com/dp/B0H5FJXKRG/ref=cm_sw_r_as_gl_apa_gl_i_dl?tag=jh0c35-20")

    def test_amazon_shortlink_keeps_provisional_id_until_redirect_is_resolved(self):
        product = import_url("https://link.amazon/B04ZSERdW", external_id="URL-OLD")
        self.assertTrue(product["external_id"].startswith("URL-"))
        self.assertEqual(product["source_metadata"]["external_id_source"], "pending_redirect")
        page = {
            "resolved_url": "https://www.amazon.com/dp/B012345678",
            "title": "Producto Amazon de prueba",
            "price": 19.99,
            "currency": "USD",
            "image_url": "https://images.example/amazon.jpg",
            "gallery_urls": ["https://images.example/amazon.jpg", "https://images.example/amazon-2.jpg"],
            "description": "Descripción pública del producto",
            "category": "tecnologia",
        }
        with patch.object(product_sources, "_public_product_page", return_value=page):
            result = product_sources.enrich_product(product)
        self.assertEqual(result["external_id"], "B012345678")
        self.assertEqual(result["source_metadata"]["external_id_source"], "resolved_url")
        self.assertEqual(result["product_url"], page["resolved_url"])
        self.assertEqual(result["source_metadata"]["original_url"], "https://link.amazon/B04ZSERdW")
        self.assertEqual(result["description"], "Descripción pública del producto")
        self.assertEqual(len(result["gallery_urls"]), 2)

    def test_aliexpress_affiliate_shortlink_extracts_item_id_after_redirect(self):
        product = import_url("https://s.click.aliexpress.com/e/_mtoJ8OF", external_id="URL-OLD")
        self.assertTrue(product["external_id"].startswith("URL-"))
        self.assertEqual(product["source_metadata"]["external_id_source"], "pending_redirect")
        page = {
            "resolved_url": "https://www.aliexpress.com/item/1005001234567890.html",
            "title": "Producto AliExpress de prueba",
            "price": 12.5,
            "currency": "USD",
            "image_url": "https://images.example/aliexpress.jpg",
            "gallery_urls": ["https://images.example/aliexpress.jpg", "https://images.example/aliexpress-2.jpg"],
            "description": "Descripción pública de AliExpress",
            "category": "tecnologia",
        }
        with patch.object(product_sources, "_public_product_page", return_value=page):
            result = product_sources.enrich_product(product)
        self.assertEqual(result["external_id"], "1005001234567890")
        self.assertEqual(result["source_metadata"]["external_id_source"], "resolved_url")
        self.assertEqual(result["product_url"], page["resolved_url"])
        self.assertEqual(result["source_metadata"]["original_url"], "https://s.click.aliexpress.com/e/_mtoJ8OF")
        self.assertEqual(result["description"], "Descripción pública de AliExpress")
        self.assertEqual(len(result["gallery_urls"]), 2)

    def test_link_amazon_short_domain_follows_redirect_to_product_page(self):
        redirect = MagicMock(status_code=302, headers={"Location": "https://www.amazon.com/dp/B012345678"})
        page = MagicMock(status_code=200, text='<html><meta property="og:title" content="Producto Amazon"></html>')
        with patch.object(product_sources.requests, "get", side_effect=[redirect, page]) as request:
            result = product_sources._public_product_page("https://link.amazon/B04ZSERdW", "amazon")
        self.assertEqual(result["resolved_url"], "https://www.amazon.com/dp/B012345678")
        self.assertEqual(request.call_count, 2)
        redirect.close.assert_called_once()
        page.close.assert_called_once()

    def test_aliexpress_click_short_domain_follows_redirect_to_item(self):
        redirect = MagicMock(status_code=302, headers={"Location": "https://www.aliexpress.com/item/1005001234567890.html"})
        page = MagicMock(status_code=200, text='<html><meta property="og:title" content="Producto AliExpress"></html>')
        with patch.object(product_sources.requests, "get", side_effect=[redirect, page]) as request:
            result = product_sources._public_product_page("https://s.click.aliexpress.com/e/_mtoJ8OF", "aliexpress")
        self.assertEqual(result["resolved_url"], "https://www.aliexpress.com/item/1005001234567890.html")
        self.assertEqual(request.call_count, 2)
        redirect.close.assert_called_once()
        page.close.assert_called_once()


    def test_title_inferred_category_remains_unverified(self):
        product = import_url("https://www.aliexpress.com/item/1005001234567890.html")
        page = {
            "resolved_url": product["product_url"], "title": "Auriculares Bluetooth",
            "price": 19.99, "currency": "USD", "image_url": "https://images.example/headphones.jpg",
            "gallery_urls": ["https://images.example/headphones.jpg"], "category": None,
        }
        with patch.object(product_sources, "_public_product_page", return_value=page):
            result = product_sources.enrich_product(product)
        self.assertEqual(result["category"], "accesorios")
        self.assertEqual(result["source_metadata"]["category_source"], "title_inference")
        self.assertIn("category", result["source_metadata"]["missing_fields"])
        self.assertFalse(result["source_metadata"]["catalog_refresh_verified"])


    def test_aliexpress_page_block_is_reported_without_api_fallback(self):
        url = "https://www.aliexpress.com/item/1005001234567890.html"
        product = import_url(url)
        with patch.object(product_sources, "_public_product_page", side_effect=RuntimeError("403 Forbidden")):
            result = product_sources.enrich_product(product)
        self.assertEqual(result["source_metadata"]["enrichment_status"], "partial")
        self.assertIn("403 Forbidden", result["source_metadata"]["enrichment_error"])
        self.assertIn("price", result["source_metadata"]["missing_fields"])
        self.assertNotIn("affiliate_api_error", result["source_metadata"])

    def test_gaming_laptop_is_classified_as_technology_not_toys(self):
        title = "Portátil para Juegos Alienware 18 Area-51 RTX5070 Intel Core Ultra 9-275HX"
        self.assertEqual(product_sources._category(None, title), "tecnologia")
        self.assertEqual(product_sources._category(None, "Juguetes para niños"), "juguetes")


if __name__ == "__main__":
    unittest.main()
