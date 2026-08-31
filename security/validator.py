"""
Security & Scope Validator.

Security Guardrails:
- Validates Zoho Data Center region to avoid cross-region token leaks.
- Audits Zoho OAuth scopes to enforce least privilege (strictly Read-Only).
- Audits Google Service Account credentials & Domain-Wide Delegation scopes.
"""

import json
from typing import List, Dict, Tuple, Optional, Set

VALID_ZOHO_DOMAINS = {
    "zoho.com": "https://accounts.zoho.com",
    "zoho.eu": "https://accounts.zoho.eu",
    "zoho.in": "https://accounts.zoho.in",
    "zoho.com.au": "https://accounts.zoho.com.au",
    "zoho.com.cn": "https://accounts.zoho.com.cn",
    "zohocloud.ca": "https://accounts.zohocloud.ca",
}

# Approved read-only Zoho scopes for passwordless admin migration
APPROVED_ZOHO_READ_SCOPES = {
    "ZohoMail.organization.accounts.READ",
    "ZohoMail.organization.accounts.ALL",
    "ZohoMail.accounts.READ",
    "ZohoMail.accounts.ALL",
    "ZohoMail.messages.READ",
    "ZohoMail.messages.ALL",
    "ZohoMail.partner.organization.READ",
    "ZohoCalendar.event.READ",
    "ZohoCalendar.event.ALL",
    "ZohoContacts.contactapi.READ",
    "ZohoContacts.contactapi.ALL",
    "ZohoContacts.contacts.READ",
    "ZohoContacts.user.READ",
    "ZohoDirectory.user.READ",
    "ZohoDirectory.org.READ",
}

# Forbidden Zoho scopes (destructive / unnecessary write access)
FORBIDDEN_ZOHO_SCOPES = {
    "ZohoMail.messages.DELETE",
    "ZohoMail.messages.UPDATE",
    "ZohoMail.messages.CREATE",
    "ZohoMail.accounts.DELETE",
    "ZohoDirectory.user.DELETE",
    "ZohoDirectory.user.UPDATE",
}

# Required Google Workspace Domain-Wide Delegation Scopes
REQUIRED_GOOGLE_SCOPES = {
    "https://www.googleapis.com/auth/admin.directory.user",  # Provision users & aliases
    "https://www.googleapis.com/auth/gmail.insert",          # Insert emails without user passwords
    "https://www.googleapis.com/auth/gmail.labels",          # Create & apply folder labels
    "https://www.googleapis.com/auth/calendar.events",       # Insert calendar events
    "https://www.googleapis.com/auth/contacts",              # Create user contacts
}


class ScopeValidationError(Exception):
    """Raised when OAuth scopes violate security guardrails."""
    pass


def validate_zoho_domain(domain: str) -> str:
    """Validates and normalizes Zoho domain."""
    cleaned = domain.strip().lower().lstrip(".").replace("https://", "").replace("http://", "")
    if cleaned.startswith("accounts."):
        cleaned = cleaned.replace("accounts.", "")

    if cleaned not in VALID_ZOHO_DOMAINS:
        valid_list = ", ".join(VALID_ZOHO_DOMAINS.keys())
        raise ValueError(f"Invalid Zoho Data Center domain '{domain}'. Must be one of: {valid_list}")
    return cleaned


def audit_zoho_scopes(scopes: List[str]) -> Tuple[bool, List[str], List[str]]:
    """
    Audits Zoho scopes against security rules.
    Returns: (is_valid, warnings, errors)
    """
    warnings: List[str] = []
    errors: List[str] = []
    scope_set = set(s.strip() for s in scopes if s.strip())

    # Check for forbidden destructive scopes
    forbidden_matches = scope_set.intersection(FORBIDDEN_ZOHO_SCOPES)
    if forbidden_matches:
        errors.append(
            f"Unsafe/Destructive Zoho scopes detected: {', '.join(forbidden_matches)}. "
            "Migration agent requires strictly READ-ONLY scopes."
        )

    # Check for write scopes
    for s in scope_set:
        if any(action in s.upper() for action in ["DELETE", "CREATE", "UPDATE", "WRITE", "MANAGE"]):
            if s not in errors:
                warnings.append(f"Scope '{s}' includes write/manage permissions. Verify if read-only alternative is available.")

    # Check if necessary read scopes are present
    has_mail_read = (
        any("ZohoMail" in s and ("READ" in s.upper() or "ALL" in s.upper()) for s in scope_set)
    )
    has_dir_read = (
        any("ZohoDirectory" in s and ("READ" in s.upper() or "ALL" in s.upper()) for s in scope_set)
        or any("ZohoMail.organization.accounts" in s for s in scope_set)
        or "ZohoMail.accounts.READ" in scope_set
        or "ZohoMail.accounts.ALL" in scope_set
    )

    if not has_mail_read:
        warnings.append("Missing Zoho Mail Read scope (e.g., ZohoMail.messages.READ or ZohoMail.organization.accounts.READ).")
    if not has_dir_read:
        warnings.append("Missing Zoho Organization/Directory Read scope (e.g., ZohoMail.organization.accounts.READ).")

    is_valid = len(errors) == 0
    return is_valid, warnings, errors


def validate_google_service_account_json(sa_json_str: str) -> Dict[str, str]:
    """
    Validates the structure and integrity of Google Service Account credentials.
    Ensures private key, client_email, and required fields are valid.
    """
    try:
        data = json.loads(sa_json_str)
    except Exception as e:
        raise ValueError(f"Invalid Google Service Account JSON: {e}")

    required_fields = ["type", "project_id", "private_key_id", "private_key", "client_email", "token_uri"]
    missing = [f for f in required_fields if f not in data]
    if missing:
        raise ValueError(f"Google Service Account JSON is missing required fields: {', '.join(missing)}")

    if data.get("type") != "service_account":
        raise ValueError(f"Invalid credential type: expected 'service_account', got '{data.get('type')}'")

    private_key = data.get("private_key", "")
    if "-----BEGIN PRIVATE KEY-----" not in private_key or "-----END PRIVATE KEY-----" not in private_key:
        raise ValueError("Google Service Account private key is malformed or corrupted.")

    return {
        "client_email": data["client_email"],
        "project_id": data["project_id"],
        "token_uri": data.get("token_uri", "https://oauth2.googleapis.com/token"),
    }
