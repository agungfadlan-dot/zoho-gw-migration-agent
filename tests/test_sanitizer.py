"""
Unit tests for security/sanitizer.py
"""

import unittest
import logging
import io
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from security.sanitizer import sanitize_text, sanitize_dict, setup_secure_logger


class TestSanitizer(unittest.TestCase):

    def test_sanitize_zoho_tokens(self):
        sample = "Requesting Zoho API with token 1000.a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4.1234567890abcdef1234567890abcdef and code 1000.abc123xyz789012345678"
        redacted = sanitize_text(sample)
        self.assertNotIn("1000.a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4", redacted)
        self.assertIn("[REDACTED_ZOHO_TOKEN]", redacted)

    def test_sanitize_bearer_tokens(self):
        sample = "Authorization: Bearer ya29.a0AfH6SMD_secrettoken12345"
        redacted = sanitize_text(sample)
        self.assertNotIn("ya29.a0AfH6SMD_secrettoken12345", redacted)
        self.assertIn("Bearer [REDACTED_BEARER_TOKEN]", redacted)

    def test_sanitize_private_key(self):
        sample = (
            "Loading key:\n"
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEA0YpT...\n"
            "-----END RSA PRIVATE KEY-----\n"
            "Key loaded."
        )
        redacted = sanitize_text(sample)
        self.assertNotIn("MIIEowIBAAKCAQEA0YpT...", redacted)
        self.assertIn("[REDACTED_PRIVATE_KEY]", redacted)

    def test_sanitize_dict(self):
        payload = {
            "username": "admin@example.com",
            "client_secret": "sensitive_zoho_secret_value",
            "refresh_token": "sensitive_refresh_token",
            "nested": {
                "password": "user_password_plain",
                "normal_field": "public_data"
            }
        }
        cleaned = sanitize_dict(payload)
        self.assertEqual(cleaned["username"], "admin@example.com")
        self.assertEqual(cleaned["client_secret"], "[REDACTED_SECRET]")
        self.assertEqual(cleaned["refresh_token"], "[REDACTED_SECRET]")
        self.assertEqual(cleaned["nested"]["password"], "[REDACTED_SECRET]")
        self.assertEqual(cleaned["nested"]["normal_field"], "public_data")

    def test_secure_logger_redaction(self):
        log_stream = io.StringIO()
        logger = logging.getLogger("test_logger")
        logger.setLevel(logging.INFO)

        from security.sanitizer import SanitizedFormatter, RedactingFilter
        handler = logging.StreamHandler(log_stream)
        handler.setFormatter(SanitizedFormatter("%(message)s"))
        handler.addFilter(RedactingFilter())
        logger.addHandler(handler)

        logger.info("Connecting using client_secret=very_secret_key_123")
        output = log_stream.getvalue()

        self.assertNotIn("very_secret_key_123", output)
        self.assertIn("client_secret=[REDACTED_SECRET]", output)


if __name__ == "__main__":
    unittest.main()
