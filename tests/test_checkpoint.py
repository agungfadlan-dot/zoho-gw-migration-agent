"""
Unit tests for engine/checkpoint.py
"""

import unittest
import os
import tempfile
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.checkpoint import CheckpointStore


class TestCheckpointStore(unittest.TestCase):

    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.temp_db.close()
        self.store = CheckpointStore(db_path=self.temp_db.name)

    def tearDown(self):
        if os.path.exists(self.temp_db.name):
            os.remove(self.temp_db.name)

    def test_user_registration_and_status(self):
        self.store.register_user("zuid_101", "alice@example.com", "Alice", "Smith", ["alice.s@example.com"])
        users = self.store.get_all_users()
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["email"], "alice@example.com")
        self.assertEqual(users[0]["status"], "PENDING")

        self.store.update_user_status("alice@example.com", "PROVISIONED")
        updated_users = self.store.get_all_users()
        self.assertEqual(updated_users[0]["status"], "PROVISIONED")

    def test_folder_mapping(self):
        self.store.save_folder_mapping("alice@example.com", "zoho_f_1", "Finance", "google_label_999")
        label_id = self.store.get_google_label_id("alice@example.com", "zoho_f_1")
        self.assertEqual(label_id, "google_label_999")
        self.assertIsNone(self.store.get_google_label_id("alice@example.com", "non_existent"))

    def test_item_sync_idempotency(self):
        self.assertFalse(self.store.is_item_synced("MAIL", "msg_123", "alice@example.com"))

        self.store.record_item_sync(
            entity_type="MAIL",
            source_id="msg_123",
            user_email="alice@example.com",
            destination_id="g_msg_456",
            status="SYNCED",
            checksum="abc123sha"
        )

        self.assertTrue(self.store.is_item_synced("MAIL", "msg_123", "alice@example.com"))

    def test_summary_stats(self):
        self.store.register_user("zuid_101", "alice@example.com", "Alice", "Smith", [])
        self.store.register_user("zuid_102", "bob@example.com", "Bob", "Jones", [])
        self.store.update_user_status("alice@example.com", "CREATED")

        self.store.record_item_sync("MAIL", "m1", "alice@example.com", status="SYNCED")
        self.store.record_item_sync("MAIL", "m2", "alice@example.com", status="FAILED", error_msg="Timeout")
        self.store.record_item_sync("CALENDAR", "c1", "bob@example.com", status="SYNCED")

        stats = self.store.get_summary_stats()
        self.assertEqual(stats["users"]["CREATED"], 1)
        self.assertEqual(stats["users"]["PENDING"], 1)
        self.assertEqual(stats["items"]["MAIL"]["SYNCED"], 1)
        self.assertEqual(stats["items"]["MAIL"]["FAILED"], 1)
        self.assertEqual(stats["items"]["CALENDAR"]["SYNCED"], 1)


if __name__ == "__main__":
    unittest.main()
