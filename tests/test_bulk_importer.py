"""
Unit tests for engine/bulk_importer.py.
"""

import unittest
from unittest.mock import MagicMock
import tempfile
import os
import io
import zipfile
import shutil

from engine.checkpoint import CheckpointStore
from engine.bulk_importer import ZohoBulkZipImporter, parse_eml_metadata


class TestBulkImporter(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_checkpoint.db")
        self.checkpoint = CheckpointStore(db_path=self.db_path)

        self.mock_google = MagicMock()
        self.mock_google.get_or_create_label.return_value = "Label_Custom_123"
        self.mock_google.import_message.return_value = {"id": "g_msg_999"}

        self.importer = ZohoBulkZipImporter(
            google_client=self.mock_google,
            checkpoint_store=self.checkpoint,
            max_workers=2,
            dry_run=False,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_sample_zip(self, zip_path: str) -> None:
        """Creates a sample ZIP archive with nested folders and .eml messages."""
        with zipfile.ZipFile(zip_path, "w") as zf:
            # Message 1: Inbox
            eml1 = (
                b"From: sender1@example.com\r\n"
                b"To: recipient@example.com\r\n"
                b"Subject: Test Message 1\r\n"
                b"Date: Mon, 31 Aug 2026 10:00:00 +0000\r\n"
                b"Message-ID: <msg001@example.com>\r\n"
                b"\r\n"
                b"Hello from Inbox!"
            )
            zf.writestr("Inbox/msg1.eml", eml1)

            # Message 2: Sent
            eml2 = (
                b"From: recipient@example.com\r\n"
                b"To: sender1@example.com\r\n"
                b"Subject: Reply Message 2\r\n"
                b"Date: Mon, 31 Aug 2026 11:00:00 +0000\r\n"
                b"Message-ID: <msg002@example.com>\r\n"
                b"\r\n"
                b"Hello from Sent!"
            )
            zf.writestr("Sent/msg2.eml", eml2)

            # Message 3: Custom Nested Folder
            eml3 = (
                b"From: boss@example.com\r\n"
                b"To: recipient@example.com\r\n"
                b"Subject: Project Alpha Plan\r\n"
                b"Date: Mon, 31 Aug 2026 12:00:00 +0000\r\n"
                b"Message-ID: <msg003@example.com>\r\n"
                b"\r\n"
                b"Important Project Document"
            )
            zf.writestr("Projects/Alpha/plan.eml", eml3)

            # Extra non-email file (should be ignored)
            zf.writestr("metadata.json", b'{"version": 1}')

    def test_parse_eml_metadata(self):
        sample_eml = (
            b"From: test@domain.com\r\n"
            b"Subject: Hello\r\n"
            b"Date: Mon, 31 Aug 2026 12:00:00 +0000\r\n"
            b"Message-ID: <unique_123@domain.com>\r\n"
            b"X-Status: U\r\n"
            b"\r\n"
            b"Body text"
        )
        msg_id, internal_date, is_read, sha256 = parse_eml_metadata(sample_eml)
        self.assertEqual(msg_id, "unique_123@domain.com")
        self.assertIsNotNone(internal_date)
        self.assertFalse(is_read)
        self.assertTrue(len(sha256) == 64)

    def test_import_user_zip_success(self):
        zip_path = os.path.join(self.temp_dir, "test_export.zip")
        self._create_sample_zip(zip_path)

        res = self.importer.import_user_zip(zip_path, "test.user@andhika.com")
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["synced"], 3)
        self.assertEqual(res["skipped"], 0)
        self.assertEqual(res["failed"], 0)

        # Verify Google Client was called 3 times
        self.assertEqual(self.mock_google.import_message.call_count, 3)

        # Verify Checkpoint recorded 3 synced items
        stats = self.checkpoint.get_summary_stats()
        self.assertEqual(stats["items"]["MAIL"]["SYNCED"], 3)

    def test_idempotency_and_deduplication(self):
        zip_path = os.path.join(self.temp_dir, "test_export.zip")
        self._create_sample_zip(zip_path)

        # First run: 3 synced
        res1 = self.importer.import_user_zip(zip_path, "test.user@andhika.com")
        self.assertEqual(res1["synced"], 3)

        # Second run: 3 skipped, 0 synced
        res2 = self.importer.import_user_zip(zip_path, "test.user@andhika.com")
        self.assertEqual(res2["synced"], 0)
        self.assertEqual(res2["skipped"], 3)
        self.assertEqual(self.mock_google.import_message.call_count, 3)

    def test_import_directory_batch(self):
        export_dir = os.path.join(self.temp_dir, "batch_exports")
        os.makedirs(export_dir, exist_ok=True)

        user1_zip = os.path.join(export_dir, "user1@andhika.com.zip")
        user2_zip = os.path.join(export_dir, "user2@andhika.com.zip")

        self._create_sample_zip(user1_zip)
        self._create_sample_zip(user2_zip)

        results = self.importer.import_directory(export_dir)
        self.assertEqual(len(results), 2)
        self.assertIn("user1@andhika.com", results)
        self.assertIn("user2@andhika.com", results)
        self.assertEqual(results["user1@andhika.com"]["synced"], 3)
        self.assertEqual(results["user2@andhika.com"]["synced"], 3)


if __name__ == "__main__":
    unittest.main()
