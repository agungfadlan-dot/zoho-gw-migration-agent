"""
Comprehensive Unit & Integration Tests for Atomic Agents Architecture.
Verifies single-responsibility isolation, error boundaries, and security enforcement.
"""

import unittest
from unittest.mock import MagicMock, patch
import os
import json
import tempfile
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from security.vault import EphemeralVault
from connectors.base import ZohoUser, MailFolder, MailMessageMeta, CalendarEvent, ContactRecord
from engine.checkpoint import CheckpointStore
from atomic_agents.security_auditor_agent import SecurityAuditorAgent, AuditRequest
from atomic_agents.discovery_agent import DiscoveryAssessmentAgent, DiscoveryRequest
from atomic_agents.provisioning_agent import UserProvisioningAgent, ProvisioningRequest
from atomic_agents.calendar_agent import CalendarMigrationAgent, CalendarSyncRequest
from atomic_agents.contacts_agent import ContactsMigrationAgent, ContactsSyncRequest
from atomic_agents.mailbox_agent import MailboxStreamingAgent, MailboxStreamingRequest
from atomic_agents.supervisor import MigrationSupervisor


class TestAtomicAgents(unittest.TestCase):

    def setUp(self):
        self.vault = EphemeralVault()
        self.vault.store("zoho_client_id", "test_zoho_id")
        self.vault.store("zoho_client_secret", "test_zoho_secret")
        self.vault.store("zoho_refresh_token", "1000.test_refresh_token.abcdef")
        
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
        self.vault.store("google_admin_email", "admin@company.com")

        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.checkpoint = CheckpointStore(db_path=self.temp_db.name)

        self.sample_user = ZohoUser(
            zuid="z100",
            email="alice@company.com",
            first_name="Alice",
            last_name="Smith",
            display_name="Alice Smith",
            role="member",
            is_active=True,
            aliases=["alice.s@company.com"],
            mailbox_account_id="acc100",
            storage_used_bytes=5 * 1024 * 1024
        )

    def tearDown(self):
        self.vault.purge()
        if os.path.exists(self.temp_db.name):
            os.remove(self.temp_db.name)

    @patch("connectors.zoho_client.ZohoAdminClient.test_connection", return_value={"status": "connected", "org_name": "Acme Org", "org_id": "999"})
    @patch("connectors.google_client.GoogleWorkspaceAdminClient.test_connection", return_value={"status": "connected", "client_email": "migration-sa@test-project.iam.gserviceaccount.com"})
    def test_security_auditor_agent_success(self, mock_g, mock_z):
        agent = SecurityAuditorAgent()
        req = AuditRequest(
            vault=self.vault,
            zoho_domain="zoho.com",
            zoho_scopes=["ZohoMail.organization.accounts.READ", "ZohoMail.messages.READ"],
            google_admin_email="admin@company.com"
        )
        res = agent.run(req)
        self.assertTrue(res.success)
        self.assertTrue(res.data.is_compliant)
        self.assertEqual(res.data.zoho_org_name, "Acme Org")
        self.assertEqual(len(res.data.checks_passed), 5)

    def test_security_auditor_agent_scope_violation(self):
        agent = SecurityAuditorAgent()
        req = AuditRequest(
            vault=self.vault,
            zoho_domain="zoho.com",
            zoho_scopes=["ZohoMail.messages.CREATE", "ZohoMail.messages.DELETE"]
        )
        res = agent.run(req)
        self.assertFalse(res.success)
        self.assertIn("failed", res.error.lower())

    def test_discovery_agent_pilot_cohort(self):
        agent = DiscoveryAssessmentAgent()
        mock_zoho = MagicMock()
        mock_zoho.list_organization_users.return_value = [
            self.sample_user,
            ZohoUser(zuid="z200", email="heavy@company.com", first_name="Heavy", last_name="User", display_name="Heavy", role="admin", is_active=True, aliases=[], mailbox_account_id="acc200", storage_used_bytes=100 * 1024 * 1024),
            ZohoUser(zuid="z300", email="light@company.com", first_name="Light", last_name="User", display_name="Light", role="member", is_active=True, aliases=[], mailbox_account_id="acc300", storage_used_bytes=1 * 1024 * 1024)
        ]
        mock_zoho.list_user_folders.return_value = [MailFolder(folder_id="f1", folder_name="Inbox", folder_path="/Inbox", message_count=10, unread_count=2)]
        mock_zoho.list_calendar_events.return_value = []
        mock_zoho.list_contacts.return_value = []

        req = DiscoveryRequest(zoho_client=mock_zoho, sample_items=False, pilot_candidate_count=2)
        res = agent.run(req)
        self.assertTrue(res.success)
        self.assertEqual(res.data.report.total_users, 3)
        # Pilot ranking should prefer light member over heavy admin
        self.assertEqual(res.data.recommended_pilot_cohort[0].email, "light@company.com")

    def test_provisioning_agent_dry_run(self):
        agent = UserProvisioningAgent()
        mock_google = MagicMock()
        req = ProvisioningRequest(
            users=[self.sample_user],
            google_client=mock_google,
            checkpoint_store=self.checkpoint,
            dry_run=True,
            export_csv=False
        )
        res = agent.run(req)
        self.assertTrue(res.success)
        self.assertEqual(res.data.created_count, 1)
        mock_google.provision_user.assert_not_called()

    def test_provisioning_agent_live(self):
        agent = UserProvisioningAgent()
        mock_google = MagicMock()
        mock_google.provision_user.return_value = {"status": "CREATED", "email": "alice@company.com", "temp_password": "FakePass123!@#"}

        req = ProvisioningRequest(
            users=[self.sample_user],
            google_client=mock_google,
            checkpoint_store=self.checkpoint,
            dry_run=False,
            export_csv=False
        )
        res = agent.run(req)
        self.assertTrue(res.success)
        self.assertEqual(res.data.created_count, 1)
        mock_google.provision_user.assert_called_once()

    def test_calendar_agent_sync(self):
        agent = CalendarMigrationAgent()
        mock_zoho = MagicMock()
        mock_google = MagicMock()
        mock_zoho.list_calendar_events.return_value = [
            CalendarEvent(
                event_id="evt_101",
                title="Q3 Strategy Meeting",
                start_time="2026-09-01T10:00:00Z",
                end_time="2026-09-01T11:00:00Z",
                location="Google Meet"
            )
        ]
        mock_google.insert_calendar_event.return_value = {"id": "g_evt_101"}

        req = CalendarSyncRequest(
            users=[self.sample_user],
            zoho_client=mock_zoho,
            google_client=mock_google,
            checkpoint_store=self.checkpoint,
            dry_run=False
        )
        res = agent.run(req)
        self.assertTrue(res.success)
        self.assertEqual(res.data.synced, 1)
        self.assertEqual(res.data.skipped, 0)
        self.assertTrue(self.checkpoint.is_item_synced("CALENDAR", "evt_101", "alice@company.com"))

    def test_contacts_agent_sync(self):
        agent = ContactsMigrationAgent()
        mock_zoho = MagicMock()
        mock_google = MagicMock()
        mock_zoho.list_contacts.return_value = [
            ContactRecord(
                contact_id="cnt_201",
                first_name="Bob",
                last_name="Partner",
                display_name="Bob Partner",
                email_addresses=["bob@partner.com"],
                phone_numbers=["+15550199"]
            )
        ]
        mock_google.insert_contact.return_value = {"resourceName": "people/c201"}

        req = ContactsSyncRequest(
            users=[self.sample_user],
            zoho_client=mock_zoho,
            google_client=mock_google,
            checkpoint_store=self.checkpoint,
            dry_run=False
        )
        res = agent.run(req)
        self.assertTrue(res.success)
        self.assertEqual(res.data.synced, 1)
        self.assertTrue(self.checkpoint.is_item_synced("CONTACT", "cnt_201", "alice@company.com"))

    def test_mailbox_agent_stream_and_size_guardrail(self):
        agent = MailboxStreamingAgent()
        mock_zoho = MagicMock()
        mock_google = MagicMock()

        mock_zoho.list_user_folders.return_value = [
            MailFolder(folder_id="f_inbox", folder_name="Inbox", folder_path="/Inbox", message_count=2, unread_count=0)
        ]
        mock_zoho.list_folder_messages.return_value = [
            MailMessageMeta(message_id="msg_normal", folder_id="f_inbox", subject="Normal Email", sender="client@acme.com", received_time_ms=1690000000000, size_bytes=1024),
            MailMessageMeta(message_id="msg_giant", folder_id="f_inbox", subject="Giant 30MB File", sender="big@acme.com", received_time_ms=1690000000000, size_bytes=30 * 1024 * 1024)
        ]
        mock_zoho.stream_raw_message_rfc822.return_value = b"From: client@acme.com\r\nSubject: Normal\r\n\r\nBody"
        mock_google.import_message_rfc822.return_value = {"id": "g_msg_normal"}

        req = MailboxStreamingRequest(
            users=[self.sample_user],
            zoho_client=mock_zoho,
            google_client=mock_google,
            checkpoint_store=self.checkpoint,
            dry_run=False
        )
        res = agent.run(req)
        self.assertTrue(res.success)
        self.assertEqual(res.data.total_messages_synced, 1)
        # Giant message >25MB should be rejected gracefully by the size guardrail
        self.assertEqual(res.data.total_messages_failed, 1)


if __name__ == "__main__":
    unittest.main()
