"""
Atomic Agent: Contacts Synchronization Agent.
Maps Zoho Address Book cards into Google People API schemas with field deduplication.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable
from atomic_agents.base import AtomicAgent
from connectors.zoho_client import ZohoAdminClient
from connectors.google_client import GoogleWorkspaceAdminClient
from connectors.base import ZohoUser
from engine.checkpoint import CheckpointStore
from engine.rate_limiter import TokenBucket, retry_with_backoff


@dataclass
class ContactsSyncRequest:
    """Input payload for ContactsMigrationAgent."""
    users: List[ZohoUser]
    zoho_client: ZohoAdminClient
    google_client: GoogleWorkspaceAdminClient
    checkpoint_store: CheckpointStore
    dry_run: bool = False
    progress_callback: Optional[Callable[[str, int, int, str], None]] = None
    pause_controller: Optional[Any] = None


@dataclass
class ContactsSyncSummary:
    """Output payload from ContactsMigrationAgent."""
    synced: int = 0
    skipped: int = 0
    failed: int = 0
    user_results: Dict[str, Dict[str, int]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "synced": self.synced,
            "skipped": self.skipped,
            "failed": self.failed,
            "user_results": self.user_results
        }


class ContactsMigrationAgent(AtomicAgent[ContactsSyncRequest, ContactsSyncSummary]):
    """
    Atomic Agent responsible strictly for contact card synchronization
    and People API schema translation.
    """

    def __init__(self):
        super().__init__(
            name="ContactsMigrationAgent",
            description="Maps address books, phones, and emails to Google People API."
        )

    def execute(self, input_data: ContactsSyncRequest) -> ContactsSyncSummary:
        synced = 0
        skipped = 0
        failed = 0
        user_results = {}
        rate_limiter = TokenBucket(rate_per_second=5.0, capacity=10.0)

        for user in input_data.users:
            if input_data.pause_controller:
                input_data.pause_controller.wait_if_paused()

            u_synced = 0
            u_skipped = 0
            u_failed = 0

            try:
                acc_id = user.mailbox_account_id or user.zuid or user.email
                contacts = input_data.zoho_client.list_contacts(account_id=acc_id)
            except Exception as e:
                self.logger.error(f"Failed to fetch contacts for {user.email}: {e}")
                user_results[user.email] = {"synced": 0, "skipped": 0, "failed": 1}
                failed += 1
                continue

            total_user_contacts = len(contacts)
            for idx, contact in enumerate(contacts, 1):
                if input_data.pause_controller:
                    input_data.pause_controller.wait_if_paused()

                c_name = contact.display_name or f"{contact.first_name} {contact.last_name}".strip() or "Contact"
                if input_data.progress_callback:
                    input_data.progress_callback(user.email, idx, total_user_contacts, f"Contact: {c_name[:20]}")

                if input_data.checkpoint_store.is_item_synced("CONTACT", contact.contact_id, user.email):
                    u_skipped += 1
                    skipped += 1
                    continue

                if input_data.dry_run:
                    u_synced += 1
                    synced += 1
                    continue

                try:
                    rate_limiter.acquire()
                    resp = retry_with_backoff(
                        lambda: input_data.google_client.insert_contact(user.email, contact),
                        pause_controller=input_data.pause_controller
                    )
                    g_resource_name = resp.get("resourceName") if isinstance(resp, dict) else None
                    input_data.checkpoint_store.record_item_sync(
                        entity_type="CONTACT",
                        source_id=contact.contact_id,
                        user_email=user.email,
                        destination_id=g_resource_name,
                        status="SYNCED"
                    )
                    u_synced += 1
                    synced += 1
                except Exception as e:
                    u_failed += 1
                    failed += 1
                    error_msg = str(e)
                    self.logger.error(f"Failed syncing contact '{c_name}' for {user.email}: {error_msg}")
                    input_data.checkpoint_store.record_item_sync(
                        entity_type="CONTACT",
                        source_id=contact.contact_id,
                        user_email=user.email,
                        status="FAILED",
                        error_msg=error_msg
                    )

            user_results[user.email] = {"synced": u_synced, "skipped": u_skipped, "failed": u_failed}

        return ContactsSyncSummary(
            synced=synced,
            skipped=skipped,
            failed=failed,
            user_results=user_results
        )
