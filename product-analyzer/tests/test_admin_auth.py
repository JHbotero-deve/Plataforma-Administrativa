import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import HTTPException, Response
from pydantic import ValidationError
from starlette.requests import Request


APP_PATH = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_PATH))

import admin_auth
import api
import publications
from publications import PublicationDraftPayload, PublicationOrderPayload


class AdminAuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                "CENTRAL_ADMIN_EMAIL": "admin@example.com",
                "CENTRAL_ADMIN_PASSWORD": "a-strong-password",
                "CENTRAL_AUTH_SECRET": "s" * 64,
            },
            clear=True,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def request(self, method="GET", token=None, origin=None):
        headers = []
        if token:
            headers.append(
                (
                    b"cookie",
                    f"{admin_auth.ADMIN_COOKIE_NAME}={token}".encode(),
                )
            )
        if origin:
            headers.append((b"origin", origin.encode()))
        return Request(
            {
                "type": "http",
                "method": method,
                "scheme": "https",
                "path": "/api/v1/auth/session",
                "raw_path": b"/api/v1/auth/session",
                "query_string": b"",
                "headers": headers,
                "server": ("example.com", 443),
                "client": ("127.0.0.1", 1),
            }
        )

    def test_valid_credentials_create_a_verifiable_session(self):
        email = admin_auth.authenticate_admin(
            " ADMIN@example.com ", "a-strong-password"
        )
        token = admin_auth.create_session_token(email)

        self.assertTrue(admin_auth.verify_session_token(token))

    def test_invalid_credentials_are_rejected(self):
        with self.assertRaises(HTTPException) as error:
            admin_auth.authenticate_admin("admin@example.com", "wrong")

        self.assertEqual(error.exception.status_code, 401)

    def test_tampered_session_is_rejected(self):
        token = admin_auth.create_session_token("admin@example.com")
        payload, signature = token.split(".", 1)

        self.assertFalse(
            admin_auth.verify_session_token(payload + "." + "0" * len(signature))
        )

    def test_expired_session_is_rejected(self):
        with patch.object(admin_auth.time, "time", return_value=1000):
            token = admin_auth.create_session_token("admin@example.com")
        with patch.object(admin_auth.time, "time", return_value=1000 + admin_auth.SESSION_MAX_AGE):
            self.assertFalse(admin_auth.verify_session_token(token))

    def test_missing_auth_configuration_fails_closed(self):
        os.environ.pop("CENTRAL_AUTH_SECRET")

        with self.assertRaises(HTTPException) as error:
            admin_auth.authenticate_admin("admin@example.com", "a-strong-password")

        self.assertEqual(error.exception.status_code, 503)

    def test_admin_dependency_accepts_a_valid_cookie(self):
        token = admin_auth.create_session_token("admin@example.com")

        self.assertEqual(
            admin_auth.require_admin(self.request(token=token)),
            "admin@example.com",
        )

    def test_admin_dependency_rejects_a_missing_cookie(self):
        with self.assertRaises(HTTPException) as error:
            admin_auth.require_admin(self.request(method="PATCH"))

        self.assertEqual(error.exception.status_code, 401)

    def test_mutation_requires_the_configured_origin(self):
        token = admin_auth.create_session_token("admin@example.com")

        with self.assertRaises(HTTPException) as error:
            admin_auth.require_admin(self.request(method="PATCH", token=token))

        self.assertEqual(error.exception.status_code, 403)

    def test_mutation_accepts_the_store_origin(self):
        token = admin_auth.create_session_token("admin@example.com")

        self.assertEqual(
            admin_auth.require_admin(
                self.request(
                    method="PATCH",
                    token=token,
                    origin="https://central-7ykr.vercel.app",
                )
            ),
            "admin@example.com",
        )

    def test_mutation_rejects_unconfigured_vercel_subdomains(self):
        token = admin_auth.create_session_token("admin@example.com")

        with self.assertRaises(HTTPException) as error:
            admin_auth.require_admin(
                self.request(
                    method="PATCH",
                    token=token,
                    origin="https://attacker.vercel.app",
                )
            )

        self.assertEqual(error.exception.status_code, 403)

    def test_mutation_accepts_an_explicitly_configured_origin(self):
        token = admin_auth.create_session_token("admin@example.com")
        os.environ["ALLOWED_ORIGINS"] = "https://admin.example.com"

        self.assertEqual(
            admin_auth.require_admin(
                self.request(
                    method="PATCH",
                    token=token,
                    origin="https://admin.example.com",
                )
            ),
            "admin@example.com",
        )

    def test_mutation_rejects_origins_with_paths(self):
        token = admin_auth.create_session_token("admin@example.com")

        with self.assertRaises(HTTPException) as error:
            admin_auth.require_admin(
                self.request(
                    method="PATCH",
                    token=token,
                    origin="https://central-7ykr.vercel.app.evil.test/path",
                )
            )

        self.assertEqual(error.exception.status_code, 403)

    def test_session_cookie_is_http_only_secure_and_same_site(self):
        response = Response()
        admin_auth.set_admin_cookie(response, "admin@example.com")
        cookie = response.headers["set-cookie"]

        self.assertIn("httponly", cookie.lower())
        self.assertIn("secure", cookie.lower())
        self.assertIn("samesite=strict", cookie.lower())
        self.assertIn("path=/api/v1", cookie.lower())

    def test_publication_draft_rejects_invalid_color_and_revision(self):
        with self.assertRaises(ValidationError):
            PublicationDraftPayload(accent="red")
        with self.assertRaises(ValidationError):
            PublicationDraftPayload(expected_revision=-1)

    def test_admin_mutations_require_a_session(self):
        guarded_routes = [
            *api.core_router.routes,
            *api.publication_router.routes,
            *api.tiktok_creator_router.routes,
        ]
        expected = {
            ("POST", "/products/import-url"),
            ("PATCH", "/products/{product_id}/images"),
            ("PATCH", "/products/{product_id}/model"),
            ("POST", "/publications"),
            ("PUT", "/publications/order"),
            ("PATCH", "/publications/{publication_id}"),
            ("DELETE", "/publications/{publication_id}"),
            ("POST", "/publications/{publication_id}/telegram"),
            ("PUT", "/publications/{publication_id}/draft"),
            ("DELETE", "/publications/{publication_id}/draft"),
            ("POST", "/tiktok/creator/sync"),
        }
        protected = set()

        for route in guarded_routes:
            for method in route.methods or ():
                if any(
                    dependency.call is admin_auth.require_admin
                    for dependency in route.dependant.dependencies
                ):
                    protected.add((method, route.path))

        self.assertTrue(expected.issubset(protected))

    def test_internal_catalog_reads_require_admin_session(self):
        core_protected = set()
        for route in api.core_router.routes:
            if any(
                dependency.call is admin_auth.require_admin
                for dependency in route.dependant.dependencies
            ):
                core_protected.update((method, route.path) for method in route.methods or ())

        expected_core = {
            ("GET", "/pipeline/metrics"),
            ("GET", "/pipeline/summary"),
            ("GET", "/products"),
            ("GET", "/products/{product_id}"),
            ("GET", "/comparison"),
            ("GET", "/opportunities/top"),
        }
        self.assertTrue(expected_core.issubset(core_protected))

        creator_protected = set()
        for route in api.tiktok_creator_router.routes:
            if any(
                dependency.call is admin_auth.require_admin
                for dependency in route.dependant.dependencies
            ):
                creator_protected.update((method, route.path) for method in route.methods or ())

        self.assertTrue(
            {
                ("GET", "/tiktok/creator/status"),
                ("GET", "/tiktok/creator/profile"),
                ("GET", "/tiktok/creator/products"),
            }.issubset(creator_protected)
        )

    def test_publication_order_rejects_duplicate_ids(self):
        with self.assertRaises(HTTPException) as error:
            publications.reorder_publications(
                PublicationOrderPayload(publication_ids=[1, 1])
            )

        self.assertEqual(error.exception.status_code, 422)

    def test_publication_order_saves_an_exact_active_card_order(self):
        connection = MagicMock()
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = [{"id": 8}, {"id": 4}]

        with patch("publications.get_connection", return_value=connection):
            result = publications.reorder_publications(
                PublicationOrderPayload(publication_ids=[4, 8])
            )

        self.assertEqual(result["publication_ids"], [4, 8])
        self.assertEqual(
            cursor.execute.call_args_list[1].args[1],
            (0, 4),
        )
        self.assertEqual(
            cursor.execute.call_args_list[2].args[1],
            (1, 8),
        )
        connection.commit.assert_called_once()

    def test_publication_order_rejects_stale_card_sets(self):
        connection = MagicMock()
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = [{"id": 8}, {"id": 4}]

        with patch("publications.get_connection", return_value=connection):
            with self.assertRaises(HTTPException) as error:
                publications.reorder_publications(
                    PublicationOrderPayload(publication_ids=[4, 99])
                )

        self.assertEqual(error.exception.status_code, 409)
        connection.rollback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
