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
    def test_add_without_arguments_returns_usage_instead_of_import_error(self):
        send = MagicMock(return_value=True)

        with patch.object(telegram_alert, "_send", send):
            result = telegram_alert._handle("token", "chat", "/agregar")

        self.assertTrue(result)
        self.assertIn("/agregar URL", send.call_args.args[2])

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
            patch.object(telegram_alert, "get_connection", return_value=conn),
            patch.object(telegram_alert, "upsert_product", return_value=7) as upsert,
        ):
            result, saved_product, score = telegram_alert._add_url(
                "https://www.amazon.com/dp/B000000001"
            )

        self.assertEqual(result, 7)
        self.assertIs(saved_product, product)
        self.assertIsNone(score)
        self.assertEqual(upsert.call_args.args[1:3], ("amazon", "accesorios"))
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
