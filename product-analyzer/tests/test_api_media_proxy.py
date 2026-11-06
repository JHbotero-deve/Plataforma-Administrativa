import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

APP_PATH = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_PATH))

import api


class ImageProxySecurityTests(unittest.TestCase):
    def _public_dns(self, address="93.184.216.34"):
        family = api.socket.AF_INET6 if ":" in address else api.socket.AF_INET
        sockaddr = (address, 443, 0, 0) if family == api.socket.AF_INET6 else (address, 443)
        return [(family, api.socket.SOCK_STREAM, 6, "", sockaddr)]

    def _pool(self, status=200, headers=None, body=b"image"):
        response = MagicMock()
        response.status = status
        response.headers = headers or {"Content-Type": "image/jpeg", "Content-Length": str(len(body))}
        response.stream.return_value = [body]
        pool = MagicMock()
        pool.urlopen.return_value = response
        return pool, response

    def test_serves_public_image_using_pinned_ip_and_original_tls_hostname(self):
        pool, response = self._pool()
        with (
            patch.object(api.socket, "getaddrinfo", return_value=self._public_dns()) as lookup,
            patch.object(api.urllib3, "HTTPSConnectionPool", return_value=pool) as pool_factory,
        ):
            result = api.proxy_media_image("https://images.example/photo.jpg?size=large")
        self.assertEqual(result.body, b"image")
        self.assertEqual(result.media_type, "image/jpeg")
        self.assertEqual(lookup.call_count, 1)
        self.assertEqual(pool_factory.call_args.kwargs["host"], "93.184.216.34")
        self.assertEqual(pool_factory.call_args.kwargs["server_hostname"], "images.example")
        self.assertEqual(pool_factory.call_args.kwargs["assert_hostname"], "images.example")
        self.assertEqual(pool.urlopen.call_args.args[:2], ("GET", "/photo.jpg?size=large"))
        self.assertEqual(pool.urlopen.call_args.kwargs["headers"]["Host"], "images.example")
        self.assertFalse(pool.urlopen.call_args.kwargs["redirect"])
        response.close.assert_called_once()
        pool.close.assert_called_once()

    def test_rejects_http_and_private_hosts_before_fetch(self):
        with self.assertRaises(HTTPException):
            api.proxy_media_image("http://images.example/photo.jpg")
        with (
            patch.object(api.urllib3, "HTTPSConnectionPool") as pool_factory,
            self.assertRaises(HTTPException),
        ):
            api.proxy_media_image("https://127.0.0.1/admin")
        pool_factory.assert_not_called()

    def test_rejects_private_or_mixed_dns_answers(self):
        mixed = self._public_dns() + self._public_dns("127.0.0.1")
        with (
            patch.object(api.socket, "getaddrinfo", return_value=mixed),
            patch.object(api.urllib3, "HTTPSConnectionPool") as pool_factory,
            self.assertRaises(HTTPException),
        ):
            api.proxy_media_image("https://images.example/photo.jpg")
        pool_factory.assert_not_called()

    def test_rejects_ipv4_mapped_private_ipv6(self):
        with self.assertRaises(HTTPException):
            api._resolve_public_image_addresses("::ffff:127.0.0.1")

    def test_pins_public_ipv6_address(self):
        pool, response = self._pool()
        with (
            patch.object(api.socket, "getaddrinfo", return_value=self._public_dns("2606:4700:4700::1111")),
            patch.object(api.urllib3, "HTTPSConnectionPool", return_value=pool) as pool_factory,
        ):
            api.proxy_media_image("https://images.example/photo.jpg")
        self.assertEqual(pool_factory.call_args.kwargs["host"], "2606:4700:4700::1111")
        self.assertEqual(pool_factory.call_args.kwargs["server_hostname"], "images.example")
        response.close.assert_called_once()

    def test_redirect_to_private_host_is_blocked(self):
        pool, response = self._pool(status=302, headers={"Location": "https://127.0.0.1/admin"})
        with (
            patch.object(api.socket, "getaddrinfo", return_value=self._public_dns()),
            patch.object(api.urllib3, "HTTPSConnectionPool", return_value=pool) as pool_factory,
            self.assertRaises(HTTPException),
        ):
            api.proxy_media_image("https://images.example/redirect")
        self.assertEqual(pool_factory.call_count, 1)
        response.close.assert_called_once()
        pool.close.assert_called_once()

    def test_rejects_non_image_content(self):
        pool, response = self._pool(headers={"Content-Type": "text/html"})
        with (
            patch.object(api.socket, "getaddrinfo", return_value=self._public_dns()),
            patch.object(api.urllib3, "HTTPSConnectionPool", return_value=pool),
            self.assertRaises(HTTPException),
        ):
            api.proxy_media_image("https://images.example/not-an-image")
        response.close.assert_called_once()
        pool.close.assert_called_once()

    def test_rejects_oversized_image_from_header_and_stream(self):
        pool, response = self._pool(headers={"Content-Type": "image/jpeg", "Content-Length": str(api.MAX_PROXY_IMAGE_BYTES + 1)})
        with (
            patch.object(api.socket, "getaddrinfo", return_value=self._public_dns()),
            patch.object(api.urllib3, "HTTPSConnectionPool", return_value=pool),
            self.assertRaises(HTTPException),
        ):
            api.proxy_media_image("https://images.example/large.jpg")
        response.close.assert_called_once()
        pool.close.assert_called_once()
        pool, response = self._pool(headers={"Content-Type": "image/jpeg"})
        response.stream.return_value = [b"x" * (api.MAX_PROXY_IMAGE_BYTES + 1)]
        with (
            patch.object(api.socket, "getaddrinfo", return_value=self._public_dns()),
            patch.object(api.urllib3, "HTTPSConnectionPool", return_value=pool),
            self.assertRaises(HTTPException),
        ):
            api.proxy_media_image("https://images.example/large.jpg")
        response.close.assert_called_once()
        pool.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
