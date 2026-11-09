import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


APP_PATH = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_PATH))

import notifications
import telegram_alert
import url_import


class TelegramPollingLockTests(unittest.TestCase):
    def test_busy_lock_logs_owner_metadata_without_exposing_queries(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"locked": False}
        cursor.fetchall.return_value = [{
            "pid": 4321,
            "application_name": "psycopg2",
            "state": "idle",
            "backend_start": "2026-11-07 04:00:00",
        }]
        with (
            patch.object(telegram_alert, "get_connection", return_value=connection),
            patch("builtins.print") as output,
        ):
            self.assertIsNone(telegram_alert._acquire_polling_lock())
        self.assertTrue(connection.autocommit)
        self.assertEqual(connection.cursor.call_count, 2)
        self.assertIn("pg_stat_activity", cursor.execute.call_args_list[1].args[0])
        messages = " ".join(str(call.args[0]) for call in output.call_args_list)
        self.assertIn("pid=4321", messages)
        self.assertIn("app=psycopg2", messages)
        self.assertNotIn("SELECT", messages)
        connection.close.assert_called_once()

    def test_polling_lock_keeps_a_dedicated_autocommit_connection(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"locked": True}
        with patch.object(telegram_alert, "get_connection", return_value=connection):
            lock_connection = telegram_alert._acquire_polling_lock()
        self.assertIs(lock_connection, connection)
        self.assertTrue(connection.autocommit)
        connection.close.assert_not_called()


