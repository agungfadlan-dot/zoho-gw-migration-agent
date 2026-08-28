"""
Interactive Credential Collector with Masked Input & Security Enclave Integration.

Security Guardrails:
- Credentials collected via getpass (masked, not echoed).
- Loaded directly into EphemeralVault (AES-256-GCM in-memory store).
- Service Account JSON read in-memory and immediately sanitized.
"""

import os
import sys
import getpass
import json
from typing import Dict, Any, Optional

from security.vault import EphemeralVault
from security.validator import (
    validate_zoho_domain,
    audit_zoho_scopes,
    validate_google_service_account_json,
    VALID_ZOHO_DOMAINS
)
from agent.console import Colors


def collect_zoho_credentials(vault: EphemeralVault) -> str:
    """Interactively prompts admin for Zoho API credentials."""
    print(f"\n{Colors.BOLD}{Colors.CYAN}--- Step 1: Zoho Global Admin API Credentials ---{Colors.RESET}")
    print(f"{Colors.DIM}Create a Self-Client / Server-based client in https://api-console.zoho.com{Colors.RESET}\n")

    # Domain selection
    print("Select Zoho Data Center:")
    domains = list(VALID_ZOHO_DOMAINS.keys())
    for idx, d in enumerate(domains, 1):
        print(f"  [{idx}] {d}")

    while True:
        choice = input(f"Enter choice [1-{len(domains)}] (default: 1 [zoho.com]): ").strip()
        if not choice:
            selected_domain = "zoho.com"
            break
        if choice.isdigit() and 1 <= int(choice) <= len(domains):
            selected_domain = domains[int(choice) - 1]
            break
        print(f"{Colors.RED}Invalid choice. Please select a valid number.{Colors.RESET}")

    # Prompt Client ID
    client_id = input("Zoho Client ID: ").strip()
    while not client_id:
        client_id = input(f"{Colors.RED}Client ID cannot be empty: {Colors.RESET}").strip()

    # Masked Client Secret
    client_secret = getpass.getpass("Zoho Client Secret [Masked]: ").strip()
    while not client_secret:
        client_secret = getpass.getpass(f"{Colors.RED}Client Secret cannot be empty: {Colors.RESET}").strip()

    # Masked Refresh Token
    refresh_token = getpass.getpass("Zoho Admin Refresh Token [Masked]: ").strip()
    while not refresh_token:
        refresh_token = getpass.getpass(f"{Colors.RED}Refresh Token cannot be empty: {Colors.RESET}").strip()

    # Store into in-memory vault
    vault.store("zoho_client_id", client_id)
    vault.store("zoho_client_secret", client_secret)
    vault.store("zoho_refresh_token", refresh_token)
    vault.store("zoho_domain", selected_domain)

    print(f"{Colors.GREEN}✓ Zoho credentials securely ingested into Ephemeral Vault.{Colors.RESET}")
    return selected_domain


def collect_google_credentials(vault: EphemeralVault) -> str:
    """Prompts admin for Google Cloud Service Account JSON key."""
    print(f"\n{Colors.BOLD}{Colors.CYAN}--- Step 2: Google Workspace Service Account ---{Colors.RESET}")
    print(f"{Colors.DIM}Requires a Service Account with Domain-Wide Delegation enabled.{Colors.RESET}\n")

    while True:
        key_path = input("Enter path to Google Service Account JSON key: ").strip()
        expanded_path = os.path.expanduser(key_path)

        if not os.path.isfile(expanded_path):
            print(f"{Colors.RED}File not found at '{key_path}'. Please check path.{Colors.RESET}")
            continue

        try:
            with open(expanded_path, "r", encoding="utf-8") as f:
                raw_json = f.read()

            info = validate_google_service_account_json(raw_json)
            vault.store("google_sa_json", raw_json)
            break
        except Exception as e:
            print(f"{Colors.RED}Error validating Service Account JSON: {e}{Colors.RESET}")

    admin_email = input("Enter Google Workspace Super Admin email (for DWD directory provisioning): ").strip()
    while not admin_email or "@" not in admin_email:
        admin_email = input(f"{Colors.RED}Valid admin email is required: {Colors.RESET}").strip()

    vault.store("google_admin_email", admin_email)

    print(f"{Colors.GREEN}✓ Google Workspace Service Account verified and stored in Ephemeral Vault.{Colors.RESET}")
    return admin_email
