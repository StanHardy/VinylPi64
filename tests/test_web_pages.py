"""Smoke tests for the web application's public page and asset routes."""
from __future__ import annotations

import unittest
from unittest.mock import patch

try:
    from flask import Flask
except ModuleNotFoundError:
    Flask = None

if Flask is not None:
    from vinylpi.web.app import create_app


@unittest.skipIf(Flask is None, "Flask is not installed")
class WebRouteTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("vinylpi.web.app.initialize_storage")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

    def test_page_routes(self):
        for route in ("/", "/index.html", "/settings.html", "/pixoo.html",
                      "/stats.html", "/about.html", "/logs.html"):
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.mimetype == "text/html")

    def test_static_assets(self):
        for asset in ("/static/css/base.css", "/static/js/pages/dashboard.js",
                      "/static/js/pages/logs.js", "/static/images/logo.png"):
            with self.subTest(asset=asset):
                response = self.client.get(asset)
                self.assertEqual(response.status_code, 200)
                self.assertGreater(len(response.data), 0)


if __name__ == "__main__":
    unittest.main()
