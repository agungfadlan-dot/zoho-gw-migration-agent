"""
Scalable Zoho Admin Bulk Export Importer.

Security & Architecture:
- Zero user passwords required (uses Google Domain-Wide Delegation).
- Zero full-disk archive extraction (streams .eml files entry-by-entry directly from ZIP in RAM).
- Idempotent and resumable via CheckpointStore (SHA-256 / Message-ID deduplication).
- Native folder-to-Gmail label translation preserving hierarchies.
- High-throughput multi-threaded worker pipeline.
"""

import os
import io
import re
import time
import zipfile
import hashlib
import email
from email.utils import parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Any, Callable, Generator, Tuple

from security.sanitizer import setup_secure_logger
from engine.checkpoint import CheckpointStore
from connectors.google_client import GoogleWorkspaceAdminClient, GoogleClientError

logger = setup_secure_logger("bulk_importer")

# Standard folder normalization map
STANDARD_FOLDERS = {
    "inbox": "INBOX",
    "sent": "SENT",
    "sent messages": "SENT",
    "sent items": "SENT",
    "trash": "TRASH",
    "deleted": "TRASH",
    "deleted items": "TRASH",
    "bin": "TRASH",
    "drafts": "DRAFT",
    "draft": "DRAFT",
    "spam": "SPAM",
    "junk": "SPAM",
    "starred": "STARRED",
}


def parse_eml_metadata(eml_bytes: bytes) -> Tuple[str, Optional[int], bool, str]:
    """
    Parses key RFC822 metadata from raw email bytes.
    Returns: (message_id_or_hash, internal_date_ms, is_read, sha256_checksum)
    """
    sha256 = hashlib.sha256(eml_bytes).hexdigest()
    msg_id = ""
    internal_date_ms = None
    is_read = True

    try:
        msg = email.message_from_bytes(eml_bytes)
        
        # Message-ID header
        header_msg_id = msg.get("Message-ID") or msg.get("Message-Id") or msg.get("message-id")
        if header_msg_id:
            msg_id = str(header_msg_id).strip().strip("<>").strip()

        # Date header
        date_hdr = msg.get("Date") or msg.get("date")
        if date_hdr:
            try:
                dt = parsedate_to_datetime(date_hdr)
                if dt:
                    internal_date_ms = int(dt.timestamp() * 1000)
            except Exception:
                pass
                
        # Flags / Status header (X-Status, X-Mozilla-Status, etc.)
        status_hdr = str(msg.get("X-Status", "") or msg.get("Status", "")).upper()
        if "U" in status_hdr or "NEW" in status_hdr:
            is_read = False
    except Exception as err:
        logger.debug(f"Error parsing RFC822 metadata: {err}")

    if not msg_id:
        msg_id = f"sha256:{sha256}"

    return msg_id, internal_date_ms, is_read, sha256


