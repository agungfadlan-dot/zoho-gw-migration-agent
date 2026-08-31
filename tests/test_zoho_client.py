"""
Unit tests for connectors/zoho_client.py with mocked HTTP transport.
"""

import unittest
from unittest.mock import patch, MagicMock
import io
import json
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from security.vault import EphemeralVault
from connectors.zoho_client import ZohoAdminClient, ZohoClientError


class TestZohoClient(unittest.TestCase):

    def setUp(self):
        self.vault = EphemeralVault()
        self.vault.store("zoho_client_id", "test_client_id")
        self.vault.store("zoho_client_secret", "test_client_secret")
        self.vault.store("zoho_refresh_token", "1000.test_refresh_token")
        self.client = ZohoAdminClient(vault=self.vault, domain="zoho.com")

    def tearDown(self):
        self.vault.purge()

    @patch("urllib.request.urlopen")
    def test_token_refresh_and_test_connection(self, mock_urlopen):
        # 1st call: OAuth token refresh
        token_resp = MagicMock()
        token_resp.read.return_value = json.dumps({
            "access_token": "1000.mocked_access_token",
            "expires_in": 3600
        }).encode("utf-8")
        token_resp.__enter__.return_value = token_resp

        # 2nd call: Organization info
        org_resp = MagicMock()
        org_resp.read.return_value = json.dumps({
            "data": {
                "orgName": "Acme Corp",
                "orgId": "org_98765",
                "userCount": 42
            }
        }).encode("utf-8")
        org_resp.__enter__.return_value = org_resp

        mock_urlopen.side_effect = [token_resp, org_resp]

        conn_info = self.client.test_connection()
        self.assertEqual(conn_info["status"], "connected")
        self.assertEqual(conn_info["org_name"], "Acme Corp")
        self.assertEqual(conn_info["org_id"], "org_98765")

    @patch("urllib.request.urlopen")
    def test_list_organization_users(self, mock_urlopen):
        token_resp = MagicMock()
        token_resp.read.return_value = json.dumps({"access_token": "1000.mock_token", "expires_in": 3600}).encode("utf-8")
        token_resp.__enter__.return_value = token_resp

        org_resp = MagicMock()
        org_resp.read.return_value = json.dumps({"data": {"orgId": "org_98765"}}).encode("utf-8")
        org_resp.__enter__.return_value = org_resp

        users_resp = MagicMock()
        users_resp.read.return_value = json.dumps({
            "data": [
                {
                    "zuid": "1001",
                    "primaryEmailAddress": "alice@acme.com",
                    "firstName": "Alice",
                    "lastName": "Smith",
                    "role": "admin",
                    "accountStatus": True,
                    "aliasList": [{"aliasEmail": "a.smith@acme.com"}],
                    "accountId": "acc_1001",
                    "usedMailStorage": 104857600
                }
            ]
        }).encode("utf-8")
        users_resp.__enter__.return_value = users_resp

        mock_urlopen.side_effect = [token_resp, org_resp, users_resp]

        users = self.client.list_organization_users()
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0].email, "alice@acme.com")
        self.assertEqual(users[0].aliases, ["a.smith@acme.com"])
        self.assertEqual(users[0].storage_used_bytes, 104857600)

    @patch("urllib.request.urlopen")
    def test_stream_raw_message_rfc822(self, mock_urlopen):
        token_resp = MagicMock()
        token_resp.read.return_value = json.dumps({"access_token": "1000.mock_token", "expires_in": 3600}).encode("utf-8")
        token_resp.__enter__.return_value = token_resp

        raw_rfc822_content = b"From: sender@example.com\r\nTo: alice@acme.com\r\nSubject: Test\r\n\r\nHello World!"
        msg_resp = MagicMock()
        msg_resp.read.return_value = raw_rfc822_content
        msg_resp.__enter__.return_value = msg_resp

        mock_urlopen.side_effect = [token_resp, msg_resp]

        data = self.client.stream_raw_message_rfc822("acc_1001", "msg_999")
        self.assertEqual(data, raw_rfc822_content)


if __name__ == "__main__":
    unittest.main()
