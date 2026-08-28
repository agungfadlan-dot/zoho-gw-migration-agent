"""
End-to-End Migration Pipeline Simulation Test.
"""

import unittest
from unittest.mock import MagicMock, patch
import tempfile
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from security.vault import EphemeralVault
from connectors.base import ZohoUser, MailFolder, MailMessageMeta, CalendarEvent, ContactRecord
from engine.checkpoint import CheckpointStore
from engine.pipeline import MigrationPipeline


class TestEndToEndMockPipeline(unittest.TestCase):

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.temp_db.close()
        self.checkpoint = CheckpointStore(db_path=self.temp_db.name)

        # Mock Zoho Client
        self.mock_zoho = MagicMock()
        self.sample_users = [
            ZohoUser(
                zuid="zuid_001",
                email="alice@company.com",
                first_name="Alice",
                last_name="Wang",
                display_name="Alice Wang",
                role="admin",
                is_active=True,
                aliases=["alice.w@company.com"],
                mailbox_account_id="acc_001",
                storage_used_bytes=52428800
            )
        ]

        self.sample_folders = [
            MailFolder(folder_id="f_inbox", folder_name="Inbox", folder_path="Inbox", message_count=1)
        ]
        self.sample_messages = [
            MailMessageMeta(
                message_id="msg_001",
                folder_id="f_inbox",
                subject="Q3 Roadmap",
                sender="boss@company.com",
                received_time_ms=1690000000000,
                size_bytes=4096,
                is_read=True
            )
        ]
        self.sample_events = [
            CalendarEvent(
                event_id="ev_001",
                title="Strategy Sync",
                start_time="2026-09-01T10:00:00Z",
                end_time="2026-09-01T11:00:00Z"
            )
        ]
        self.sample_contacts = [
            ContactRecord(
                contact_id="cnt_001",
                first_name="Bob",
                last_name="Taylor",
                display_name="Bob Taylor",
                email_addresses=["bob@partner.org"]
            )
        ]

        self.mock_zoho.list_organization_users.return_value = self.sample_users
        self.mock_zoho.list_user_folders.return_value = self.sample_folders
        self.mock_zoho.list_folder_messages.side_effect = lambda acc, f_id, start, limit: self.sample_messages if start == 1 else []
        self.mock_zoho.stream_raw_message_rfc822.return_value = b"From: boss@company.com\r\nSubject: Q3 Roadmap\r\n\r\nBody text"
        self.mock_zoho.list_calendar_events.return_value = self.sample_events
        self.mock_zoho.list_contacts.return_value = self.sample_contacts

        # Mock Google Client
        self.mock_google = MagicMock()
        self.mock_google.provision_user.return_value = {
            "status": "CREATED",
            "email": "alice@company.com",
            "temp_password": "SecurePassword123!@#"
        }
        self.mock_google.ensure_label.return_value = "Label_Inbox"
        self.mock_google.import_message.return_value = {"id": "g_msg_001"}
        self.mock_google.insert_calendar_event.return_value = {"id": "g_ev_001"}
        self.mock_google.insert_contact.return_value = {"resourceName": "people/c001"}

        self.pipeline = MigrationPipeline(
            zoho_client=self.mock_zoho,
            google_client=self.mock_google,
            checkpoint_store=self.checkpoint,
            dry_run=False
        )

    def tearDown(self):
        if os.path.exists(self.temp_db.name):
            os.remove(self.temp_db.name)

    def test_full_staged_migration_flow(self):
        # Stage 1: User Provisioning
        prov_res = self.pipeline.run_user_provisioning(self.sample_users)
        self.assertEqual(len(prov_res), 1)
        self.assertEqual(prov_res[0]["status"], "CREATED")
        self.mock_google.provision_user.assert_called_once_with(
            email="alice@company.com",
            first_name="Alice",
            last_name="Wang",
            aliases=["alice.w@company.com"]
        )

        # Stage 2: Calendar Migration
        cal_res = self.pipeline.run_calendar_migration(self.sample_users)
        self.assertEqual(cal_res["synced"], 1)
        self.assertEqual(cal_res["failed"], 0)
        self.assertTrue(self.checkpoint.is_item_synced("CALENDAR", "ev_001", "alice@company.com"))

        # Stage 3: Contacts Migration
        cont_res = self.pipeline.run_contacts_migration(self.sample_users)
        self.assertEqual(cont_res["synced"], 1)
        self.assertEqual(cont_res["failed"], 0)
        self.assertTrue(self.checkpoint.is_item_synced("CONTACT", "cnt_001", "alice@company.com"))

        # Stage 4: Mailbox Streaming Migration
        mail_res = self.pipeline.run_mailbox_migration(self.sample_users)
        self.assertEqual(mail_res["synced"], 1)
        self.assertEqual(mail_res["failed"], 0)
        self.assertTrue(self.checkpoint.is_item_synced("MAIL", "msg_001", "alice@company.com"))

        # Check DB stats
        stats = self.checkpoint.get_summary_stats()
        self.assertEqual(stats["items"]["MAIL"]["SYNCED"], 1)
        self.assertEqual(stats["items"]["CALENDAR"]["SYNCED"], 1)
        self.assertEqual(stats["items"]["CONTACT"]["SYNCED"], 1)


if __name__ == "__main__":
    unittest.main()
