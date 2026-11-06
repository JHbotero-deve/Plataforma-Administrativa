import hashlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient


APP_PATH = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_PATH))

import aliexpress_oauth
import api


class AliExpressOAuthTests(unittest.IsolatedAsyncioTestCase):
    def test_api_exposes_the_public_callback_route(self):
        with patch.object(aliexpress_oauth, "_consume_oauth_state", return_value=False):
            response = TestClient(api.app).get(
                "/api/v1/aliexpress/oauth/callback?code=private-code&state=invalid"
            )

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("private-code", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_start_uses_current_official_seller_authorization_endpoint(self):
        request = MagicMock()
        request.cookies = {aliexpress_oauth.ADMIN_COOKIE_NAME: "valid-session"}

        with (
            patch.object(aliexpress_oauth, "verify_session_token", return_value=True),
            patch.object(aliexpress_oauth, "_create_oauth_state", return_value="one-time-state"),
            patch.dict(
                os.environ,
                {
                    "ALIEXPRESS_APP_KEY": "12345678",
                    "ALIEXPRESS_CALLBACK_URL": "https://central.example/api/v1/aliexpress/oauth/callback",
                },
                clear=True,
            ),
        ):
            response = aliexpress_oauth.start_oauth(request)

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response.headers["location"].startswith(
                "https://api-sg.aliexpress.com/oauth/authorize?"
            )
        )
        self.assertIn("client_id=12345678", response.headers["location"])
        self.assertIn("state=one-time-state", response.headers["location"])

    def test_iop_signature_matches_documented_token_request_vector(self):
        params = {
            "app_key": "12345678",
            "code": "3_500102_JxZ05Ux3cnnSSUm6dCxYg6Q26",
            "sign_method": "sha256",
            "timestamp": "1517820392000",
        }

        signature = aliexpress_oauth._iop_signature(
            "/auth/token/create", params, "helloworld"
        )

        self.assertEqual(
            signature,
            "35607762342831B6A417A0DED84B79C05FEFBF116969C48AD6DC00279A9F4D81",
        )

    def test_token_storage_encrypts_both_tokens_before_database_write(self):
        key = Fernet.generate_key()
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value

        with (
            patch.dict(
                os.environ,
                {"ALIEXPRESS_TOKEN_ENCRYPTION_KEY": key.decode("ascii")},
                clear=True,
            ),
            patch.object(aliexpress_oauth, "get_connection", return_value=connection),
        ):
            aliexpress_oauth._save_tokens(
                {
                    "access_token": "access-secret",
                    "refresh_token": "refresh-secret",
                    "expires_in": "3600",
                    "refresh_token_valid_time": "7200",
                }
            )

        parameters = cursor.execute.call_args.args[1]
        access_ciphertext, refresh_ciphertext = parameters[:2]
        fernet = Fernet(key)
        self.assertEqual(fernet.decrypt(access_ciphertext.encode()), b"access-secret")
        self.assertEqual(
            fernet.decrypt(refresh_ciphertext.encode()), b"refresh-secret"
        )
        self.assertNotIn("access-secret", access_ciphertext)
        self.assertNotIn("refresh-secret", refresh_ciphertext)
        connection.commit.assert_called_once()

    async def test_invalid_state_rejects_callback_without_exchange_or_token_storage(self):
        request = MagicMock()
        request.scope = {"query_string": b"code=private-code&state=invalid"}

        with (
            patch.object(aliexpress_oauth, "_consume_oauth_state", return_value=False),
            patch.object(aliexpress_oauth, "_exchange_authorization_code") as exchange,
            patch.object(aliexpress_oauth, "_save_tokens") as save_tokens,
        ):
            response = await aliexpress_oauth.oauth_callback(
                request, code="private-code", state="invalid"
            )

        self.assertIsInstance(response, HTMLResponse)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(request.scope["query_string"], b"")
        self.assertNotIn(b"private-code", response.body)
        exchange.assert_not_called()
        save_tokens.assert_not_called()

    def test_oauth_state_is_consumed_only_once_and_only_hash_is_persisted(self):
        state = "one-time-state"
        state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [(state_hash,), None]

        with patch.object(
            aliexpress_oauth, "get_connection", side_effect=[connection, connection]
        ):
            first_use = aliexpress_oauth._consume_oauth_state(state)
            second_use = aliexpress_oauth._consume_oauth_state(state)

        self.assertTrue(first_use)
        self.assertFalse(second_use)
        for call in cursor.execute.call_args_list:
            self.assertEqual(call.args[1], (state_hash,))
            self.assertNotIn(state, call.args[1])

    async def test_callback_success_does_not_return_or_log_tokens(self):
        request = MagicMock()
        request.scope = {"query_string": b"code=private-code&state=one-time"}

        with (
            patch.object(aliexpress_oauth, "_consume_oauth_state", return_value=True),
            patch.object(
                aliexpress_oauth,
                "_exchange_authorization_code",
                return_value={
                    "access_token": "access-secret",
                    "refresh_token": "refresh-secret",
                },
            ) as exchange,
            patch.object(aliexpress_oauth, "_save_tokens") as save_tokens,
        ):
            response = await aliexpress_oauth.oauth_callback(
                request, code="private-code", state="one-time", error=None
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.scope["query_string"], b"")
        self.assertNotIn(b"private-code", response.body)
        self.assertNotIn(b"access-secret", response.body)
        self.assertNotIn(b"refresh-secret", response.body)
        exchange.assert_called_once_with("private-code")
        save_tokens.assert_called_once()

    def test_invalid_fernet_key_fails_explicitly(self):
        with patch.dict(
            os.environ, {"ALIEXPRESS_TOKEN_ENCRYPTION_KEY": "invalid"}, clear=True
        ):
            with self.assertRaises(HTTPException) as raised:
                aliexpress_oauth._fernet()

        self.assertEqual(raised.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
