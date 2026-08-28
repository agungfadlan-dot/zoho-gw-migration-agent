"""
Unit tests for security/validator.py
"""

import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from security.validator import (
    validate_zoho_domain,
    audit_zoho_scopes,
    validate_google_service_account_json
)


class TestValidator(unittest.TestCase):

    def test_validate_zoho_domain(self):
        self.assertEqual(validate_zoho_domain("zoho.com"), "zoho.com")
        self.assertEqual(validate_zoho_domain("accounts.zoho.eu"), "zoho.eu")
        self.assertEqual(validate_zoho_domain("https://zoho.in"), "zoho.in")

        with self.assertRaises(ValueError):
            validate_zoho_domain("malicious-zoho-phish.com")

    def test_audit_zoho_scopes_safe(self):
        safe_scopes = [
            "ZohoMail.organization.accounts.READ",
            "ZohoMail.messages.READ",
            "ZohoDirectory.user.READ"
        ]
        is_valid, warnings, errors = audit_zoho_scopes(safe_scopes)
        self.assertTrue(is_valid)
        self.assertEqual(len(errors), 0)

    def test_audit_zoho_scopes_destructive_rejected(self):
        unsafe_scopes = [
            "ZohoMail.messages.READ",
            "ZohoMail.messages.DELETE",
            "ZohoDirectory.user.DELETE"
        ]
        is_valid, warnings, errors = audit_zoho_scopes(unsafe_scopes)
        self.assertFalse(is_valid)
        self.assertGreater(len(errors), 0)
        self.assertTrue(any("Unsafe/Destructive" in e for e in errors))

    def test_validate_google_sa_json(self):
        valid_sa = """{
            "type": "service_account",
            "project_id": "test-project-123",
            "private_key_id": "key123",
            "private_key": "-----BEGIN PRIVATE KEY-----\\nMIIEvgIBADANBgk...\\n-----END PRIVATE KEY-----\\n",
            "client_email": "migration-sa@test-project-123.iam.gserviceaccount.com",
            "client_id": "123456789",
            "token_uri": "https://oauth2.googleapis.com/token"
        }"""
        res = validate_google_service_account_json(valid_sa)
        self.assertEqual(res["client_email"], "migration-sa@test-project-123.iam.gserviceaccount.com")
        self.assertEqual(res["project_id"], "test-project-123")

        # Missing required field
        invalid_sa = '{"type": "service_account", "project_id": "foo"}'
        with self.assertRaises(ValueError):
            validate_google_service_account_json(invalid_sa)


if __name__ == "__main__":
    unittest.main()
