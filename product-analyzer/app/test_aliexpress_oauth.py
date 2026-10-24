import hmac
import hashlib
import unittest

from aliexpress_oauth import (
    DEFAULT_CALLBACK_URL,
    _find_provider_error,
    _find_token_payload,
    _sign_api_request,
    _state_digest,
)


class AliExpressOAuthTests(unittest.TestCase):
    def test_default_callback_is_https_and_points_to_real_api_route(self):
        self.assertTrue(DEFAULT_CALLBACK_URL.startswith("https://"))
        self.assertTrue(DEFAULT_CALLBACK_URL.endswith("/api/v1/aliexpress/oauth/callback"))

    def test_signature_uses_sorted_key_value_pairs_and_api_path(self):
        params = {"code": "temporary", "app_key": "123", "sign_method": "sha256"}
        expected_message = "/auth/token/create" + "".join(
            key + params[key] for key in sorted(params)
        )
        expected = hmac.new(
            b"secret", expected_message.encode("utf-8"), hashlib.sha256
        ).hexdigest().upper()
        self.assertEqual(_sign_api_request(params, "secret"), expected)

    def test_state_is_hashed_before_storage(self):
        self.assertEqual(_state_digest("state-value"), hashlib.sha256(b"state-value").hexdigest())
        self.assertNotEqual(_state_digest("state-value"), "state-value")

    def test_token_parser_handles_iop_response_wrappers(self):
        body = {"aliexpress_solution_auth_token_create_response": {
            "resp_result": {"access_token": "access", "refresh_token": "refresh"}
        }}
        self.assertEqual(_find_token_payload(body)["access_token"], "access")

    def test_error_parser_handles_nested_provider_errors(self):
        self.assertEqual(
            _find_provider_error({"error_response": {"msg": "permission denied"}}),
            "permission denied",
        )


if __name__ == "__main__":
    unittest.main()
