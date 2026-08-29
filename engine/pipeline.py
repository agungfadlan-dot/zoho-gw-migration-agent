from __future__ import annotations

"""
Asynchronous & Streaming Migration Pipeline.

Execution Stages:
1. User Provisioning (Google Admin SDK Directory API)
2. Calendar Migration (Google Calendar API)
3. Contacts Migration (Google People API)
4. Mailbox & Message Streaming (Gmail API import via RFC822 memory stream)

Security & Reliability:
- Zero disk spooling of email bodies or passwords.
- Idempotent checkpointing via CheckpointStore.
- Full Dry-Run simulation support.
- Real-time progress notifications.
"""

import hashlib
import time
from typing import List, Dict, Optional, Callable, Any, TYPE_CHECKING
if TYPE_CHECKING:
    from connectors.zoho_client import ZohoAdminClient
    from connectors.google_client import GoogleWorkspaceAdminClient
from connectors.base import ZohoUser, MailFolder, MailMessageMeta
from engine.checkpoint import CheckpointStore
from security.sanitizer import setup_secure_logger

logger = setup_secure_logger("migration_pipeline")


class MigrationProgressCallback:
    """Protocol for receiving real-time migration progress events."""

    def on_stage_start(self, stage_name: str, total_items: int) -> None:
        pass

    def on_item_progress(self, stage_name: str, current_item: str, index: int, total: int, status: str) -> None:
        pass

    def on_stage_complete(self, stage_name: str, summary: Dict[str, Any]) -> None:
        pass

    def on_log_message(self, level: str, message: str) -> None:
        pass


