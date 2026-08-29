"""
Atomic Agent: Scope & Security Compliance Auditor.
Performs pre-flight least-privilege verification, endpoint validation, and DWD checks.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from atomic_agents.base import AtomicAgent
from security.validator import (
    audit_zoho_scopes,
    validate_zoho_domain,
    validate_google_service_account_json
)
from connectors.zoho_client import ZohoAdminClient
from connectors.google_client import GoogleWorkspaceAdminClient
from security.vault import EphemeralVault


@dataclass
class AuditRequest:
    """Input payload for SecurityAuditorAgent."""
    vault: EphemeralVault
    zoho_domain: str = "zoho.com"
    zoho_scopes: List[str] = field(default_factory=list)
    google_admin_email: Optional[str] = None


@dataclass
class AuditReport:
    """Output payload from SecurityAuditorAgent."""
    is_compliant: bool
    zoho_org_name: Optional[str] = None
    zoho_org_id: Optional[str] = None
    google_service_account_email: Optional[str] = None
    checks_passed: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class SecurityAuditorAgent(AtomicAgent[AuditRequest, AuditReport]):
    """
    Atomic Agent responsible strictly for verifying security compliance,
    least-privilege permissions, and API connectivity.
    """

    def __init__(self):
        super().__init__(
            name="SecurityAuditorAgent",
            description="Audits OAuth scopes, validates regional endpoints, and tests DWD delegation."
        )

    def pre_execute(self, input_data: AuditRequest) -> None:
        if not input_data.vault.retrieve("zoho_client_id"):
            raise ValueError("Security Vault is missing 'zoho_client_id'")
        if not input_data.vault.retrieve("google_sa_json"):
            raise ValueError("Security Vault is missing 'google_sa_json'")

    def execute(self, input_data: AuditRequest) -> AuditReport:
        checks_passed = []
        warnings = []
        errors = []

        # 1. Validate Zoho Domain & Regional TLD
        try:
            normalized_domain = validate_zoho_domain(input_data.zoho_domain)
            checks_passed.append(f"Zoho Domain '{normalized_domain}' verified as valid regional data center")
        except Exception as e:
            errors.append(f"Zoho Domain error: {e}")

        # 2. Audit Zoho OAuth Scopes (Least Privilege Enforcement)
        if input_data.zoho_scopes:
            is_safe, scope_warnings, scope_errors = audit_zoho_scopes(input_data.zoho_scopes)
            if not is_safe:
                errors.extend(scope_errors)
            else:
                checks_passed.append(f"All {len(input_data.zoho_scopes)} Zoho OAuth scopes verified as Read-Only")
            warnings.extend(scope_warnings)

        # 3. Validate Google Service Account JSON Schema
        sa_json_str = input_data.vault.retrieve("google_sa_json")
        try:
            sa_info = validate_google_service_account_json(sa_json_str)
            sa_email = sa_info.get("client_email")
            checks_passed.append(f"Google Service Account key valid for '{sa_email}'")
        except Exception as e:
            errors.append(f"Google Service Account JSON validation failed: {e}")
            sa_email = None

        # 4. Test Zoho Live Connection & Org Info
        zoho_client = ZohoAdminClient(vault=input_data.vault, domain=input_data.zoho_domain)
        zoho_org_name = None
        zoho_org_id = None
        try:
            zoho_info = zoho_client.test_connection()
            zoho_org_name = zoho_info.get("org_name")
            zoho_org_id = zoho_info.get("org_id")
            checks_passed.append(f"Connected to Zoho Org '{zoho_org_name}' (ID: {zoho_org_id})")
        except Exception as e:
            errors.append(f"Zoho Admin API connection test failed: {e}")

        # 5. Test Google Workspace DWD Live Connection
        google_admin_email = input_data.google_admin_email or input_data.vault.retrieve("google_admin_email")
        google_client = GoogleWorkspaceAdminClient(vault=input_data.vault, admin_subject_email=google_admin_email)
        try:
            google_info = google_client.test_connection()
            if google_info.get("status") == "connected":
                checks_passed.append(f"Google Workspace Domain-Wide Delegation verified for '{sa_email}'")
            else:
                warnings.append(f"Google DWD check warning: {google_info.get('error')}")
        except Exception as e:
            errors.append(f"Google Workspace DWD test failed: {e}")

        is_compliant = len(errors) == 0
        if not is_compliant:
            raise PermissionError(f"Security pre-flight checks failed with {len(errors)} error(s): {'; '.join(errors)}")

        return AuditReport(
            is_compliant=is_compliant,
            zoho_org_name=zoho_org_name,
            zoho_org_id=zoho_org_id,
            google_service_account_email=sa_email,
            checks_passed=checks_passed,
            warnings=warnings,
            errors=errors
        )
