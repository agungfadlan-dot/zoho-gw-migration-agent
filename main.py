#!/usr/bin/env python3
"""
Zoho to Google Workspace Migration Agent - Main CLI Entrypoint.

Usage:
  python3 main.py [--ui] [--dry-run] [--import-zip PATH --user EMAIL] [--import-dir PATH]
"""

import sys
import os
import argparse
from typing import Optional

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from security.vault import EphemeralVault
from security.sanitizer import setup_secure_logger
from agent.console import banner, Colors
from agent.interactive import collect_zoho_credentials, collect_google_credentials
from agent.workflow import MigrationWorkflow
from connectors.google_client import GoogleWorkspaceAdminClient
from engine.checkpoint import CheckpointStore
from engine.bulk_importer import ZohoBulkZipImporter
from ui.server import run_ui_server

logger = setup_secure_logger("main")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Zoho to Google Workspace Migration Agent (Passwordless Admin-to-Admin Architecture)"
    )
    parser.add_argument(
        "--ui", "--gui", "--web",
        action="store_true",
        help="Launch the local Web UI in your browser for guided visual migration"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Local port for Web UI server (default: 8080)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate the migration without writing data to Google Workspace"
    )
    parser.add_argument(
        "--checkpoint-db",
        type=str,
        default="migration_checkpoint.db",
        help="Path to SQLite checkpoint database (default: migration_checkpoint.db)"
    )
    parser.add_argument(
        "--import-zip",
        type=str,
        default=None,
        help="Directly import a single Zoho export ZIP archive into Google Workspace without local extraction"
    )
    parser.add_argument(
        "--import-dir",
        type=str,
        default=None,
        help="Directly import an entire folder of Zoho export ZIP archives into Google Workspace in parallel"
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Number of concurrent worker threads for bulk import (default: 5)"
    )
    parser.add_argument(
        "--user",
        type=str,
        default=None,
        help="Target user email address for --import-zip"
    )
    parser.add_argument(
        "--users",
        type=str,
        default=None,
        help="Comma-separated list of specific user emails/UPNs to migrate (e.g. --users user1@domain.com,user2@domain.com)"
    )
    parser.add_argument(
        "--users-file",
        type=str,
        default=None,
        help="Path to a text file containing target user emails (one per line)"
    )
    parser.add_argument(
        "--pilot",
        type=int,
        default=None,
        help="Limit migration to the first N discovered users for pilot testing (e.g. --pilot 5)"
    )
    parser.add_argument(
        "--skip-calendar",
        action="store_true",
        help="Skip Zoho Calendar event synchronization"
    )
    parser.add_argument(
        "--skip-contacts",
        action="store_true",
        help="Skip Zoho Address Book contact synchronization"
    )
    parser.add_argument(
        "--mail-only",
        action="store_true",
        help="Only migrate mailboxes (equivalent to --skip-calendar --skip-contacts)"
    )
    parser.add_argument(
        "--vault-ttl",
        type=int,
        default=86400,
        help="In-memory credential vault TTL in seconds (default: 86400 / 24 hours)"
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Automatically confirm and proceed with migration without interactive prompts"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.ui:
        run_ui_server(port=args.port, open_browser=True)
        return

    banner()

    if args.dry_run:
        print(f"{Colors.YELLOW}{Colors.BOLD}>>> RUNNING IN DRY-RUN MODE (No changes will be made to Google Workspace) <<<{Colors.RESET}\n")

    # Initialize in-memory security vault with configurable TTL (default: 24h)
    with EphemeralVault(ttl_seconds=args.vault_ttl) as vault:
        try:
            # Handle Bulk ZIP / Export Directory Importer Flow directly
            if args.import_zip or args.import_dir:
                print(f"{Colors.CYAN}{Colors.BOLD}=== Zoho Bulk Export Importer Pipeline ==={Colors.RESET}\n")
                collect_google_credentials(vault)

                google_client = GoogleWorkspaceAdminClient(vault=vault)
                checkpoint_store = CheckpointStore(db_path=args.checkpoint_db)

                importer = ZohoBulkZipImporter(
                    google_client=google_client,
                    checkpoint_store=checkpoint_store,
                    max_workers=args.concurrency,
                    dry_run=args.dry_run,
                )

                if args.import_zip:
                    target_email = args.user
                    if not target_email:
                        target_email = input(f"{Colors.CYAN}Enter target Google Workspace user email for this ZIP: {Colors.RESET}").strip()
                    if not target_email:
                        print(f"{Colors.RED}Target user email is required for --import-zip.{Colors.RESET}")
                        sys.exit(1)

                    print(f"\n{Colors.GREEN}Streaming archive '{args.import_zip}' into {target_email}...{Colors.RESET}")
                    res = importer.import_user_zip(args.import_zip, target_email)
                    print(f"\n{Colors.GREEN}{Colors.BOLD}Import Completed for {target_email}:{Colors.RESET}")
                    print(f"  • Synced: {res['synced']} messages")
                    print(f"  • Skipped (Already Synced): {res['skipped']} messages")
                    print(f"  • Failed: {res['failed']} messages")
                    print(f"  • Data Streamed: {(res['bytes_streamed'] / (1024 * 1024)):.2f} MB")
                    print(f"  • Duration: {res['elapsed_seconds']}s")

                elif args.import_dir:
                    print(f"\n{Colors.GREEN}Scanning directory '{args.import_dir}' and importing all user archives...{Colors.RESET}")
                    results = importer.import_directory(args.import_dir)
                    print(f"\n{Colors.GREEN}{Colors.BOLD}Bulk Import Summary:{Colors.RESET}")
                    for uemail, r in results.items():
                        print(f"  • {uemail}: {r.get('synced', 0)} synced, {r.get('skipped', 0)} skipped, {r.get('failed', 0)} failed ({r.get('status', 'DONE')})")

                return

            # Standard Agent Workflow
            # Step 1: Collect credentials interactively into memory vault
            collect_zoho_credentials(vault)
            collect_google_credentials(vault)

            # Step 2: Initialize workflow
            workflow = MigrationWorkflow(
                vault=vault,
                checkpoint_db_path=args.checkpoint_db,
                dry_run=args.dry_run,
                target_users_str=args.users,
                users_file=args.users_file,
                pilot_count=args.pilot,
                skip_calendar=args.skip_calendar or args.mail_only,
                skip_contacts=args.skip_contacts or args.mail_only,
            )

            # Step 3: Pre-flight Verification
            if not workflow.run_preflight():
                print(f"{Colors.RED}Pre-flight verification failed. Exiting.{Colors.RESET}")
                sys.exit(1)

            # Step 4: Organization Discovery & Assessment
            report = workflow.run_discovery()

            # Step 5: Execute Staged Migration
            workflow.execute_migration(report, auto_confirm=args.yes)

        except KeyboardInterrupt:
            print(f"\n{Colors.YELLOW}Operation cancelled by user. Ephemeral vault purged.{Colors.RESET}")
            sys.exit(130)
        except Exception as e:
            logger.exception(f"Fatal error during migration: {e}")
            print(f"\n{Colors.RED}Migration failed with error: {e}{Colors.RESET}")
            sys.exit(1)


if __name__ == "__main__":
    main()
