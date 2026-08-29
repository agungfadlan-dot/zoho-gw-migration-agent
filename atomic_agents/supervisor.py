"""
Migration Supervisor Orchestrator.
Coordinates specialized Atomic Agents to execute pre-flight checks, discovery, and staged migration.
"""

from typing import List, Dict, Any, Optional, Callable
import time
import json
from security.vault import EphemeralVault
from connectors.zoho_client import ZohoAdminClient
from connectors.google_client import GoogleWorkspaceAdminClient
from connectors.base import ZohoUser
from engine.checkpoint import CheckpointStore
from engine.pause_controller import PauseController, PauseState
from security.sanitizer import setup_secure_logger

from atomic_agents.security_auditor_agent import SecurityAuditorAgent, AuditRequest, AuditReport
from atomic_agents.discovery_agent import DiscoveryAssessmentAgent, DiscoveryRequest, DiscoveryResult
from atomic_agents.provisioning_agent import UserProvisioningAgent, ProvisioningRequest, ProvisioningSummary
from atomic_agents.calendar_agent import CalendarMigrationAgent, CalendarSyncRequest, CalendarSyncSummary
from atomic_agents.contacts_agent import ContactsMigrationAgent, ContactsSyncRequest, ContactsSyncSummary
from atomic_agents.mailbox_agent import MailboxStreamingAgent, MailboxStreamingRequest, MailboxStreamingSummary

logger = setup_secure_logger("migration_supervisor")


