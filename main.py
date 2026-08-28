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

logger = setup_secure_logger("main")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Zoho to Google Workspace Migration Agent (Passwordless Admin-to-Admin Architecture)"
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
        help="Path to SQLite migration checkpoint database (default: migration_checkpoint.db)"
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Automatically confirm and proceed with migration without interactive prompts"
    )
    return parser.parse_args()


def main():
    args = parse_args()
    banner()

    if args.dry_run:
        print(f"{Colors.YELLOW}{Colors.BOLD}>>> RUNNING IN DRY-RUN MODE (No changes will be made to Google Workspace) <<<{Colors.RESET}\n")

    # Initialize in-memory security vault with 2-hour TTL
    with EphemeralVault(ttl_seconds=7200) as vault:
        try:
            # Step 1: Collect credentials interactively into memory vault
            collect_zoho_credentials(vault)
            collect_google_credentials(vault)

            # Step 2: Initialize workflow
            workflow = MigrationWorkflow(
                vault=vault,
                checkpoint_db_path=args.checkpoint_db,
                dry_run=args.dry_run
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
