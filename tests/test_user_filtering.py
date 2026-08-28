"""
Unit tests for pilot testing and target user filtering in MigrationWorkflow.
"""

import unittest
from unittest.mock import MagicMock, patch
import os
import json
import tempfile
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from security.vault import EphemeralVault
from connectors.base import ZohoUser
from agent.workflow import MigrationWorkflow


class TestUserFiltering(unittest.TestCase):

    def setUp(self):
        self.vault = EphemeralVault()
        self.vault.store("zoho_client_id", "cid")
        self.vault.store("zoho_client_secret", "sec")
        self.vault.store("zoho_refresh_token", "ref")
        dummy_sa = json.dumps({
            "type": "service_account",
            "project_id": "test-project",
            "private_key_id": "key_123",
            "private_key": "-----BEGIN PRIVATE KEY-----\\nMIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQDOf2Zq4\\n-----END PRIVATE KEY-----\\n",
            "client_email": "migration-sa@test-project.iam.gserviceaccount.com",
            "client_id": "1122334455",
            "token_uri": "https://oauth2.googleapis.com/token"
        })
        self.vault.store("google_sa_json", dummy_sa)
        self.vault.store("google_admin_email", "admin@test.com")

        self.users = [
            ZohoUser(zuid="1", email="alice@test.com", first_name="Alice", last_name="A", display_name="Alice A", role="admin", is_active=True, aliases=["a.alias@test.com"], mailbox_account_id="acc1", storage_used_bytes=100),
            ZohoUser(zuid="2", email="bob@test.com", first_name="Bob", last_name="B", display_name="Bob B", role="member", is_active=True, aliases=[], mailbox_account_id="acc2", storage_used_bytes=200),
            ZohoUser(zuid="3", email="charlie@test.com", first_name="Charlie", last_name="C", display_name="Charlie C", role="member", is_active=True, aliases=["c.alias@test.com"], mailbox_account_id="acc3", storage_used_bytes=300),
            ZohoUser(zuid="4", email="david@test.com", first_name="David", last_name="D", display_name="David D", role="member", is_active=True, aliases=[], mailbox_account_id="acc4", storage_used_bytes=400),
        ]

    def tearDown(self):
        self.vault.purge()

    def test_filter_by_users_flag(self):
        workflow = MigrationWorkflow(
            vault=self.vault,
            target_users_str="alice@test.com, charlie@test.com"
        )
        selected = workflow.select_target_users(self.users, auto_confirm=True)
        self.assertEqual(len(selected), 2)
        self.assertEqual([u.email for u in selected], ["alice@test.com", "charlie@test.com"])

    def test_filter_by_alias(self):
        workflow = MigrationWorkflow(
            vault=self.vault,
            target_users_str="a.alias@test.com"
        )
        selected = workflow.select_target_users(self.users, auto_confirm=True)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].email, "alice@test.com")

    def test_filter_by_pilot_count(self):
        workflow = MigrationWorkflow(
            vault=self.vault,
            pilot_count=2
        )
        selected = workflow.select_target_users(self.users, auto_confirm=True)
        self.assertEqual(len(selected), 2)
        self.assertEqual([u.email for u in selected], ["alice@test.com", "bob@test.com"])

    def test_filter_by_users_file(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("# Pilot migration list\n")
            f.write("bob@test.com\n")
            f.write("david@test.com\n")
            file_path = f.name

        try:
            workflow = MigrationWorkflow(
                vault=self.vault,
                users_file=file_path
            )
            selected = workflow.select_target_users(self.users, auto_confirm=True)
            self.assertEqual(len(selected), 2)
            self.assertEqual([u.email for u in selected], ["bob@test.com", "david@test.com"])
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)

    @patch("builtins.input", side_effect=["4", "bob@test.com"])
    def test_interactive_email_selection(self, mock_input):
        workflow = MigrationWorkflow(vault=self.vault)
        selected = workflow.select_target_users(self.users, auto_confirm=False)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].email, "bob@test.com")

    @patch("builtins.input", side_effect=["5", "1, 3"])
    def test_interactive_index_selection(self, mock_input):
        workflow = MigrationWorkflow(vault=self.vault)
        selected = workflow.select_target_users(self.users, auto_confirm=False)
        self.assertEqual(len(selected), 2)
        self.assertEqual([u.email for u in selected], ["alice@test.com", "charlie@test.com"])


if __name__ == "__main__":
    unittest.main()
