"""
Migration Workflow Orchestrator.
Coordinates specialized Atomic Agents via MigrationSupervisor.
"""

import os
import sys
import json
import time
from typing import Optional, Dict, Any, List

from security.vault import EphemeralVault
from security.sanitizer import setup_secure_logger
from engine.discovery import OrganizationAssessmentReport
from atomic_agents.supervisor import MigrationSupervisor
from engine.pause_controller import PauseController, PauseState
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
    """
    Master workflow orchestrator.
    Delegates pre-flight, discovery, provisioning, and synchronization to Atomic Agents.
    """

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

        self.progress_cb = TerminalProgressCallback()

        def on_cli_state_change(state: PauseState, reason: str):
            if state == PauseState.PAUSED_NETWORK_LOST:
                print(f"\n{Colors.YELLOW}{Colors.BOLD}⚠️  NETWORK CONNECTION LOST!{Colors.RESET}")
                print(f"{Colors.YELLOW}Migration paused automatically. Watchdog is monitoring connection and will auto-resume once restored...{Colors.RESET}")
            elif state == PauseState.PAUSED_MANUAL:
                print(f"\n{Colors.YELLOW}⏸️  Migration paused: {reason}{Colors.RESET}")
            elif state == PauseState.RUNNING:
                print(f"\n{Colors.GREEN}▶️  Connection restored / Migration resumed.{Colors.RESET}")

        self.pause_controller = PauseController(on_state_change=on_cli_state_change)

        # Initialize the Atomic Migration Supervisor
        self.supervisor = MigrationSupervisor(
            vault=self.vault,
            checkpoint_db=self.checkpoint_db_path,
            dry_run=self.dry_run,
            pause_controller=self.pause_controller
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
                print(f"{Colors.YELLOW}Warning: No valid user emails found in '{self.users_file}'.{Colors.RESET}")

        # 3. Specified via --pilot flag (e.g. --pilot 5)
        if self.pilot_count and self.pilot_count > 0:
            return all_users[:self.pilot_count]

        # 4. Interactive selection menu if running in interactive terminal
        if not auto_confirm:
            print(f"\n{Colors.CYAN}{Colors.BOLD}--- Target User Scope Selection ---{Colors.RESET}")
            print(f"Discovered {len(all_users)} total Zoho organization user(s).")
            print("Select migration scope:")
            print(f"  [1] Migrate ALL organization users ({len(all_users)} users) - Full Migration")
            print(f"  [2] Quick Pilot Test: Migrate first 1 user")
            print(f"  [3] Quick Pilot Test: Migrate first {min(5, len(all_users))} users")
            print(f"  [4] Select specific users by Email / UPN (comma-separated)")
            print(f"  [5] Select users by list index numbers (e.g., 1, 3, 5)")

            choice = input(f"{Colors.BOLD}Enter choice [1-5] (Default: 1): {Colors.RESET}").strip()
            if choice == "2":
                return all_users[:1]
            elif choice == "3":
                return all_users[:min(5, len(all_users))]
            elif choice == "4":
                raw_emails = input("Enter email address(es) to migrate (comma-separated): ").strip()
                if raw_emails:
                    targets = set(e.strip().lower() for e in raw_emails.split(",") if e.strip())
                    selected = [u for u in all_users if u.email.lower() in targets or any(a.lower() in targets for a in u.aliases)]
                    if selected:
                        return selected
                    print(f"{Colors.YELLOW}No matching users found. Defaulting to all users.{Colors.RESET}")
            elif choice == "5":
                raw_indices = input("Enter user index numbers (e.g. 1, 3, 5): ").strip()
                if raw_indices:
                    indices = []
                    for part in raw_indices.replace(" ", "").split(","):
                        if part.isdigit():
                            idx = int(part) - 1
                            if 0 <= idx < len(all_users):
                                indices.append(idx)
                    if indices:
                        return [all_users[i] for i in sorted(set(indices))]

        return all_users

    def run_preflight(self) -> bool:
        """Step 1: Executes Atomic Agent 1 (SecurityAuditorAgent)."""
        print_stage_header(1, "Pre-flight Security & Connectivity Verification", "Validating API tokens, scopes, and tenant endpoints via SecurityAuditorAgent.")

        domain = self.vault.retrieve("zoho_domain") or "zoho.com"
        try:
            report = self.supervisor.run_security_audit(zoho_domain=domain)
            for check in report.checks_passed:
                print(f"   {Colors.GREEN}✓ {check}{Colors.RESET}")
            for warning in report.warnings:
                print(f"   {Colors.YELLOW}⚠ {warning}{Colors.RESET}")
            print(f"\n{Colors.GREEN}{Colors.BOLD}>>> Pre-flight security & scope checks passed successfully.{Colors.RESET}")
            return True
        except Exception as e:
            print(f"   {Colors.RED}✗ Pre-flight security verification failed: {e}{Colors.RESET}")
            return False

    def run_discovery(self) -> OrganizationAssessmentReport:
        """Step 2: Executes Atomic Agent 2 (DiscoveryAssessmentAgent)."""
        print_stage_header(2, "Organization Discovery & Volume Assessment", "Scanning directory topology, estimating storage, and analyzing pilot candidates.")

        domain = self.vault.retrieve("zoho_domain") or "zoho.com"
        discovery_result = self.supervisor.run_discovery(zoho_domain=domain, sample_items=True)
        report = discovery_result.report

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

        if discovery_result.recommended_pilot_cohort:
            pilot_emails = [u.email for u in discovery_result.recommended_pilot_cohort]
            print(f"\n{Colors.CYAN}{Colors.BOLD}💡 AI Recommended Pilot Cohort (Lowest Risk): {', '.join(pilot_emails)}{Colors.RESET}")

        return report

    def execute_migration(self, report: OrganizationAssessmentReport, auto_confirm: bool = False) -> None:
        """Executes full or pilot migration across Atomic Agents 3, 4, 5, and 6."""
        domain = self.vault.retrieve("zoho_domain") or "zoho.com"
        zoho_client = self.supervisor.get_zoho_client(domain=domain)
        all_zoho_users = zoho_client.list_organization_users()

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

        # Stage 1: User Provisioning (Atomic Agent 3)
        print_stage_header(3, "Stage 1: Google Workspace User Provisioning", "Executing UserProvisioningAgent.")
        prov_summary = self.supervisor.run_stage_provisioning(target_users)
        provision_results = prov_summary.results
        if prov_summary.credentials_csv_path:
            print(f"\n{Colors.BOLD}{Colors.GREEN}✓ One-time temporary passwords saved to '{prov_summary.credentials_csv_path}'.{Colors.RESET}")
            print(f"{Colors.RED}{Colors.BOLD}WARNING: Distribute these credentials securely to users and delete this file.{Colors.RESET}")

        # Stage 2: Calendar Migration (Atomic Agent 4)
        print_stage_header(4, "Stage 2: Google Calendar Migration", "Executing CalendarMigrationAgent.")
        cal_summary_obj = self.supervisor.run_stage_calendar(target_users, zoho_domain=domain, progress_callback=self.progress_cb)
        cal_summary = cal_summary_obj.to_dict()

        # Stage 3: Contacts Migration (Atomic Agent 5)
        print_stage_header(5, "Stage 3: Google Contacts Migration", "Executing ContactsMigrationAgent.")
        cont_summary_obj = self.supervisor.run_stage_contacts(target_users, zoho_domain=domain, progress_callback=self.progress_cb)
        cont_summary = cont_summary_obj.to_dict()

        # Stage 4: Mailbox Streaming Migration (Atomic Agent 6)
        print_stage_header(6, "Stage 4: Mailbox & Messages Streaming", "Executing MailboxStreamingAgent.")
        mail_summary_obj = self.supervisor.run_stage_mailbox(target_users, zoho_domain=domain, progress_callback=self.progress_cb)
        mail_summary = mail_summary_obj.to_dict()

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
        print_stage_header(7, "Migration Final Audit & Completion Summary", "Complete status across all atomic agents.")

        db_stats = self.supervisor.checkpoint_store.get_summary_stats()
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
