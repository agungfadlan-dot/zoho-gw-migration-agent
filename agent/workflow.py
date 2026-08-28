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
        target_users_str: Optional[str] = None,
        users_file: Optional[str] = None,
        pilot_count: Optional[int] = None,
    ):
        self.vault = vault
        self.checkpoint_db_path = checkpoint_db_path
        self.dry_run = dry_run
        self.target_users_str = target_users_str
        self.users_file = users_file
        self.pilot_count = pilot_count

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

    def select_target_users(self, all_users: List[Any], auto_confirm: bool = False) -> List[Any]:
        """Filters users based on CLI flags or interactive selection menu."""
        if not all_users:
            return []

        # 1. Specified via --users flag (comma-separated)
        if self.target_users_str:
            targets = set(u.strip().lower() for u in self.target_users_str.split(",") if u.strip())
            selected = [
                u for u in all_users
                if u.email.lower() in targets or any(a.lower() in targets for a in u.aliases)
            ]
            if not selected:
                print(f"{Colors.YELLOW}Warning: None of the emails in '{self.target_users_str}' matched discovered Zoho users.{Colors.RESET}")
                return all_users
            return selected

        # 2. Specified via --users-file
        if self.users_file:
            expanded = os.path.expanduser(self.users_file)
            if os.path.isfile(expanded):
                with open(expanded, "r", encoding="utf-8") as f:
                    targets = set(line.strip().lower() for line in f if line.strip() and not line.startswith("#"))
                selected = [
                    u for u in all_users
                    if u.email.lower() in targets or any(a.lower() in targets for a in u.aliases)
                ]
                if selected:
                    return selected
                print(f"{Colors.YELLOW}Warning: No users from file '{self.users_file}' matched discovered users.{Colors.RESET}")

        # 3. Specified via --pilot count
        if self.pilot_count and self.pilot_count > 0:
            return all_users[:self.pilot_count]

        # 4. Interactive Selection Menu (if not auto-confirm)
        if not auto_confirm and not self.dry_run and len(all_users) > 1:
            print(f"\n{Colors.BOLD}{Colors.CYAN}--- Target User Scope Selection ---{Colors.RESET}")
            print("Select migration scope:")
            print(f"  [1] Migrate ALL organization users ({len(all_users)} users) - Full Migration")
            print("  [2] Quick Pilot Test: Migrate first 1 user")
            print("  [3] Quick Pilot Test: Migrate first 5 users")
            print("  [4] Select specific users by Email / UPN (comma-separated)")
            print("  [5] Select users by list index numbers (e.g., 1, 3, 5)")

            choice = input("Enter choice [1-5] (default: 1 [All Users]): ").strip()
            if choice == "2":
                return all_users[:1]
            elif choice == "3":
                return all_users[:min(5, len(all_users))]
            elif choice == "4":
                raw_emails = input("Enter user emails (comma-separated): ").strip()
                targets = set(e.strip().lower() for e in raw_emails.split(",") if e.strip())
                matched = [u for u in all_users if u.email.lower() in targets or any(a.lower() in targets for a in u.aliases)]
                if matched:
                    return matched
                print(f"{Colors.YELLOW}No matching users found. Defaulting to all users.{Colors.RESET}")
            elif choice == "5":
                raw_idx = input(f"Enter user numbers between 1 and {len(all_users)} (e.g. 1, 2, 4): ").strip()
                indices = []
                for part in raw_idx.replace(",", " ").split():
                    if part.isdigit() and 1 <= int(part) <= len(all_users):
                        indices.append(int(part) - 1)
                if indices:
                    return [all_users[i] for i in sorted(set(indices))]

        return all_users

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

        headers = ["#", "Email", "Display Name", "Aliases", "Folders", "Est. Messages", "Est. Storage", "Events", "Contacts"]
        rows = []
        for idx, u in enumerate(report.user_assessments, 1):
            rows.append([
                str(idx),
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
        """Executes full or pilot migration across all 4 stages."""
        # Fetch full Zoho user objects
        all_zoho_users = self.zoho_client.list_organization_users()

        # Filter target users for pilot or full migration
        target_users = self.select_target_users(all_zoho_users, auto_confirm=auto_confirm)

        is_pilot = len(target_users) < len(all_zoho_users)
        if is_pilot:
            print(f"\n{Colors.CYAN}{Colors.BOLD}================================================================================{Colors.RESET}")
            print(f"{Colors.CYAN}{Colors.BOLD}                  PILOT MIGRATION MODE: {len(target_users)} USER(S) SELECTED                  {Colors.RESET}")
            print(f"{Colors.CYAN}{Colors.BOLD}================================================================================{Colors.RESET}")
            for idx, u in enumerate(target_users, 1):
                print(f"  {idx}. {Colors.BOLD}{u.email}{Colors.RESET} ({u.display_name})")
            print()
        else:
            print(f"\n{Colors.GREEN}{Colors.BOLD}>>> FULL ORGANIZATION MIGRATION MODE: All {len(target_users)} user(s) selected.{Colors.RESET}\n")

        if not auto_confirm and not self.dry_run:
            print(f"{Colors.BOLD}{Colors.YELLOW}IMPORTANT: You are about to initiate LIVE migration for {len(target_users)} user(s) to Google Workspace.{Colors.RESET}")
            confirm = input("Are you sure you want to proceed? [y/N]: ").strip().lower()
            if confirm not in ("y", "yes"):
                print(f"{Colors.RED}Migration aborted by Administrator.{Colors.RESET}")
                return

        # Stage 1: User Provisioning
        print_stage_header(3, "Stage 1: Google Workspace User Provisioning", "Creating accounts with secure temporary passwords & aliases.")
        provision_results = self.pipeline.run_user_provisioning(target_users)

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
        cal_summary = self.pipeline.run_calendar_migration(target_users)

        # Stage 3: Contacts Migration
        print_stage_header(5, "Stage 3: Google Contacts Migration", "Importing address books, emails, and phone numbers via People API.")
        cont_summary = self.pipeline.run_contacts_migration(target_users)

        # Stage 4: Mailbox Streaming Migration
        print_stage_header(6, "Stage 4: Mailbox & Messages Streaming", "Direct memory-buffered streaming of folders & RFC822 messages to Gmail.")
        mail_summary = self.pipeline.run_mailbox_migration(target_users)

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
