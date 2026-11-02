import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException


APP_PATH = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_PATH))

import api
import notifications
import publications
from publications import PublicationPayload
import store_orders
import store_notifications
import wompi


class StoreCommerceTests(unittest.TestCase):
    def test_personal_card_margin_requires_a_real_internal_cost(self):
        self.assertEqual(publications._profit_metrics(100, 70), (30.0, 30.0))
        self.assertEqual(publications._profit_metrics(100, None), (None, None))
        self.assertEqual(publications._profit_metrics(0, 20), (None, None))

    def test_public_publication_feed_never_exposes_internal_cost_or_margin(self):
        connection = unittest.mock.MagicMock()
        cursor = unittest.mock.MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = []

        with patch("publications.get_connection", return_value=connection):
            publications.list_publications(
                request=unittest.mock.MagicMock(),
                limit=20,
                include_unpublished=False,
            )

        query = cursor.execute.call_args.args[0]
        self.assertNotIn("pc.cost_price", query)
        self.assertNotIn("pc.profit_amount", query)
        self.assertNotIn("pc.profit_margin_pct", query)

    def test_personal_publication_keeps_source_price_and_saves_private_cost(self):
        connection = unittest.mock.MagicMock()
        cursor = unittest.mock.MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchone.side_effect = [
            {
                "product_id": 42,
                "title": "Producto personal",
                "image_url": "https://example.com/image.jpg",
                "image_gallery": [],
                "product_url": None,
                "affiliate_url": None,
                "current_price": 100,
                "currency": "COP",
                "platform": "personal",
                "opportunity_score": None,
            },
            {"id": 8, "is_published": True},
        ]
        payload = PublicationPayload(
            product_id=42,
            title="Producto personal",
            price_display="Precio manipulado",
            sale_price=999,
            cost_price=70,
        )

        with patch("publications.get_connection", return_value=connection):
            publications.update_publication(8, payload)

        saved = cursor.execute.call_args_list[1].args[1]
        self.assertEqual(saved[2], "100 COP")
        self.assertEqual(saved[5:9], (100, 70, 30.0, 30.0))

    def test_wompi_checkout_rejects_products_without_personal_publications(self):
        connection = unittest.mock.MagicMock()
        cursor = unittest.mock.MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchone.return_value = None
        request = wompi.ProductCheckout(
            product_id=42,
            customer_name="Cliente",
            customer_email="cliente@example.com",
        )

        with patch.dict(
            os.environ,
            {"WOMPI_PUBLIC_KEY": "test-public-key", "WOMPI_CURRENCY": "COP"},
            clear=True,
        ), patch("wompi.get_connection", return_value=connection):
            with self.assertRaises(HTTPException) as error:
                wompi.create_product_checkout(request)

        self.assertEqual(error.exception.status_code, 404)
        self.assertIn("LOWER(pl.name)='personal'", cursor.execute.call_args.args[0])

    def test_wompi_cart_checkout_rejects_non_personal_publications(self):
        connection = unittest.mock.MagicMock()
        cursor = unittest.mock.MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = []
        request = unittest.mock.MagicMock()
        request.json = AsyncMock(return_value={
            "customer_name": "Cliente",
            "customer_email": "cliente@example.com",
            "items": [{"product_id": 42, "quantity": 1}],
        })

        with patch.dict(
            os.environ,
            {"WOMPI_PUBLIC_KEY": "test-public-key", "WOMPI_CURRENCY": "COP"},
            clear=True,
        ), patch("wompi.get_connection", return_value=connection):
            with self.assertRaises(HTTPException) as error:
                asyncio.run(wompi.create_cart_checkout(request))

        self.assertEqual(error.exception.status_code, 404)
        self.assertIn("LOWER(pl.name)='personal'", cursor.execute.call_args.args[0])
        self.assertEqual(cursor.execute.call_count, 1)
        connection.commit.assert_not_called()

    def test_admin_sales_routes_require_a_session(self):
        guarded = set()
        for route in [*store_orders.router.routes, *api.core_router.routes]:
            for method in route.methods or ():
                if any(
                    dependency.call is api.require_admin
                    for dependency in route.dependant.dependencies
                ):
                    guarded.add((method, route.path))

        self.assertTrue(
            {
                ("GET", "/store/orders"),
                ("GET", "/store/metrics"),
                ("GET", "/store/invoices"),
                ("POST", "/store/notifications/retry"),
                ("GET", "/intelligence/overview"),
            }.issubset(guarded)
        )

    def test_public_invoice_requires_a_long_token(self):
        endpoint = api.app.openapi()["paths"][
            "/api/v1/store/invoices/{invoice_number}"
        ]["get"]
        token = next(
            param for param in endpoint["parameters"] if param["name"] == "token"
        )

        self.assertTrue(token["required"])
        self.assertEqual(token["schema"]["minLength"], 40)
        self.assertEqual(token["schema"]["maxLength"], 64)

    def test_admin_invoice_listing_repairs_legacy_missing_tokens(self):
        cursor = unittest.mock.MagicMock()
        cursor.fetchone.return_value = {"public_token": "a" * 43}
        invoice = {"invoice_number": "FAC-123", "public_token": None}

        store_orders._ensure_invoice_tokens(cursor, [invoice])

        self.assertEqual(invoice["public_token"], "a" * 43)
        self.assertIn("public_token IS NULL", cursor.execute.call_args.args[0])

    def test_checkout_return_url_stays_on_the_store_origin(self):
        with patch.dict(
            os.environ,
            {"PUBLIC_STORE_URL": "https://central.example", "WOMPI_REDIRECT_URL": ""},
            clear=True,
        ):
            result = wompi._redirect_url("ORD-123")
            self.assertEqual(
                result,
                "https://central.example/resultado-pago.html?reference=ORD-123",
            )
            with self.assertRaises(HTTPException) as error:
                wompi._redirect_url(
                    "ORD-123", "https://attacker.example/collect"
                )

        self.assertEqual(error.exception.status_code, 400)

    def test_customer_email_contains_private_invoice_link_and_escaped_content(self):
        order = {
            "reference": "ORD-123",
            "customer_name": "<cliente>",
            "total_amount": 12000,
            "estimated_profit": 2000,
            "currency": "COP",
        }
        invoice = {
            "invoice_number": "FAC-123",
            "public_token": "private-token",
        }
        items = [{"product_title": "<producto>", "quantity": 1, "line_total": 12000}]

        subject, text, body = store_notifications._email_content(
            "CUSTOMER_RECEIPT", order, invoice, items
        )

        self.assertIn("ORD-123", subject)
        self.assertIn("numero=FAC-123", text)
        self.assertIn("token=private-token", text)
        self.assertIn("&lt;producto&gt;", body)
        self.assertIn("No reemplaza una factura electrónica", body)

    @patch("store_notifications.requests.post")
    def test_resend_delivery_uses_configured_sender_and_reports_rejection(self, post):
        post.return_value.ok = True
        with patch.dict(
            os.environ,
            {
                "RESEND_API_KEY": "test-key",
                "RESEND_FROM_EMAIL": "Central <ventas@example.com>",
            },
            clear=True,
        ):
            store_notifications._send_email(
                "buyer@example.com",
                "Receipt",
                "text",
                "<p>receipt</p>",
                "test-idempotency-key",
            )

        self.assertEqual(post.call_args.args[0], store_notifications.RESEND_URL)
        self.assertEqual(
            post.call_args.kwargs["headers"]["Authorization"], "Bearer test-key"
        )
        self.assertEqual(
            post.call_args.kwargs["headers"]["Idempotency-Key"],
            "test-idempotency-key",
        )
        self.assertEqual(post.call_args.kwargs["json"]["to"], ["buyer@example.com"])

    @patch("store_notifications.smtplib.SMTP")
    @patch("store_notifications.requests.post")
    def test_smtp_is_preferred_when_both_providers_are_configured(
        self, post, smtp
    ):
        client = smtp.return_value.__enter__.return_value
        with patch.dict(
            os.environ,
            {
                "RESEND_API_KEY": "configured-but-unavailable",
                "RESEND_FROM_EMAIL": "Central <ventas@example.com>",
                "SMTP_HOST": "smtp.example.com",
                "SMTP_PORT": "587",
                "SMTP_USER": "mailer",
                "SMTP_PASSWORD": "smtp-password",
                "SMTP_FROM": "Central <ventas@example.com>",
            },
            clear=True,
        ):
            store_notifications._send_email(
                "buyer@example.com", "Receipt", "text", "<p>receipt</p>", "test-key"
            )

        smtp.assert_called_once()
        client.starttls.assert_called_once()
        client.login.assert_called_once_with("mailer", "smtp-password")
        client.send_message.assert_called_once()
        post.assert_not_called()

    def test_production_outbox_migration_includes_retry_and_deduplication(self):
        migration = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "sql"
            / "017_store_notifications.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("UNIQUE(order_id, channel, notification_type, recipient)", migration)
        self.assertIn("available_at", migration)
        self.assertIn("public_token", migration)

    @patch("notifications._post_telegram", return_value=True)
    @patch("notifications._config", return_value=("bot-token", "chat"))
    def test_telegram_sale_alert_does_not_disclose_customer_contact(
        self, config, post
    ):
        notifications.send_store_sale_notification(
            {
                "reference": "ORD-123",
                "customer_name": "Private Name",
                "customer_email": "private@example.com",
                "total_amount": 12000,
                "estimated_profit": 2000,
                "currency": "COP",
            },
            {"invoice_number": "FAC-123"},
            [{"product_title": "Producto", "quantity": 1}],
        )

        message = post.call_args.args[2]["text"]
        self.assertNotIn("Private Name", message)
        self.assertNotIn("private@example.com", message)
        self.assertIn("ORD-123", message)


if __name__ == "__main__":
    unittest.main()
