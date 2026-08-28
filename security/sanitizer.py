"""
Security Sanitizer & Redaction Pipeline.

Security Guardrails:
- Scrub OAuth Bearer tokens, Zoho client secrets, refresh tokens.
- Scrub Google Service Account private keys & client secrets.
- Scrub email authorization codes, password fields, and raw payload bodies.
- Integrates with standard Python logging (RedactingFilter, SanitizedFormatter).
"""

import re
import logging
from typing import Any, Dict, List, Union

# Compiled regex patterns for detecting sensitive data
SECRET_PATTERNS = [
    # Zoho / Generic OAuth Access & Refresh Tokens (e.g., 1000.xxxx.xxxx or 1000.xxxx)
    (re.compile(r'\b1000\.[a-fA-F0-9]{32}\.[a-fA-F0-9]{32}\b'), '[REDACTED_ZOHO_TOKEN]'),
    (re.compile(r'\b1000\.[a-zA-Z0-9_\-]{20,}\b'), '[REDACTED_ZOHO_TOKEN]'),

    # Standard Bearer Tokens
    (re.compile(r'(?i)\bBearer\s+([a-zA-Z0-9\-_\.~+/]+=*)'), 'Bearer [REDACTED_BEARER_TOKEN]'),

    # Google OAuth Tokens (ya29.xxx)
    (re.compile(r'\bya29\.[a-zA-Z0-9_\-]+\b'), '[REDACTED_GOOGLE_OAUTH_TOKEN]'),

    # RSA / EC Private Keys
    (
        re.compile(r'-----BEGIN [A-Z\s]+PRIVATE KEY-----[^-]+-----END [A-Z\s]+PRIVATE KEY-----', re.DOTALL),
        '[REDACTED_PRIVATE_KEY]'
    ),

    # JSON Field matches: client_secret, refresh_token, access_token, private_key, password
    (
        re.compile(r'("?(?:client_secret|refresh_token|access_token|private_key|password|auth_code)"?\s*:\s*")([^"]+)(")', re.IGNORECASE),
        r'\1[REDACTED_SECRET]\3'
    ),

    # Key-value matches: client_secret=xxx, refresh_token=xxx, password=xxx
    (
        re.compile(r'(?i)\b(client_secret|refresh_token|access_token|private_key|password|auth_code)\s*=\s*([^\s&]+)'),
        r'\1=[REDACTED_SECRET]'
    ),
]


def sanitize_text(text: str) -> str:
    """Scans and redacts all recognized sensitive tokens/keys from text."""
    if not isinstance(text, str):
        text = str(text)

    sanitized = text
    for pattern, replacement in SECRET_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)

    return sanitized


def sanitize_dict(data: Union[Dict, List, Any]) -> Any:
    """Recursively redacts sensitive fields in dictionaries or lists."""
    sensitive_keys = {
        'client_secret', 'refresh_token', 'access_token', 'private_key',
        'password', 'auth_code', 'token', 'secret', 'credentials'
    }

    if isinstance(data, dict):
        cleaned = {}
        for k, v in data.items():
            if str(k).lower() in sensitive_keys:
                cleaned[k] = '[REDACTED_SECRET]'
            else:
                cleaned[k] = sanitize_dict(v)
        return cleaned
    elif isinstance(data, list):
        return [sanitize_dict(item) for item in data]
    elif isinstance(data, str):
        return sanitize_text(data)
    return data


class RedactingFilter(logging.Filter):
    """Logging filter that scrubs sensitive information from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = sanitize_text(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = sanitize_dict(record.args)
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    sanitize_text(arg) if isinstance(arg, str) else arg
                    for arg in record.args
                )
        return True


class SanitizedFormatter(logging.Formatter):
    """Logging formatter that ensures formatted strings are fully redacted."""

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        return sanitize_text(original)


def setup_secure_logger(name: str = "migration_agent", level: int = logging.INFO) -> logging.Logger:
    """Configures a logger with automatic redaction attached to console handler."""
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Avoid duplicate handlers
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = SanitizedFormatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")
        handler.setFormatter(formatter)
        handler.addFilter(RedactingFilter())
        logger.addHandler(handler)

    return logger
