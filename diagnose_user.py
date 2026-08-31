#!/usr/bin/env python3
"""
Diagnostic script to test Zoho Mailbox resolution and folder discovery
for an individual user without writing to Google Workspace.
"""

import sys
import os
import json

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from security.vault import EphemeralVault
from connectors.zoho_client import ZohoClient


def diagnose_user(target_email: str = "oksiadri.abacy@andhika.com"):
    print("=" * 80)
    print(f" Zoho Mailbox API Diagnostic Tool: Testing [{target_email}]")
    print("=" * 80)

    client_id = input("Enter Zoho Client ID: ").strip()
    client_secret = input("Enter Zoho Client Secret: ").strip()
    refresh_token = input("Enter Zoho Refresh Token: ").strip()
    domain = input("Enter Zoho Domain [default: zoho.com]: ").strip() or "zoho.com"

    vault = EphemeralVault()
    vault.store("zoho_client_id", client_id)
    vault.store("zoho_client_secret", client_secret)
    vault.store("zoho_refresh_token", refresh_token)
    vault.store("zoho_domain", domain)

    zoho = ZohoClient(vault=vault, domain=domain)
    
    print("\n1. Testing Zoho OAuth Token...")
    token = zoho._get_access_token()
    print(f"   [OK] Access token obtained: {token[:10]}...{token[-5:]}")

    print("\n2. Fetching Organization ID...")
    zoid = zoho.get_organization_id()
    print(f"   [OK] Organization ID (ZOID): {zoid}")

    print("\n3. Inspecting Zoho Mail Accounts Endpoints...")
    candidate_user_endpoints = [
        f"/api/organization/{zoid}/mailaccounts" if zoid else None,
        f"/api/organization/{zoid}/accounts" if zoid else None,
        "/api/organization/mailaccounts",
        "/api/organization/accounts",
        "/api/accounts",
    ]

    target_user_info = None
    target_account_id = None

    for ep in filter(None, candidate_user_endpoints):
        try:
            print(f"   -> Querying: {ep}")
            resp = zoho._api_request(ep)
            data = resp.get("data", [])
            if isinstance(data, dict):
                data = [data]
            
            print(f"      Returned {len(data)} accounts/entries.")
            for item in data:
                email = (
                    item.get("primaryEmailAddress")
                    or item.get("mailboxAddress")
                    or item.get("email")
                    or item.get("emailAddress")
                    or item.get("accountName", "")
                ).lower().strip()

                if email == target_email.lower().strip():
                    target_user_info = item
                    target_account_id = str(
                        item.get("accountId")
                        or item.get("account_id")
                        or item.get("mailAccountId")
                        or item.get("zuid")
                        or ""
                    )
                    print(f"      [MATCH FOUND] Found {target_email} in {ep}!")
                    print(f"      Item Details: {json.dumps(item, indent=2)}")
                    break
            if target_user_info and target_account_id:
                break
        except Exception as err:
            print(f"      [Endpoint {ep} returned]: {err}")

    if not target_account_id and target_user_info:
        target_account_id = str(target_user_info.get("zuid", ""))

    print(f"\n4. Resolved Account ID for {target_email}: [{target_account_id}]")

    print("\n5. Testing Folder Endpoints for resolved Account ID...")
    folder_endpoints = [
        f"/api/accounts/{target_account_id}/folders" if target_account_id else None,
        f"/api/organization/{zoid}/accounts/{target_account_id}/folders" if (zoid and target_account_id) else None,
        f"/api/organization/{zoid}/users/{target_account_id}/folders" if (zoid and target_account_id) else None,
        f"/api/organization/accounts/{target_account_id}/folders" if target_account_id else None,
    ]

    successful_folders = []
    for ep in filter(None, folder_endpoints):
        try:
            print(f"   -> Testing: {ep}")
            resp = zoho._api_request(ep)
            data = resp.get("data", [])
            if isinstance(data, dict):
                data = [data]
            print(f"      [SUCCESS on {ep}] Found {len(data)} folder(s):")
            for f in data:
                fname = f.get("folderName") or f.get("name")
                fid = f.get("folderId") or f.get("id")
                fcount = f.get("totalCount") or f.get("messageCount") or f.get("count") or 0
                fsize = f.get("size") or f.get("folderSize") or 0
                print(f"        * Folder: {fname} (ID: {fid}) -> Messages: {fcount}, Size: {fsize}")
            if data:
                successful_folders = data
                break
        except Exception as err:
            print(f"      [Endpoint {ep} returned]: {err}")

    print("\n" + "=" * 80)
    if successful_folders:
        print(f" DIAGNOSTIC RESULT: SUCCESS! Retrieved folders for {target_email}.")
    else:
        print(f" DIAGNOSTIC RESULT: FAILED to fetch folders for {target_email}.")
    print("=" * 80)


if __name__ == "__main__":
    email = sys.argv[1] if len(sys.argv) > 1 else "oksiadri.abacy@andhika.com"
    diagnose_user(email)
