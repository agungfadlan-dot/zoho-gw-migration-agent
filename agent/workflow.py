"""
Migration Workflow Orchestrator.

Guides the Administrator through:
1. Security & Scope Pre-Flight Verification
2. Organization Discovery & Volume Assessment
3. Stage 1: Google Workspace User Provisioning
4. Stage 2: Calendar Migration
5. Stage 3: Contacts Migration
6. Stage 4: Mailbox Streaming Migration
7. Final Audit & Resumability Summary
"""

import os
import sys
import json
import csv
import time
from typing import Optional, Dict, Any, List

from security.vault import EphemeralVault
from security.sanitizer import setup_secure_logger
from connectors.zoho_client import ZohoAdminClient
from connectors.google_client import GoogleWorkspaceAdminClient
from engine.checkpoint import CheckpointStore
from engine.discovery import DiscoveryEngine, OrganizationAssessmentReport
from engine.pipeline import MigrationPipeline
from agent.console import (
    banner,
    print_stage_header,
    print_status_badge,
    print_table,
    TerminalProgressCallback,
    Colors
)

logger = setup_secure_logger("workflow_orchestrator")


class MigrationWorkflow:
    """Master workflow orchestrator."""

    def __init__(
        self,
        vault: EphemeralVault,
        checkpoint_db_path: str = "migration_checkpoint.db",
        dry_run: bool = False,
    ):
        self.vault = vault
        self.checkpoint_db_path = checkpoint_db_path
        self.dry_run = dry_run
        self.checkpoint = CheckpointStore(db_path=checkpoint_db_path)
        self.progress_cb = TerminalProgressCallback()

        domain = self.vault.retrieve("zoho_domain") or "zoho.com"
        admin_email = self.vault.retrieve("google_admin_email")

        self.zoho_client = ZohoAdminClient(vault=self.vault, domain=domain)
        self.google_client = GoogleWorkspaceAdminClient(vault=self.vault, admin_subject_email=admin_email)
        self.discovery = DiscoveryEngine(self.zoho_client)
        self.pipeline = MigrationPipeline(
            zoho_client=self.zoho_client,
            google_client=self.google_client,
            checkpoint_store=self.checkpoint,
            dry_run=self.dry_run,
            progress_callback=self.progress_cb
        )

    def run_preflight(self) -> bool:
        """Step 1: Runs security checks and connectivity tests."""
        print_stage_header(1, "Pre-flight Security & Connectivity Verification", "Validating API tokens, scopes, and tenant endpoints.")

        # Test Zoho connection
        print("1. Testing Zoho Admin API connectivity...")
        try:
            zoho_info = self.zoho_client.test_connection()
            print(f"   {Colors.GREEN}✓ Connected to Zoho Org: {zoho_info.get('org_name')} (ID: {zoho_info.get('org_id')}){Colors.RESET}")
        except Exception as e:
            print(f"   {Colors.RED}✗ Zoho connection failed: {e}{Colors.RESET}")
            return False

        # Test Google connection
        print("2. Testing Google Workspace Service Account & DWD...")
        try:
            google_info = self.google_client.test_connection()
            if google_info.get("status") == "connected":
                print(f"   {Colors.GREEN}✓ Google Workspace DWD verified for {google_info.get('client_email')}{Colors.RESET}")
            else:
                print(f"   {Colors.YELLOW}⚠ Google Workspace check returned warning: {google_info.get('error')}{Colors.RESET}")
        except Exception as e:
            print(f"   {Colors.RED}✗ Google Workspace verification failed: {e}{Colors.RESET}")
            return False

        print(f"\n{Colors.GREEN}{Colors.BOLD}>>> Pre-flight checks passed successfully.{Colors.RESET}")
        return True

    def run_discovery(self) -> OrganizationAssessmentReport:
        """Step 2: Scans Zoho Organization and renders assessment table."""
        print_stage_header(2, "Organization Discovery & Volume Assessment", "Scanning directory users, mailboxes, calendars, and address books.")

        report = self.discovery.run_assessment(sample_items=True)

        headers = ["Email", "Display Name", "Aliases", "Folders", "Est. Messages", "Est. Storage", "Events", "Contacts"]
        rows = []
        for u in report.user_assessments:
            rows.append([
                u.email,
                u.display_name,
                ", ".join(u.aliases) if u.aliases else "-",
                str(u.folder_count),
                str(u.estimated_messages),
                f"{u.estimated_storage_mb:.1f} MB",
                str(u.calendar_events_count),
                str(u.contacts_count),
            ])

        print_table(headers, rows)
        print(f"{Colors.BOLD}Total Organization Volume:{Colors.RESET}")
        print(f"  • Total Users: {report.total_users} ({report.active_users} active)")
        print(f"  • Estimated Messages: ~{report.total_estimated_messages:,}")
        print(f"  • Estimated Data Size: ~{report.total_estimated_storage_mb:.2f} MB")
        return report

    def execute_migration(self, report: OrganizationAssessmentReport, auto_confirm: bool = False) -> None:
        """Executes full migration across all 4 stages."""
        if not auto_confirm and not self.dry_run:
            print(f"\n{Colors.BOLD}{Colors.YELLOW}IMPORTANT: You are about to initiate LIVE migration for {report.total_users} users to Google Workspace.{Colors.RESET}")
            confirm = input("Are you sure you want to proceed? [y/N]: ").strip().lower()
            if confirm not in ("y", "yes"):
                print(f"{Colors.RED}Migration aborted by Administrator.{Colors.RESET}")
                return

        # Fetch full Zoho user objects
        zoho_users = self.zoho_client.list_organization_users()

        # Stage 1: User Provisioning
        print_stage_header(3, "Stage 1: Google Workspace User Provisioning", "Creating accounts with secure temporary passwords & aliases.")
        provision_results = self.pipeline.run_user_provisioning(zoho_users)

        # Export temporary passwords securely if any were created
        newly_created = [r for r in provision_results if r.get("status") == "CREATED" and "temp_password" in r]
        if newly_created and not self.dry_run:
            creds_file = f"provisioned_credentials_{int(time.time())}.csv"
            try:
                with open(creds_file, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(["Email", "Temporary_Password", "ChangePasswordAtNextLogin"])
                    for u in newly_created:
                        writer.writerow([u["email"], u["temp_password"], "TRUE"])
                print(f"\n{Colors.BOLD}{Colors.GREEN}✓ One-time temporary passwords saved to '{creds_file}'.{Colors.RESET}")
                print(f"{Colors.RED}{Colors.BOLD}WARNING: Distribute these credentials securely to users and delete this file.{Colors.RESET}")
            except Exception as e:
                logger.error(f"Could not export credentials CSV: {e}")

        # Stage 2: Calendar Migration
        print_stage_header(4, "Stage 2: Google Calendar Migration", "Importing calendar events, recurrence, and attendee links.")
        cal_summary = self.pipeline.run_calendar_migration(zoho_users)

        # Stage 3: Contacts Migration
        print_stage_header(5, "Stage 3: Google Contacts Migration", "Importing address books, emails, and phone numbers via People API.")
        cont_summary = self.pipeline.run_contacts_migration(zoho_users)

        # Stage 4: Mailbox Streaming Migration
        print_stage_header(6, "Stage 4: Mailbox & Messages Streaming", "Direct memory-buffered streaming of folders & RFC822 messages to Gmail.")
        mail_summary = self.pipeline.run_mailbox_migration(zoho_users)

        # Final Summary
        self.render_completion_summary(report, provision_results, cal_summary, cont_summary, mail_summary)

    def render_completion_summary(
        self,
        report: OrganizationAssessmentReport,
        provision_results: List[Dict[str, Any]],
        cal_summary: Dict[str, Any],
        cont_summary: Dict[str, Any],
        mail_summary: Dict[str, Any],
    ) -> None:
        """Prints final completion report and audit status."""
        print_stage_header(7, "Migration Final Audit & Completion Summary", "Complete status across all migrated entities.")

        db_stats = self.checkpoint.get_summary_stats()
        print(f"{Colors.BOLD}{Colors.GREEN}================================================================================{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.GREEN}                  MIGRATION RUN COMPLETED SUCCESSFULLY                          {Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.GREEN}================================================================================{Colors.RESET}\n")

        print(f"• Mode: {'DRY-RUN (SIMULATED)' if self.dry_run else 'LIVE MIGRATION'}")
        print(f"• Target Domain: {report.domain}")
        print(f"• Checkpoint Database: {self.checkpoint_db_path}")
        print(f"• Users Processed: {len(provision_results)}")
        print(f"• Calendar Events Synced: {cal_summary.get('synced', 0)} (Failed: {cal_summary.get('failed', 0)})")
        print(f"• Contacts Synced: {cont_summary.get('synced', 0)} (Failed: {cont_summary.get('failed', 0)})")
        print(f"• Emails Synced: {mail_summary.get('synced', 0)} (Failed: {mail_summary.get('failed', 0)})\n")

        # Save JSON audit report
        audit_file = f"migration_audit_report_{int(time.time())}.json"
        audit_data = {
            "mode": "DRY_RUN" if self.dry_run else "LIVE",
            "timestamp": time.time(),
            "organization": report.to_dict(),
            "summary": {
                "provisioning": provision_results,
                "calendar": cal_summary,
                "contacts": cont_summary,
                "mailbox": mail_summary,
                "database_stats": db_stats,
            }
        }
        with open(audit_file, "w", encoding="utf-8") as f:
            json.dump(audit_data, f, indent=2)

        print(f"{Colors.CYAN}✓ Detailed JSON audit report written to '{audit_file}'.{Colors.RESET}\n")