class ZohoBulkZipImporter:
    """Streams and imports Zoho Admin bulk export ZIP archives directly into Google Workspace."""

    def __init__(
        self,
        google_client: GoogleWorkspaceAdminClient,
        checkpoint_store: CheckpointStore,
        max_workers: int = 5,
        dry_run: bool = False,
    ):
        self.google_client = google_client
        self.checkpoint = checkpoint_store
        self.max_workers = max(1, max_workers)
        self.dry_run = dry_run
        self._label_cache: Dict[Tuple[str, str], str] = {}  # (user_email, folder_path) -> label_id

    def _resolve_label(self, user_email: str, folder_path: str) -> str:
        """Resolves folder path to Gmail label ID, creating custom labels if needed."""
        norm = folder_path.strip("/\\").lower()

        # System labels
        if norm in STANDARD_FOLDERS:
            return STANDARD_FOLDERS[norm]

        # Top-level root or empty folder -> INBOX
        if not norm or norm in [".", "root", "mail"]:
            return "INBOX"

        cache_key = (user_email.lower(), folder_path)
        if cache_key in self._label_cache:
            return self._label_cache[cache_key]

        # Checkpoint DB lookup
        db_label = self.checkpoint.get_google_label_id(user_email, folder_path)
        if db_label:
            self._label_cache[cache_key] = db_label
            return db_label

        if self.dry_run:
            return f"DRYRUN_LABEL_{norm}"

        # Create or fetch label in Gmail
        try:
            label_name = folder_path.replace("\\", "/").strip("/")
            label_id = self.google_client.get_or_create_label(user_email, label_name)
            self._label_cache[cache_key] = label_id
            self.checkpoint.save_folder_mapping(user_email, folder_path, label_name, label_id)
            return label_id
        except Exception as err:
            logger.warning(f"Could not resolve label '{folder_path}' for {user_email}: {err}. Defaulting to INBOX.")
            return "INBOX"

    def iterate_zip_entries(self, zip_path_or_file: Any) -> Generator[Tuple[str, str, bytes], None, None]:
        """
        Memory-efficient streaming generator that yields (folder_path, filename, eml_bytes)
        without extracting the entire ZIP archive to disk.
        """
        with zipfile.ZipFile(zip_path_or_file, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue

                filename = info.filename
                # Skip OS metadata / non-email files
                if filename.startswith("__MACOSX") or filename.endswith(".DS_Store") or filename.endswith(".json"):
                    continue

                if not (filename.lower().endswith(".eml") or filename.lower().endswith(".msg") or filename.lower().endswith(".txt")):
                    continue

                # Derive folder path from zip archive hierarchy
                parts = filename.replace("\\", "/").split("/")
                folder_path = "/".join(parts[:-1]) if len(parts) > 1 else "Inbox"
                leaf_name = parts[-1]

                with zf.open(info) as f:
                    eml_bytes = f.read()

                yield folder_path, leaf_name, eml_bytes

    def import_user_zip(
        self,
        zip_path_or_file: Any,
        target_user_email: str,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """
        Streams a single user's Zoho export ZIP directly into their Google Workspace mailbox.
        """
        user_email = target_user_email.lower().strip()
        logger.info(f"Starting bulk import for {user_email} from archive...")

        synced_count = 0
        skipped_count = 0
        failed_count = 0
        bytes_streamed = 0
        start_time = time.time()

        try:
            for folder_path, leaf_name, eml_bytes in self.iterate_zip_entries(zip_path_or_file):
                msg_id, internal_date_ms, is_read, sha256 = parse_eml_metadata(eml_bytes)
                source_id = f"{folder_path}/{msg_id}"

                # Deduplication check
                if self.checkpoint.is_item_synced("MAIL", source_id, user_email):
                    skipped_count += 1
                    continue

                # Resolve label
                label_id = self._resolve_label(user_email, folder_path)
                label_ids = [label_id]

                if self.dry_run:
                    synced_count += 1
                    bytes_streamed += len(eml_bytes)
                    self.checkpoint.record_item_sync(
                        "MAIL", source_id, user_email, destination_id="dryrun_msg",
                        status="SYNCED", checksum=sha256
                    )
                else:
                    try:
                        res = self.google_client.import_message(
                            user_email=user_email,
                            raw_rfc822_bytes=eml_bytes,
                            label_ids=label_ids,
                            is_read=is_read,
                            internal_date_ms=internal_date_ms,
                        )
                        dest_id = res.get("id", "imported")
                        self.checkpoint.record_item_sync(
                            "MAIL", source_id, user_email, destination_id=dest_id,
                            status="SYNCED", checksum=sha256
                        )
                        synced_count += 1
                        bytes_streamed += len(eml_bytes)
                    except Exception as err:
                        logger.error(f"Failed to import message {msg_id} in {folder_path} for {user_email}: {err}")
                        self.checkpoint.record_item_sync(
                            "MAIL", source_id, user_email, status="FAILED",
                            error_msg=str(err), checksum=sha256
                        )
                        failed_count += 1

                # Report incremental progress
                if progress_callback and (synced_count + skipped_count + failed_count) % 25 == 0:
                    progress_callback({
                        "user_email": user_email,
                        "synced": synced_count,
                        "skipped": skipped_count,
                        "failed": failed_count,
                        "bytes_streamed": bytes_streamed,
                    })

        except Exception as e:
            logger.error(f"Fatal error streaming ZIP for {user_email}: {e}")
            raise

        elapsed = time.time() - start_time
        logger.info(
            f"Completed bulk import for {user_email} in {elapsed:.2f}s: "
            f"{synced_count} synced, {skipped_count} skipped, {failed_count} failed, "
            f"{(bytes_streamed / (1024 * 1024)):.2f} MB streamed."
        )

        result = {
            "user_email": user_email,
            "synced": synced_count,
            "skipped": skipped_count,
            "failed": failed_count,
            "bytes_streamed": bytes_streamed,
            "elapsed_seconds": round(elapsed, 2),
            "status": "SUCCESS" if failed_count == 0 else "PARTIAL",
        }

        if progress_callback:
            progress_callback(result)

        return result

    def import_directory(
        self,
        directory_path: str,
        user_mapping: Optional[Dict[str, str]] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """
        Discovers all user export ZIP archives in a directory and imports them in parallel.
        Matches files by pattern (e.g. `user@domain.com.zip` or user prefix).
        """
        if not os.path.isdir(directory_path):
            raise ValueError(f"Export directory does not exist: {directory_path}")

        zip_files = [
            os.path.join(directory_path, f)
            for f in os.listdir(directory_path)
            if f.lower().endswith(".zip") and not f.startswith(".")
        ]

        logger.info(f"Discovered {len(zip_files)} export ZIP files in {directory_path}.")
        results: Dict[str, Any] = {}

        # Auto-detect target email from filename
        tasks = []
        for zpath in zip_files:
            bname = os.path.basename(zpath)
            name_without_ext = os.path.splitext(bname)[0]

            email_match = re.search(r"[\w\.-]+@[a-zA-Z0-9\.-]+", name_without_ext)
            target_email = None

            if email_match:
                target_email = email_match.group(0).lower()
            elif user_mapping and bname in user_mapping:
                target_email = user_mapping[bname].lower()
            else:
                candidate = name_without_ext.replace("_", "@", 1)
                if "@" in candidate:
                    target_email = candidate.lower()

            if target_email:
                tasks.append((zpath, target_email))
            else:
                logger.warning(f"Could not determine target user email for ZIP file: {bname}. Skipping.")

        # Execute parallel workers
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_email = {
                executor.submit(self.import_user_zip, zpath, uemail, progress_callback): uemail
                for zpath, uemail in tasks
            }

            for future in as_completed(future_to_email):
                uemail = future_to_email[future]
                try:
                    res = future.result()
                    results[uemail] = res
                except Exception as exc:
                    logger.error(f"User import generated an exception for {uemail}: {exc}")
                    results[uemail] = {
                        "user_email": uemail,
                        "status": "FAILED",
                        "error": str(exc),
                    }

        return results