class MigrationSupervisor:
    """
    Master Supervisor orchestrating the 6 Atomic Migration Agents.
    Provides execution logging, consolidated error reporting, pause/resume coordination, and state management.
    """

    def __init__(
        self,
        vault: EphemeralVault,
        checkpoint_db: str = "migration_checkpoint.db",
        dry_run: bool = False,
        pause_controller: Optional[PauseController] = None
    ):
        self.vault = vault
        self.checkpoint_db = checkpoint_db
        self.dry_run = dry_run
        self.checkpoint_store = CheckpointStore(db_path=checkpoint_db)
        self.pause_controller = pause_controller or PauseController()

        # Initialize the 6 Atomic Agents
        self.auditor_agent = SecurityAuditorAgent()
        self.discovery_agent = DiscoveryAssessmentAgent()
        self.provisioning_agent = UserProvisioningAgent()
        self.calendar_agent = CalendarMigrationAgent()
        self.contacts_agent = ContactsMigrationAgent()
        self.mailbox_agent = MailboxStreamingAgent()

        # Clients initialized on demand
        self._zoho_client: Optional[ZohoAdminClient] = None
        self._google_client: Optional[GoogleWorkspaceAdminClient] = None

    def pause(self, reason: str = "Manual pause by administrator") -> None:
        """Pauses migration safely."""
        self.pause_controller.pause(reason)

    def resume(self) -> None:
        """Resumes migration."""
        self.pause_controller.resume()

    def cancel(self, reason: str = "Migration cancelled by user") -> None:
        """Cancels migration."""
        self.pause_controller.cancel(reason)

    @property
    def is_paused(self) -> bool:
        return self.pause_controller.is_paused

    @property
    def pause_state(self) -> str:
        return self.pause_controller.state.value

    def get_zoho_client(self, domain: str = "zoho.com") -> ZohoAdminClient:
        if not self._zoho_client:
            self._zoho_client = ZohoAdminClient(vault=self.vault, domain=domain)
        return self._zoho_client

    def get_google_client(self, admin_email: Optional[str] = None) -> GoogleWorkspaceAdminClient:
        if not self._google_client:
            adm = admin_email or self.vault.retrieve("google_admin_email")
            self._google_client = GoogleWorkspaceAdminClient(vault=self.vault, admin_subject_email=adm)
        return self._google_client

    def run_security_audit(
        self,
        zoho_domain: str = "zoho.com",
        zoho_scopes: Optional[List[str]] = None,
        google_admin_email: Optional[str] = None
    ) -> AuditReport:
        """Executes Atomic Agent 1: Scope & Security Audit."""
        req = AuditRequest(
            vault=self.vault,
            zoho_domain=zoho_domain,
            zoho_scopes=zoho_scopes or [],
            google_admin_email=google_admin_email
        )
        res = self.auditor_agent.run(req)
        if not res.success or not res.data:
            raise RuntimeError(f"Security audit failed: {res.error}")
        return res.data

    def run_discovery(
        self,
        zoho_domain: str = "zoho.com",
        sample_items: bool = True,
        pilot_candidates_count: int = 5
    ) -> DiscoveryResult:
        """Executes Atomic Agent 2: Tenant Discovery & Assessment."""
        client = self.get_zoho_client(domain=zoho_domain)
        req = DiscoveryRequest(
            zoho_client=client,
            sample_items=sample_items,
            pilot_candidate_count=pilot_candidates_count
        )
        res = self.discovery_agent.run(req)
        if not res.success or not res.data:
            raise RuntimeError(f"Discovery failed: {res.error}")
        return res.data

    def run_stage_provisioning(self, target_users: List[ZohoUser]) -> ProvisioningSummary:
        """Executes Atomic Agent 3: User Provisioning."""
        google_client = self.get_google_client()
        req = ProvisioningRequest(
            users=target_users,
            google_client=google_client,
            checkpoint_store=self.checkpoint_store,
            dry_run=self.dry_run,
            pause_controller=self.pause_controller
        )
        res = self.provisioning_agent.run(req)
        if not res.success or not res.data:
            raise RuntimeError(f"Provisioning failed: {res.error}")
        return res.data

    def run_stage_calendar(
        self,
        target_users: List[ZohoUser],
        zoho_domain: str = "zoho.com",
        progress_callback: Optional[Callable[[str, int, int, str], None]] = None
    ) -> CalendarSyncSummary:
        """Executes Atomic Agent 4: Calendar Migration."""
        zoho_client = self.get_zoho_client(domain=zoho_domain)
        google_client = self.get_google_client()
        req = CalendarSyncRequest(
            users=target_users,
            zoho_client=zoho_client,
            google_client=google_client,
            checkpoint_store=self.checkpoint_store,
            dry_run=self.dry_run,
            progress_callback=progress_callback,
            pause_controller=self.pause_controller
        )
        res = self.calendar_agent.run(req)
        if not res.success or not res.data:
            raise RuntimeError(f"Calendar migration failed: {res.error}")
        return res.data

    def run_stage_contacts(
        self,
        target_users: List[ZohoUser],
        zoho_domain: str = "zoho.com",
        progress_callback: Optional[Callable[[str, int, int, str], None]] = None
    ) -> ContactsSyncSummary:
        """Executes Atomic Agent 5: Contacts Migration."""
        zoho_client = self.get_zoho_client(domain=zoho_domain)
        google_client = self.get_google_client()
        req = ContactsSyncRequest(
            users=target_users,
            zoho_client=zoho_client,
            google_client=google_client,
            checkpoint_store=self.checkpoint_store,
            dry_run=self.dry_run,
            progress_callback=progress_callback,
            pause_controller=self.pause_controller
        )
        res = self.contacts_agent.run(req)
        if not res.success or not res.data:
            raise RuntimeError(f"Contacts migration failed: {res.error}")
        return res.data

    def run_stage_mailbox(
        self,
        target_users: List[ZohoUser],
        zoho_domain: str = "zoho.com",
        progress_callback: Optional[Callable[[str, int, int, str], None]] = None
    ) -> MailboxStreamingSummary:
        """Executes Atomic Agent 6: Mailbox Streaming."""
        zoho_client = self.get_zoho_client(domain=zoho_domain)
        google_client = self.get_google_client()
        req = MailboxStreamingRequest(
            users=target_users,
            zoho_client=zoho_client,
            google_client=google_client,
            checkpoint_store=self.checkpoint_store,
            dry_run=self.dry_run,
            progress_callback=progress_callback,
            pause_controller=self.pause_controller
        )
        res = self.mailbox_agent.run(req)
        if not res.success or not res.data:
            raise RuntimeError(f"Mailbox streaming failed: {res.error}")
        return res.data
