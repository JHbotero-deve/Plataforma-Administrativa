import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

APP_PATH = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_PATH))

import api


class ImageProxySecurityTests(unittest.TestCase):
    def test_serves_only_public_image_content(self):
        response = MagicMock(ok=True, status_code=200)
        response.headers = {"content-type": "image/jpeg", "content-length": "5"}
        response.iter_content.return_value = [b"image"]
        with (
            patch.object(api.socket, "getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 443))]),
            patch.object(api.requests, "get", return_value=response) as get,
        ):
            result = api.proxy_media_image("https://images.example/photo.jpg")
        self.assertEqual(result.body, b"image")
        self.assertEqual(result.media_type, "image/jpeg")
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        response.close.assert_called()

    def test_rejects_http_and_private_hosts_before_fetch(self):
        with self.assertRaises(HTTPException):
            api.proxy_media_image("http://images.example/photo.jpg")
        with (
            patch.object(api.socket, "getaddrinfo", return_value=[(None, None, None, None, ("127.0.0.1", 443))]),
            patch.object(api.requests, "get") as get,
            self.assertRaises(HTTPException),
        ):
            api.proxy_media_image("https://images.example/photo.jpg")
        get.assert_not_called()

    def test_redirect_to_private_host_is_blocked(self):
        response = MagicMock(ok=False, status_code=302)
        response.headers = {"Location": "https://127.0.0.1/admin"}
        with (
            patch.object(api.socket, "getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 443))]),
            patch.object(api.requests, "get", return_value=response) as get,
            self.assertRaises(HTTPException),
        ):
            api.proxy_media_image("https://images.example/redirect")
        self.assertEqual(get.call_count, 1)
        response.close.assert_called()

    def test_rejects_non_image_content(self):
        response = MagicMock(ok=True, status_code=200)
        response.headers = {"content-type": "text/html"}
        with (
            patch.object(api.socket, "getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 443))]),
            patch.object(api.requests, "get", return_value=response),
            self.assertRaises(HTTPException),
        ):
            api.proxy_media_image("https://images.example/not-an-image")
        response.close.assert_called()


if __name__ == "__main__":
    unittest.main()
