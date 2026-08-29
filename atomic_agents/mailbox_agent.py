"""
Atomic Agent: Mailbox Streaming Migration Agent.
Streams RFC822 messages directly in memory, maps folders to Gmail labels, and enforces rate limits.
"""

import hashlib
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable
from atomic_agents.base import AtomicAgent
from connectors.zoho_client import ZohoAdminClient
from connectors.google_client import GoogleWorkspaceAdminClient
from connectors.base import ZohoUser, MailFolder
from engine.checkpoint import CheckpointStore
from engine.rate_limiter import TokenBucket, retry_with_backoff


@dataclass
class MailboxStreamingRequest:
    """Input payload for MailboxStreamingAgent."""
    users: List[ZohoUser]
    zoho_client: ZohoAdminClient
    google_client: GoogleWorkspaceAdminClient
    checkpoint_store: CheckpointStore
    dry_run: bool = False
    progress_callback: Optional[Callable[[str, int, int, str], None]] = None
    pause_controller: Optional[Any] = None


@dataclass
class MailboxStreamingSummary:
    """Output payload from MailboxStreamingAgent."""
    total_messages_synced: int = 0
    total_messages_skipped: int = 0
    total_messages_failed: int = 0
    total_bytes_streamed: int = 0
    user_results: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "synced": self.total_messages_synced,
            "skipped": self.total_messages_skipped,
            "failed": self.total_messages_failed,
            "bytes_streamed": self.total_bytes_streamed,
            "user_results": self.user_results
        }


