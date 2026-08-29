"""
Unit tests for the local Web UI server and API endpoints using in-memory request simulation.
"""

import unittest
import json
import os
from io import BytesIO
from unittest.mock import MagicMock
from ui.server import MigrationUIHandler
from security.vault import EphemeralVault


class MockSocket:
    """In-memory mock socket to test HTTP handlers without OS socket bindings."""
    def __init__(self, raw_input: bytes):
        self.rfile = BytesIO(raw_input)
        self.wfile = BytesIO()

    def makefile(self, mode, *args, **kwargs):
        if "b" in mode:
            if "r" in mode:
                return self.rfile
            return self.wfile
        else:
            if "r" in mode:
                return self.rfile
            return self.wfile

    def sendall(self, data):
        self.wfile.write(data)


def simulate_http_request(method: str, path: str, payload: dict = None) -> tuple:
    """Executes an in-memory HTTP request against MigrationUIHandler."""
    body_bytes = json.dumps(payload).encode("utf-8") if payload is not None else b""
    headers = [
        f"{method} {path} HTTP/1.1",
        "Host: 127.0.0.1",
    ]
    if body_bytes:
        headers.append(f"Content-Length: {len(body_bytes)}")
        headers.append("Content-Type: application/json")
    headers.append("")
    headers.append("")

    raw_request = "\r\n".join(headers).encode("utf-8") + body_bytes
    mock_socket = MockSocket(raw_request)
    mock_server = MagicMock()

    try:
        MigrationUIHandler(mock_socket, ("127.0.0.1", 54321), mock_server)
    except Exception:
        pass

    raw_response = mock_socket.wfile.getvalue()
    headers_part, _, body_part = raw_response.partition(b"\r\n\r\n")
    header_lines = headers_part.decode("utf-8", errors="ignore").split("\r\n")

    status_code = 500
    if header_lines and len(header_lines[0].split()) >= 2:
        try:
            status_code = int(header_lines[0].split()[1])
        except ValueError:
            status_code = 500

    resp_headers = {}
    for line in header_lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            resp_headers[k.strip().lower()] = v.strip()

    return status_code, resp_headers, body_part


class TestUIServer(unittest.TestCase):
    def setUp(self):
        # Reset vault before each test
        MigrationUIHandler.vault = EphemeralVault()

    def test_serve_static_index_html(self):
        status, headers, body = simulate_http_request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Zoho", body)
        self.assertIn(b"Google Workspace", body)

    def test_serve_static_css_and_js(self):
        status, headers, css_body = simulate_http_request("GET", "/styles.css")
        self.assertEqual(status, 200)
        self.assertIn(b"--primary", css_body)

        status, headers, js_body = simulate_http_request("GET", "/app.js")
        self.assertEqual(status, 200)
        self.assertIn(b"DOMContentLoaded", js_body)

    def test_api_status_empty_vault(self):
        status, headers, body = simulate_http_request("GET", "/api/status")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data["success"])
        self.assertFalse(data["has_credentials"])

    def test_api_vault_store_success(self):
        payload = {
            "zoho_domain": "zoho.com",
            "zoho_client_id": "test_client_id",
            "zoho_client_secret": "test_client_secret",
            "zoho_refresh_token": "test_refresh_token",
            "google_admin_email": "admin@example.com",
            "google_sa_json": '{"client_email": "sa@proj.iam.gserviceaccount.com", "private_key": "fake_key"}'
        }
        status, headers, body = simulate_http_request("POST", "/api/vault/store", payload)
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data["success"])
        self.assertEqual(MigrationUIHandler.vault.retrieve("zoho_client_id"), "test_client_id")

    def test_api_vault_store_missing_fields(self):
        payload = {
            "zoho_domain": "zoho.com",
            "zoho_client_id": "",
            "zoho_client_secret": "sec"
        }
        status, headers, body = simulate_http_request("POST", "/api/vault/store", payload)
        self.assertEqual(status, 400)
        data = json.loads(body.decode("utf-8"))
        self.assertFalse(data["success"])
        self.assertIn("Missing", data["error"])

    def test_api_progress_poll(self):
        status, headers, body = simulate_http_request("GET", "/api/progress/poll")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data["success"])
        self.assertIn("stage", data["data"])

    def test_api_reset(self):
        MigrationUIHandler.vault.store("test_key", "test_val")
        status, headers, body = simulate_http_request("POST", "/api/reset", {})
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data["success"])
        self.assertIsNone(MigrationUIHandler.vault.retrieve("test_key"))


if __name__ == "__main__":
    unittest.main()
