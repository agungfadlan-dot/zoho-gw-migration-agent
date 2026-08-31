#!/usr/bin/env python3
"""
Zoho to Google Workspace Migration Agent - Main CLI Entrypoint.

Usage:
  python3 main.py [--dry-run] [--checkpoint-db PATH] [-y]
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