class TelegramCommandTests(unittest.TestCase):
    def test_batch_modeling_reports_missing_data_separately_from_batch_limit(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [
            {"id": 7, "current_price": 20, "category_avg": 30}
        ]
        cursor.fetchone.return_value = {"eligible": 501, "active_total": 510}

        with (
            patch.object(telegram_alert, "get_connection", return_value=connection),
            patch.object(telegram_alert, "enrich_pending_products", return_value=0),
            patch.object(telegram_alert, "score_product", return_value=72),
        ):
            result = telegram_alert._model_active_products()

        self.assertEqual(result, (1, 9, 0, 500, 0, 0, 0))
        count_query = cursor.execute.call_args_list[1].args[0]
        self.assertIn("catalog_expires_at IS NULL OR catalog_expires_at > NOW()", count_query)
        self.assertIn("catalog_status", count_query)
        self.assertIn("LOWER(COALESCE(image_url, '')) NOT LIKE", count_query)
        connection.close.assert_called_once()

    def test_plain_aliexpress_message_preserves_the_title_before_short_url(self):
        message = (
            "He encontrado esto en AliExpress: DAREVIE Maillot de Ciclismo "
            "de manga larga para hombre y mujer, Jersey transpirable de talla pequeña "
            "con FPS 50 +, nivel de entrenamiento\n"
            "https://s.click.aliexpress.com/e/_mLyQD6R"
        )
        with (
            patch.object(telegram_alert, "_add_url", return_value=(7, {"title": "DAREVIE"}, 75)) as add,
            patch.object(telegram_alert, "_send_imported", return_value=True) as send_imported,
        ):
            result = telegram_alert._handle("token", "chat", message)
        self.assertTrue(result)
        add.assert_called_once_with(
            "https://s.click.aliexpress.com/e/_mLyQD6R",
            title="DAREVIE Maillot de Ciclismo de manga larga para hombre y mujer, Jersey transpirable de talla pequeña con FPS 50 +, nivel de entrenamiento",
        )
        send_imported.assert_called_once()

    def test_add_without_arguments_returns_usage_instead_of_import_error(self):
        send = MagicMock(return_value=True)

        with patch.object(telegram_alert, "_send", send):
            result = telegram_alert._handle("token", "chat", "/agregar")

        self.assertTrue(result)
        self.assertIn("/agregar URL", send.call_args.args[2])

    def test_search_command_uses_only_local_catalog(self):
        send = MagicMock(return_value=True)
        products = [{"title": "Auriculares", "current_price": 25, "currency": "USD"}]

        with (
            patch.object(telegram_alert, "_search_products", return_value=products) as search,
            patch.object(telegram_alert, "_send", send),
            patch.object(telegram_alert, "_send_product", return_value=True) as send_product,
        ):
            result = telegram_alert._handle("token", "chat", "/buscar auriculares")

        self.assertTrue(result)
        search.assert_called_once_with("auriculares", limit=8)
        send_product.assert_called_once()
        self.assertIn("Resultados del catálogo", send.call_args_list[0].args[2])

    def test_search_command_does_not_queue_external_search_when_no_local_results(self):
        send = MagicMock(return_value=True)

        with (
            patch.object(telegram_alert, "_search_products", return_value=[]),
            patch.object(telegram_alert, "_send", send),
        ):
            result = telegram_alert._handle("token", "chat", "/buscar teclado")

        self.assertTrue(result)
        self.assertIn("No hay coincidencias", send.call_args.args[2])
        self.assertIn("/agregar <URL>", send.call_args.args[2])

    def test_search_matches_category_as_well_as_title(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [{"title": "Zapato", "category": "calzado"}]

        with patch.object(telegram_alert, "get_connection", return_value=connection):
            products = telegram_alert._search_products("calzado")

        query, parameters = cursor.execute.call_args.args
        self.assertIn("p.title ILIKE %s OR COALESCE(c.name, '') ILIKE %s", query)
        self.assertEqual(parameters, ("%calzado%", "%calzado%", 8))
        self.assertEqual(products, cursor.fetchall.return_value)
        connection.close.assert_called_once()

    def test_publish_command_explains_studio_workflow(self):
        send = MagicMock(return_value=True)

        with patch.object(telegram_alert, "_send", send):
            result = telegram_alert._handle("token", "chat", "/publicar")

        self.assertTrue(result)
        self.assertIn("Studio", send.call_args.args[2])
        self.assertIn("Telegram no publica", send.call_args.args[2])

    def test_add_accepts_a_plain_product_url(self):
        product = {
            "platform": "amazon",
            "external_id": "B000000001",
            "title": "Producto",
            "product_url": "https://www.amazon.com/dp/B000000001",
            "price": None,
        }
        conn = MagicMock()

        with (
            patch.object(telegram_alert, "import_url", return_value=product) as import_mock,
            patch.object(telegram_alert, "enrich_product", return_value=product),
            patch.object(telegram_alert, "get_connection", return_value=conn),
            patch.object(telegram_alert, "upsert_product", return_value=7) as upsert,
        ):
            result, saved_product, score = telegram_alert._add_url(
                "https://www.amazon.com/dp/B000000001"
            )

        self.assertEqual(result, 7)
        self.assertIs(saved_product, product)
        self.assertIsNone(score)
        self.assertEqual(upsert.call_args.args[1:3], ("amazon", None))
        self.assertIsNone(import_mock.call_args.kwargs["currency"])
        conn.close.assert_called_once()

    def test_send_error_does_not_log_the_bot_token(self):
        token = "123456:example-secret"
        output = io.StringIO()

        with (
            patch.object(
                telegram_alert.requests,
                "post",
                side_effect=telegram_alert.requests.ConnectionError(
                    f"https://api.telegram.org/bot{token}/sendMessage"
                ),
            ),
            contextlib.redirect_stdout(output),
        ):
            result = telegram_alert._send(token, "chat", "mensaje")

        self.assertFalse(result)
        self.assertNotIn(token, output.getvalue())


    def test_search_without_term_lists_imported_catalog(self):
        products = [{"title": "Auriculares inalámbricos"}]
        send = MagicMock(return_value=True)
        send_product = MagicMock(return_value=True)
        with (
            patch.object(telegram_alert, "_search_products", return_value=products) as search,
            patch.object(telegram_alert, "_send", send),
            patch.object(telegram_alert, "_send_product", send_product),
        ):
            self.assertTrue(telegram_alert._handle("token", "chat", "/buscar"))
        search.assert_called_once_with("", limit=8)
        self.assertIn("Resultados del catálogo", send.call_args_list[0].args[2])
        self.assertEqual(send_product.call_count, 1)

    def test_modeling_sends_progress_before_batch_result(self):
        send = MagicMock(return_value=True)
        with (
            patch.object(telegram_alert, "_model_active_products", return_value=(3, 2, 0, 0, 4, 1, 0)),
            patch.object(telegram_alert, "_send", send),
        ):
            self.assertTrue(telegram_alert._handle("token", "chat", "/modelar"))
        self.assertEqual(send.call_count, 2)
        self.assertIn("Iniciando modelado", send.call_args_list[0].args[2])
        self.assertIn("Productos puntuados: 3", send.call_args_list[1].args[2])
        self.assertIn("exclusivamente las URLs públicas", send.call_args_list[1].args[2])
        self.assertIn("no consulta APIs de búsqueda ni detalle", send.call_args_list[1].args[2])

    def test_catalog_status_reports_total_active_inactive_and_url_counts(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [
            {"total": 500, "active": 2, "inactive": 498, "stale": 498, "with_url": 490},
            {"modeled": 0},
        ]
        send = MagicMock(return_value=True)
        with (
            patch.object(telegram_alert, "get_connection", return_value=connection),
            patch.object(telegram_alert, "_send", send),
        ):
            self.assertTrue(telegram_alert._handle("token", "chat", "/estado"))
        message = send.call_args.args[2]
        self.assertIn("Productos registrados: 500", message)
        self.assertIn("Productos activos: 2", message)
        self.assertIn("Productos inactivos: 498", message)
        self.assertIn("Productos con URL: 490", message)

    def test_top_products_excludes_unverified_and_placeholder_catalog_rows(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        with patch.object(telegram_alert, "get_connection", return_value=connection):
            self.assertEqual(telegram_alert._top_products(limit=8), [])
        query, parameters = cursor.execute.call_args.args
        self.assertIn("field_sources", query)
        self.assertIn("missing_fields", query)
        self.assertIn("%placeholder%", query)
        self.assertEqual(parameters, (8,))
        connection.close.assert_called_once()

    def test_unverified_legacy_metrics_are_not_displayed_as_real(self):
        message = telegram_alert._format_product({
            "title": "Auriculares",
            "current_price": 24.99,
            "currency": "USD",
            "rating": 4.8,
            "reviews_count": 128,
            "sales_estimate": 450,
            "source_metadata": {"metadata_source": "legacy_marketplace_api"},
        })
        self.assertIn("Rating: N/D | Reseñas: N/D", message)
        self.assertIn("Ventas estimadas: N/D", message)
        self.assertNotIn("4.8", message)
        self.assertNotIn("128", message)
        self.assertNotIn("450", message)

    def test_stale_product_is_marked_for_price_revalidation(self):
        message = telegram_alert._format_product({
            "title": "Auriculares",
            "current_price": 24.99,
            "currency": "USD",
            "product_url": "https://www.amazon.com/dp/B000000001",
            "source_metadata": {"catalog_status": "stale"},
        })
        self.assertIn("precio pendiente de verificación", message)


class TelegramApiResponseTests(unittest.TestCase):
    def test_http_success_with_telegram_rejection_is_not_reported_as_sent(self):
        response = MagicMock(ok=True, status_code=200)
        response.json.return_value = {"ok": False, "description": "chat not found"}

        with patch.object(notifications.requests, "post", return_value=response):
            result = notifications._post_telegram("token", "sendMessage", {}, 5)

        self.assertFalse(result)

    def test_publication_notification_uses_text_without_marketplace_media_or_preview(self):
        with (
            patch.object(notifications, "_config", return_value=("token", "chat")),
            patch.object(notifications, "_post_telegram", return_value=True) as post,
        ):
            result = notifications.send_telegram_publication({
                "title": "Auriculares",
                "subtitle": "40% de descuento",
                "description": "Cancelación de ruido",
                "price_display": "60.00 USD",
                "image_url": "https://m.media-amazon.com/images/I/photo.jpg",
                "seller_name": "Amazon Marketplace",
                "seller_url": "https://www.amazon.com",
                "store_url": "https://central-7ykr.vercel.app/producto/auriculares-1",
            })

        self.assertTrue(result)
        self.assertEqual(post.call_args.args[1], "sendMessage")
        payload = post.call_args.args[2]
        self.assertNotIn("photo", payload)
        self.assertTrue(payload["disable_web_page_preview"])
        self.assertIn("60.00 USD", payload["text"])
        self.assertIn("Amazon Marketplace", payload["text"])
        self.assertIn("Cancelación de ruido", payload["text"])

    def test_telegram_product_images_are_not_attached_but_remain_catalog_metadata(self):
        product = {
            "image_url": "https://m.media-amazon.com/images/I/photo.jpg",
            "image_gallery": ["https://ae01.alicdn.com/photo.jpg"],
        }
        self.assertEqual(telegram_alert._product_images(product), [])
        self.assertTrue(telegram_alert._has_product_image(product))

    def test_telegram_bot_messages_disable_link_previews(self):
        response = MagicMock(ok=True, status_code=200)
        response.json.return_value = {"ok": True}
        with patch.object(telegram_alert.requests, "post", return_value=response) as post:
            self.assertTrue(telegram_alert._send("token", "chat", "https://www.amazon.com/dp/B000000001"))
        self.assertTrue(post.call_args.kwargs["json"]["disable_web_page_preview"])

    def test_promotion_alert_contains_discount_commission_sales_and_coupon(self):
        with (
            patch.object(notifications, "_config", return_value=("token", "chat")),
            patch.object(notifications, "_post_telegram", return_value=True) as post,
        ):
            result = notifications.send_telegram_alert(
                {
                    "title": "Oferta",
                    "price": 12,
                    "score": 85,
                    "platform": "AliExpress",
                    "category": "accesorios",
                    "discount": 40,
                    "commission_rate": 5,
                    "sales_estimate": 1200,
                    "promo_code": "SAVE10",
                    "url": "https://s.click.aliexpress.com/e/abc123",
                }
            )

        message = post.call_args.args[2]["text"]
        self.assertTrue(result)
        self.assertIn("Descuento:</b> 40%", message)
        self.assertIn("Comisión afiliada:</b> 5%", message)
        self.assertIn("Ventas últimos 30 días:</b> 1200", message)
        self.assertIn("<code>SAVE10</code>", message)
        self.assertTrue(post.call_args.args[2]["disable_web_page_preview"])


class SupportedMarketplaceTests(unittest.TestCase):
    def test_amazon_and_aliexpress_urls_are_supported(self):
        self.assertEqual(url_import.detect_platform("https://link.amazon/B06t7Yht8"), "amazon")
        self.assertEqual(url_import._external_id("https://www.amazon.com/gp/aw/d/B000000002", "amazon"), "B000000002")
        self.assertEqual(
            url_import.detect_platform("https://www.amazon.com/dp/B000000001"),
            "amazon",
        )
        self.assertEqual(
            url_import.detect_platform(
                "https://www.aliexpress.com/item/1005001234567890.html"
            ),
            "aliexpress",
        )
        self.assertEqual(url_import.detect_platform("https://a.aliexpress.com/_m0K2w0r"), "aliexpress")

    def test_manual_import_accepts_full_catalog_categories(self):
        product = url_import.import_url(
            "https://www.aliexpress.com/item/1005001234567890.html",
            "tecnologia",
        )
        self.assertEqual(product["category"], "tecnologia")
        self.assertEqual(product["source_metadata"]["category_source"], "user")

    def test_mercadolibre_urls_are_rejected(self):
        with self.assertRaises(ValueError):
            url_import.detect_platform("https://www.mercadolibre.com.co/item")



    def test_buscar_does_not_call_external_marketplace_search(self):
        send = MagicMock(return_value=True)
        with (
            patch.object(telegram_alert, "_search_products", return_value=[]),
            patch("marketplace_ingestion.search_and_import") as external_search,
            patch.object(telegram_alert, "_send", send),
        ):
            self.assertTrue(telegram_alert._handle("token", "chat", "/buscar auriculares"))
        external_search.assert_not_called()
        self.assertIn("No hay coincidencias", send.call_args.args[2])
        self.assertIn("/agregar URL", send.call_args.args[2])

    def test_producto_command_is_alias_for_agregar(self):
        imported = (73, {"title": "Auriculares"}, 68.5)
        with (
            patch.object(telegram_alert, "_add_url", return_value=imported) as add_url,
            patch.object(telegram_alert, "_send_imported", return_value=True) as send_imported,
        ):
            self.assertTrue(telegram_alert._handle(
                "token", "chat", "/producto https://www.amazon.com/dp/B000000073"
            ))
        add_url.assert_called_once_with("https://www.amazon.com/dp/B000000073")
        self.assertEqual(send_imported.call_args.args[-1], "<b>Producto agregado</b>")

if __name__ == "__main__":
    unittest.main()