class MigrationPipeline:
    """Orchestrates end-to-end multi-stage migration."""

    def __init__(
        self,
        zoho_client: ZohoAdminClient,
        google_client: GoogleWorkspaceAdminClient,
        checkpoint_store: CheckpointStore,
        dry_run: bool = False,
        progress_callback: Optional[MigrationProgressCallback] = None,
    ):
        self.zoho = zoho_client
        self.google = google_client
        self.checkpoint = checkpoint_store
        self.dry_run = dry_run
        self.cb = progress_callback or MigrationProgressCallback()

    # --- STAGE 1: User Provisioning ---

    def run_user_provisioning(self, users: List[ZohoUser]) -> List[Dict[str, Any]]:
        """
        Stage 1: Discovers and provisions users in Google Workspace.
        Returns list of provisioned user summaries (including generated temp passwords for one-time admin export).
        """
        stage_name = "User Provisioning"
        self.cb.on_stage_start(stage_name, len(users))
        logger.info(f"Starting Stage 1: {stage_name} ({len(users)} users, dry_run={self.dry_run})...")

        results: List[Dict[str, Any]] = []

        for idx, u in enumerate(users, 1):
            self.checkpoint.register_user(u.zuid, u.email, u.first_name, u.last_name, u.aliases)

            if self.dry_run:
                self.cb.on_item_progress(stage_name, u.email, idx, len(users), "SIMULATED")
                results.append({
                    "email": u.email,
                    "status": "SIMULATED_CREATE",
                    "aliases": u.aliases,
                })
                continue

            try:
                res = self.google.provision_user(
                    email=u.email,
                    first_name=u.first_name,
                    last_name=u.last_name,
                    aliases=u.aliases
                )
                status = res.get("status", "PROVISIONED")
                self.checkpoint.update_user_status(u.email, status)
                self.cb.on_item_progress(stage_name, u.email, idx, len(users), status)
                results.append(res)
            except Exception as e:
                err_msg = str(e)
                logger.error(f"Failed to provision user {u.email}: {err_msg}")
                self.checkpoint.update_user_status(u.email, "FAILED", error_msg=err_msg)
                self.cb.on_item_progress(stage_name, u.email, idx, len(users), "FAILED")
                results.append({"email": u.email, "status": "FAILED", "error": err_msg})

        summary = {
            "total": len(users),
            "created": sum(1 for r in results if r.get("status") == "CREATED"),
            "existing": sum(1 for r in results if r.get("status") == "EXISTS"),
            "failed": sum(1 for r in results if r.get("status") == "FAILED"),
        }
        self.cb.on_stage_complete(stage_name, summary)
        return results

    # --- STAGE 2: Calendar Migration ---

    def run_calendar_migration(self, users: List[ZohoUser]) -> Dict[str, Any]:
        """Stage 2: Migrates calendar events for all active users."""
        stage_name = "Calendar Migration"
        self.cb.on_stage_start(stage_name, len(users))
        logger.info(f"Starting Stage 2: {stage_name} ({len(users)} users)...")

        total_events = 0
        synced_events = 0
        failed_events = 0

        for u_idx, u in enumerate(users, 1):
            if not u.mailbox_account_id:
                continue

            events = self.zoho.list_calendar_events(u.mailbox_account_id, u.email)
            total_events += len(events)

            for ev in events:
                if self.checkpoint.is_item_synced("CALENDAR", ev.event_id, u.email):
                    synced_events += 1
                    continue

                if self.dry_run:
                    synced_events += 1
                    continue

                try:
                    res = self.google.insert_calendar_event(u.email, ev)
                    dest_id = res.get("id")
                    self.checkpoint.record_item_sync(
                        entity_type="CALENDAR",
                        source_id=ev.event_id,
                        user_email=u.email,
                        destination_id=dest_id,
                        status="SYNCED"
                    )
                    synced_events += 1
                except Exception as e:
                    failed_events += 1
                    logger.warning(f"Failed to migrate event '{ev.title}' for {u.email}: {e}")
                    self.checkpoint.record_item_sync(
                        entity_type="CALENDAR",
                        source_id=ev.event_id,
                        user_email=u.email,
                        status="FAILED",
                        error_msg=str(e)
                    )

            self.cb.on_item_progress(stage_name, u.email, u_idx, len(users), f"Events: {len(events)}")

        summary = {"total_events": total_events, "synced": synced_events, "failed": failed_events}
        self.cb.on_stage_complete(stage_name, summary)
        return summary

    # --- STAGE 3: Contacts Migration ---

    def run_contacts_migration(self, users: List[ZohoUser]) -> Dict[str, Any]:
        """Stage 3: Migrates address book contacts for all active users."""
        stage_name = "Contacts Migration"
        self.cb.on_stage_start(stage_name, len(users))
        logger.info(f"Starting Stage 3: {stage_name} ({len(users)} users)...")

        total_contacts = 0
        synced_contacts = 0
        failed_contacts = 0

        for u_idx, u in enumerate(users, 1):
            if not u.mailbox_account_id:
                continue

            contacts = self.zoho.list_contacts(u.mailbox_account_id)
            total_contacts += len(contacts)

            for c in contacts:
                if self.checkpoint.is_item_synced("CONTACT", c.contact_id, u.email):
                    synced_contacts += 1
                    continue

                if self.dry_run:
                    synced_contacts += 1
                    continue

                try:
                    res = self.google.insert_contact(u.email, c)
                    dest_id = res.get("resourceName")
                    self.checkpoint.record_item_sync(
                        entity_type="CONTACT",
                        source_id=c.contact_id,
                        user_email=u.email,
                        destination_id=dest_id,
                        status="SYNCED"
                    )
                    synced_contacts += 1
                except Exception as e:
                    failed_contacts += 1
                    logger.warning(f"Failed to migrate contact '{c.display_name}' for {u.email}: {e}")
                    self.checkpoint.record_item_sync(
                        entity_type="CONTACT",
                        source_id=c.contact_id,
                        user_email=u.email,
                        status="FAILED",
                        error_msg=str(e)
                    )

            self.cb.on_item_progress(stage_name, u.email, u_idx, len(users), f"Contacts: {len(contacts)}")

        summary = {"total_contacts": total_contacts, "synced": synced_contacts, "failed": failed_contacts}
        self.cb.on_stage_complete(stage_name, summary)
        return summary

    # --- STAGE 4: Mailbox & Messages Streaming ---

    def run_mailbox_migration(self, users: List[ZohoUser], batch_size: int = 50) -> Dict[str, Any]:
        """
        Stage 4: Streams mail folders and RFC822 messages in memory from Zoho to Gmail.
        Zero disk persistence.
        """
        stage_name = "Mailbox Migration"
        self.cb.on_stage_start(stage_name, len(users))
        logger.info(f"Starting Stage 4: {stage_name} ({len(users)} users)...")

        total_messages = 0
        synced_messages = 0
        failed_messages = 0

        for u_idx, u in enumerate(users, 1):
            if not u.mailbox_account_id:
                continue

            folders = self.zoho.list_user_folders(u.mailbox_account_id)
            user_msg_count = sum(f.message_count for f in folders)
            total_messages += user_msg_count

            logger.info(f"Processing mailbox for {u.email} ({len(folders)} folders, ~{user_msg_count} msgs)...")

            for f in folders:
                folder_name = f.folder_name

                # Map folder to Gmail label
                label_id = self.checkpoint.get_google_label_id(u.email, f.folder_id)
                if not label_id:
                    if self.dry_run:
                        label_id = f"LABEL_{folder_name.upper()}"
                    else:
                        label_id = self.google.ensure_label(u.email, folder_name)
                    self.checkpoint.save_folder_mapping(u.email, f.folder_id, folder_name, label_id)

                # Paginate folder messages
                start = 1
                limit = batch_size
                while True:
                    messages = self.zoho.list_folder_messages(u.mailbox_account_id, f.folder_id, start=start, limit=limit)
                    if not messages:
                        break

                    for msg in messages:
                        if self.checkpoint.is_item_synced("MAIL", msg.message_id, u.email):
                            synced_messages += 1
                            continue

                        if self.dry_run:
                            synced_messages += 1
                            continue

                        try:
                            # Stream raw RFC822 directly in memory
                            raw_rfc822 = self.zoho.stream_raw_message_rfc822(u.mailbox_account_id, msg.message_id)
                            checksum = hashlib.sha256(raw_rfc822).hexdigest()

                            res = self.google.import_message(
                                user_email=u.email,
                                raw_rfc822_bytes=raw_rfc822,
                                label_ids=[label_id],
                                is_read=msg.is_read,
                                internal_date_ms=msg.received_time_ms
                            )
                            dest_id = res.get("id")

                            self.checkpoint.record_item_sync(
                                entity_type="MAIL",
                                source_id=msg.message_id,
                                user_email=u.email,
                                destination_id=dest_id,
                                checksum=checksum,
                                status="SYNCED"
                            )
                            synced_messages += 1
                        except Exception as e:
                            failed_messages += 1
                            logger.warning(f"Failed to stream message {msg.message_id} ({msg.subject}) for {u.email}: {e}")
                            self.checkpoint.record_item_sync(
                                entity_type="MAIL",
                                source_id=msg.message_id,
                                user_email=u.email,
                                status="FAILED",
                                error_msg=str(e)
                            )

                    if len(messages) < limit:
                        break
                    start += limit

            self.cb.on_item_progress(stage_name, u.email, u_idx, len(users), f"Mailbox Sync Complete")

        summary = {"total_messages": total_messages, "synced": synced_messages, "failed": failed_messages}
        self.cb.on_stage_complete(stage_name, summary)
        return summary
