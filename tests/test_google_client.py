"""
Unit tests for connectors/google_client.py with mocked HTTP transport.
"""

import unittest
from unittest.mock import patch, MagicMock
import json
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from security.vault import EphemeralVault
from connectors.google_client import GoogleWorkspaceAdminClient, generate_secure_temporary_password
from connectors.base import CalendarEvent, ContactRecord

# Dummy RSA private key for testing
DUMMY_SA_JSON = """{
    "type": "service_account",
    "project_id": "migration-project",
    "private_key_id": "key_123",
    "private_key": "-----BEGIN PRIVATE KEY-----\\nMIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQDOf2Zq4\\n-----END PRIVATE KEY-----\\n",
    "client_email": "migration-sa@migration-project.iam.gserviceaccount.com",
    "client_id": "1122334455",
    "token_uri": "https://oauth2.googleapis.com/token"
}"""


class TestGoogleClient(unittest.TestCase):

    def setUp(self):
        self.vault = EphemeralVault()
        self.vault.store("google_sa_json", DUMMY_SA_JSON)
        self.client = GoogleWorkspaceAdminClient(vault=self.vault, admin_subject_email="admin@acme.com")

    def tearDown(self):
        self.vault.purge()

    def test_password_generation_complexity(self):
        pwd = generate_secure_temporary_password(20)
        self.assertEqual(len(pwd), 20)
        self.assertTrue(any(c.isupper() for c in pwd))
        self.assertTrue(any(c.islower() for c in pwd))
        self.assertTrue(any(c.isdigit() for c in pwd))
        self.assertTrue(any(c in "!@#$%^&*" for c in pwd))

    @patch.object(GoogleWorkspaceAdminClient, "get_delegated_token", return_value="mocked_google_token")
    @patch("urllib.request.urlopen")
    def test_provision_user_success(self, mock_urlopen, mock_token):
        user_resp = MagicMock()
        user_resp.read.return_value = json.dumps({
            "id": "g_user_101",
            "primaryEmail": "alice@acme.com",
        }).encode("utf-8")
        user_resp.__enter__.return_value = user_resp

        mock_urlopen.return_value = user_resp

        res = self.client.provision_user("alice@acme.com", "Alice", "Smith")
        self.assertEqual(res["status"], "CREATED")
        self.assertEqual(res["email"], "alice@acme.com")
        self.assertTrue("temp_password" in res)
        self.assertGreater(len(res["temp_password"]), 10)

    @patch.object(GoogleWorkspaceAdminClient, "get_delegated_token", return_value="mocked_google_token")
    @patch("urllib.request.urlopen")
    def test_ensure_label(self, mock_urlopen, mock_token):
        labels_resp = MagicMock()
        labels_resp.read.return_value = json.dumps({
            "labels": [{"id": "Label_1", "name": "INBOX"}, {"id": "Label_2", "name": "Projects"}]
        }).encode("utf-8")
        labels_resp.__enter__.return_value = labels_resp

        mock_urlopen.return_value = labels_resp

        label_id = self.client.ensure_label("alice@acme.com", "Projects")
        self.assertEqual(label_id, "Label_2")

    @patch.object(GoogleWorkspaceAdminClient, "get_delegated_token", return_value="mocked_google_token")
    @patch("urllib.request.urlopen")
    def test_import_message(self, mock_urlopen, mock_token):
        import_resp = MagicMock()
        import_resp.read.return_value = json.dumps({"id": "g_msg_777", "threadId": "th_888"}).encode("utf-8")
        import_resp.__enter__.return_value = import_resp

        mock_urlopen.return_value = import_resp

        raw_rfc822 = b"Subject: Hello\r\n\r\nTest content"
        res = self.client.import_message("alice@acme.com", raw_rfc822, ["INBOX"], is_read=True)
        self.assertEqual(res["id"], "g_msg_777")


if __name__ == "__main__":
    unittest.main()
