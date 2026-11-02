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

        self.assertEqual(result, (1, 9, 0, 500, 0))
        connection.close.assert_called_once()

    def test_add_without_arguments_returns_usage_instead_of_import_error(self):
        send = MagicMock(return_value=True)

        with patch.object(telegram_alert, "_send", send):
            result = telegram_alert._handle("token", "chat", "/agregar")

        self.assertTrue(result)
        self.assertIn("/agregar URL", send.call_args.args[2])

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
            patch.object(telegram_alert, "import_url", return_value=product),
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


class TelegramApiResponseTests(unittest.TestCase):
    def test_http_success_with_telegram_rejection_is_not_reported_as_sent(self):
        response = MagicMock(ok=True, status_code=200)
        response.json.return_value = {"ok": False, "description": "chat not found"}

        with patch.object(notifications.requests, "post", return_value=response):
            result = notifications._post_telegram("token", "sendMessage", {}, 5)

        self.assertFalse(result)

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


class SupportedMarketplaceTests(unittest.TestCase):
    def test_amazon_and_aliexpress_urls_are_supported(self):
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

    def test_mercadolibre_urls_are_rejected(self):
        with self.assertRaises(ValueError):
            url_import.detect_platform("https://www.mercadolibre.com.co/item")


if __name__ == "__main__":
    unittest.main()
