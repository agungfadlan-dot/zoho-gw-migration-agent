"""
Security package initialization.
"""

from .vault import EphemeralVault, EphemeralVaultError
from .sanitizer import sanitize_text, sanitize_dict, setup_secure_logger, SanitizedFormatter, RedactingFilter
from .validator import (
    validate_zoho_domain,
    audit_zoho_scopes,
    validate_google_service_account_json,
    APPROVED_ZOHO_READ_SCOPES,
    REQUIRED_GOOGLE_SCOPES,
)

__all__ = [
    "EphemeralVault",
    "EphemeralVaultError",
    "sanitize_text",
    "sanitize_dict",
    "setup_secure_logger",
    "SanitizedFormatter",
    "RedactingFilter",
    "validate_zoho_domain",
    "audit_zoho_scopes",
    "validate_google_service_account_json",
    "APPROVED_ZOHO_READ_SCOPES",
    "REQUIRED_GOOGLE_SCOPES",
]
