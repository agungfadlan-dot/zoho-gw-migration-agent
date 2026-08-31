from __future__ import annotations

"""
Organization Discovery & Pre-Migration Assessment Engine.

Discovers Zoho users, aliases, mailbox volume, folders, calendar events,
and contacts to prepare migration plan and verify Google Workspace readiness.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, TYPE_CHECKING
if TYPE_CHECKING:
    from connectors.zoho_client import ZohoAdminClient
from connectors.base import ZohoUser
from security.sanitizer import setup_secure_logger

logger = setup_secure_logger("discovery_engine")


@dataclass
class UserAssessment:
    """Assessment details for a single user."""
    zuid: str
    email: str
    display_name: str
    aliases: List[str]
    folder_count: int = 0
    estimated_messages: int = 0
    estimated_storage_mb: float = 0.0
    calendar_events_count: int = 0
    contacts_count: int = 0


@dataclass
class OrganizationAssessmentReport:
    """Comprehensive discovery assessment report."""
    org_name: str
    org_id: str
    domain: str
    total_users: int
    active_users: int
    total_estimated_messages: int
    total_estimated_storage_mb: float
    user_assessments: List[UserAssessment] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "org_name": self.org_name,
            "org_id": self.org_id,
            "domain": self.domain,
            "total_users": self.total_users,
            "active_users": self.active_users,
            "total_estimated_messages": self.total_estimated_messages,
            "total_estimated_storage_mb": round(self.total_estimated_storage_mb, 2),
            "users": [
                {
                    "email": u.email,
                    "display_name": u.display_name,
                    "aliases": u.aliases,
                    "folder_count": u.folder_count,
                    "estimated_messages": u.estimated_messages,
                    "estimated_storage_mb": round(u.estimated_storage_mb, 2),
                    "calendar_events_count": u.calendar_events_count,
                    "contacts_count": u.contacts_count,
                }
                for u in self.user_assessments
            ],
        }


class DiscoveryEngine:
    """Performs non-destructive discovery and volume estimation."""

    def __init__(self, zoho_client: ZohoAdminClient):
        self.zoho_client = zoho_client

    def run_assessment(self, sample_items: bool = True) -> OrganizationAssessmentReport:
        """Runs full organization discovery."""
        logger.info("Starting organization discovery on Zoho...")

        conn_info = self.zoho_client.test_connection()
        zoho_users = self.zoho_client.list_organization_users()

        user_assessments: List[UserAssessment] = []
        total_msgs = 0
        total_storage_bytes = 0

        for idx, u in enumerate(zoho_users):
            mb_used = u.storage_used_bytes / (1024 * 1024)
            total_storage_bytes += u.storage_used_bytes

            assessment = UserAssessment(
                zuid=u.zuid,
                email=u.email,
                display_name=u.display_name,
                aliases=u.aliases,
                estimated_storage_mb=mb_used,
            )

            # Sample folders for users (first 25 or all if small org)
            should_sample = sample_items and (idx < 25 or len(zoho_users) <= 30)
            if should_sample and u.mailbox_account_id:
                try:
                    folders = self.zoho_client.list_user_folders(u.mailbox_account_id)
                    assessment.folder_count = len(folders)
                    user_msg_count = sum(f.message_count for f in folders)

                    # If Zoho returned 0 in folder metadata, sample the first few folders directly
                    if user_msg_count == 0 and folders:
                        for f in folders[:3]:
                            try:
                                sample_msgs = self.zoho_client.list_folder_messages(
                                    account_id=u.mailbox_account_id,
                                    folder_id=f.folder_id,
                                    start=1,
                                    limit=20
                                )
                                if sample_msgs:
                                    f.message_count = max(len(sample_msgs), f.message_count)
                                    user_msg_count += len(sample_msgs)
                            except Exception:
                                pass

                    assessment.estimated_messages = user_msg_count
                    total_msgs += user_msg_count

                    if assessment.estimated_storage_mb == 0 and user_msg_count > 0:
                        est_bytes = user_msg_count * 100 * 1024  # ~100KB per message
                        assessment.estimated_storage_mb = est_bytes / (1024 * 1024)
                        total_storage_bytes += est_bytes
                except Exception as e:
                    logger.warning(f"Could not inspect folders for {u.email}: {e}")

                try:
                    cal_events = self.zoho_client.list_calendar_events(u.mailbox_account_id, u.email)
                    assessment.calendar_events_count = len(cal_events)
                except Exception:
                    pass

                try:
                    contacts = self.zoho_client.list_contacts(u.mailbox_account_id)
                    assessment.contacts_count = len(contacts)
                except Exception:
                    pass
            elif u.storage_used_bytes > 0:
                est_msgs = max(1, int(u.storage_used_bytes / (100 * 1024)))
                assessment.estimated_messages = est_msgs
                total_msgs += est_msgs

            user_assessments.append(assessment)

        # Extrapolate for unsampled active users if direct API didn't report per-user storage
        sampled_assessments = [a for a in user_assessments if a.estimated_messages > 0 or a.estimated_storage_mb > 0]
        if sampled_assessments and len(sampled_assessments) < len(zoho_users):
            avg_msgs = sum(a.estimated_messages for a in sampled_assessments) / len(sampled_assessments)
            avg_storage_mb = sum(a.estimated_storage_mb for a in sampled_assessments) / len(sampled_assessments)

            for a in user_assessments:
                if a.estimated_messages == 0 and a.estimated_storage_mb == 0:
                    a.estimated_messages = int(avg_msgs)
                    a.estimated_storage_mb = round(avg_storage_mb, 2)
                    total_msgs += int(avg_msgs)
                    total_storage_bytes += int(avg_storage_mb * 1024 * 1024)

        active_count = sum(1 for u in zoho_users if u.is_active)
        total_storage_mb = total_storage_bytes / (1024 * 1024)

        report = OrganizationAssessmentReport(
            org_name=conn_info.get("org_name", "Zoho Organization"),
            org_id=conn_info.get("org_id", ""),
            domain=conn_info.get("domain", self.zoho_client.domain),
            total_users=len(zoho_users),
            active_users=active_count,
            total_estimated_messages=total_msgs,
            total_estimated_storage_mb=total_storage_mb,
            user_assessments=user_assessments,
        )

        logger.info(
            f"Discovery completed: {len(zoho_users)} users, ~{total_msgs} messages, "
            f"~{round(total_storage_mb, 2)} MB data."
        )
        return report