class MailboxStreamingAgent(AtomicAgent[MailboxStreamingRequest, MailboxStreamingSummary]):
    """
    Atomic Agent responsible strictly for mailbox folder mapping,
    RFC822 in-memory message streaming, and per-user Gmail rate limiting.
    """

    MAX_MESSAGE_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB Gmail standard import limit

    def __init__(self):
        super().__init__(
            name="MailboxStreamingAgent",
            description="Direct in-memory RFC822 email streaming with folder-to-label mapping."
        )

    def execute(self, input_data: MailboxStreamingRequest) -> MailboxStreamingSummary:
        total_synced = 0
        total_skipped = 0
        total_failed = 0
        total_bytes = 0
        user_results = {}

        for user in input_data.users:
            if not user.mailbox_account_id:
                self.logger.warning(f"Skipping {user.email}: No Zoho mailbox account ID found.")
                continue

            # Token bucket per mailbox to respect Gmail's 250 units/sec limit (~2.5 msgs/sec)
            rate_limiter = TokenBucket(rate_per_second=2.5, capacity=5.0)

            try:
                folders = input_data.zoho_client.list_user_folders(user.email, user.mailbox_account_id)
            except Exception as e:
                self.logger.error(f"Failed to fetch folders for {user.email}: {e}")
                user_results[user.email] = {"synced": 0, "skipped": 0, "failed": 1, "bytes": 0}
                total_failed += 1
                continue

            label_map = self._map_and_create_labels(user.email, folders, input_data)
            u_synced, u_skipped, u_failed, u_bytes = self._stream_user_mailbox(
                user=user,
                folders=folders,
                label_map=label_map,
                rate_limiter=rate_limiter,
                input_data=input_data
            )

            user_results[user.email] = {
                "synced": u_synced,
                "skipped": u_skipped,
                "failed": u_failed,
                "bytes": u_bytes
            }
            total_synced += u_synced
            total_skipped += u_skipped
            total_failed += u_failed
            total_bytes += u_bytes

        return MailboxStreamingSummary(
            total_messages_synced=total_synced,
            total_messages_skipped=total_skipped,
            total_messages_failed=total_failed,
            total_bytes_streamed=total_bytes,
            user_results=user_results
        )

    def _map_and_create_labels(
        self,
        user_email: str,
        folders: List[MailFolder],
        input_data: MailboxStreamingRequest
    ) -> Dict[str, str]:
        label_map: Dict[str, str] = {}
        for folder in folders:
            g_label_id = self._translate_system_folder(folder.folder_name)
            if not g_label_id:
                if input_data.dry_run:
                    g_label_id = f"LABEL_{folder.folder_name}"
                else:
                    cached_id = input_data.checkpoint_store.get_label_mapping(user_email, folder.folder_id)
                    if cached_id:
                        g_label_id = cached_id
                    else:
                        try:
                            g_label_id = input_data.google_client.ensure_label(user_email, folder.folder_name)
                            input_data.checkpoint_store.record_folder_mapping(
                                user_email=user_email,
                                zoho_folder_id=folder.folder_id,
                                zoho_folder_name=folder.folder_name,
                                google_label_id=g_label_id
                            )
                        except Exception as e:
                            self.logger.error(f"Failed to create label '{folder.folder_name}' for {user_email}: {e}")
                            g_label_id = "INBOX"

            label_map[folder.folder_id] = g_label_id
        return label_map

    def _stream_user_mailbox(
        self,
        user: ZohoUser,
        folders: List[MailFolder],
        label_map: Dict[str, str],
        rate_limiter: TokenBucket,
        input_data: MailboxStreamingRequest
    ):
        u_synced = 0
        u_skipped = 0
        u_failed = 0
        u_bytes = 0

        for folder in folders:
            if input_data.pause_controller:
                input_data.pause_controller.wait_if_paused()

            label_id = label_map.get(folder.folder_id, "INBOX")
            start = 1
            limit = 100

            while True:
                if input_data.pause_controller:
                    input_data.pause_controller.wait_if_paused()

                try:
                    messages = retry_with_backoff(
                        lambda: input_data.zoho_client.list_folder_messages(
                            user_email=user.email,
                            account_id=user.mailbox_account_id,
                            folder_id=folder.folder_id,
                            start=start,
                            limit=limit
                        ),
                        pause_controller=input_data.pause_controller
                    )
                except Exception as e:
                    self.logger.error(f"Failed listing messages in folder '{folder.folder_name}' for {user.email}: {e}")
                    break

                if not messages:
                    break

                for msg in messages:
                    if input_data.pause_controller:
                        input_data.pause_controller.wait_if_paused()

                    if input_data.checkpoint_store.is_item_synced("EMAIL", msg.message_id, user.email):
                        u_skipped += 1
                        continue

                    if input_data.dry_run:
                        u_synced += 1
                        u_bytes += msg.size_bytes
                        continue

                    # Guardrail: Check message size against Gmail import limits
                    if msg.size_bytes > self.MAX_MESSAGE_SIZE_BYTES:
                        self.logger.warning(
                            f"Skipping email {msg.message_id} ({msg.subject[:30]}): Size {msg.size_bytes} exceeds Gmail 25MB limit."
                        )
                        input_data.checkpoint_store.record_item_sync(
                            entity_type="EMAIL",
                            source_id=msg.message_id,
                            user_email=user.email,
                            status="FAILED_SIZE_EXCEEDED",
                            error_msg="Message size exceeds 25MB limit"
                        )
                        u_failed += 1
                        continue

                    # Direct in-memory streaming
                    try:
                        rate_limiter.acquire()
                        raw_bytes = retry_with_backoff(
                            lambda: input_data.zoho_client.stream_raw_message_rfc822(
                                user_email=user.email,
                                account_id=user.mailbox_account_id,
                                message_id=msg.message_id
                            ),
                            pause_controller=input_data.pause_controller
                        )

                        if not raw_bytes:
                            raise ValueError(f"Empty raw RFC822 payload received for message {msg.message_id}")

                        # 1:1 Byte Integrity Checksum
                        sha256_checksum = hashlib.sha256(raw_bytes).hexdigest()

                        label_ids = [label_id] if label_id.startswith("LABEL_") or label_id in ["INBOX", "SENT", "TRASH", "SPAM", "DRAFT"] else ["INBOX"]
                        is_unread = not msg.is_read

                        g_msg = retry_with_backoff(
                            lambda: input_data.google_client.import_message_rfc822(
                                user_email=user.email,
                                raw_rfc822_bytes=raw_bytes,
                                label_ids=label_ids,
                                is_unread=is_unread,
                                internal_date_ms=msg.received_time_ms
                            ),
                            pause_controller=input_data.pause_controller
                        )

                        g_id = g_msg.get("id") if isinstance(g_msg, dict) else None
                        input_data.checkpoint_store.record_item_sync(
                            entity_type="EMAIL",
                            source_id=msg.message_id,
                            user_email=user.email,
                            destination_id=g_id,
                            status="SYNCED",
                            checksum=sha256_checksum
                        )
                        u_synced += 1
                        u_bytes += len(raw_bytes)
                    except Exception as e:
                        u_failed += 1
                        error_msg = str(e)
                        self.logger.error(f"Failed streaming message {msg.message_id} for {user.email}: {error_msg}")
                        input_data.checkpoint_store.record_item_sync(
                            entity_type="EMAIL",
                            source_id=msg.message_id,
                            user_email=user.email,
                            status="FAILED",
                            error_msg=error_msg
                        )

                if len(messages) < limit:
                    break
                start += limit

        return u_synced, u_skipped, u_failed, u_bytes

    def _translate_system_folder(self, folder_name: str) -> Optional[str]:
        mapping = {
            "inbox": "INBOX",
            "sent": "SENT",
            "sent items": "SENT",
            "drafts": "DRAFT",
            "trash": "TRASH",
            "deleted items": "TRASH",
            "spam": "SPAM",
            "junk": "SPAM",
            "starred": "STARRED",
            "important": "IMPORTANT"
        }
        return mapping.get(folder_name.strip().lower())
