"""
Google Workspace Client with Domain-Wide Delegation (DWD).

Security & Passwordless Guardrails:
- Authenticates via Service Account JWT assertions with Domain-Wide Delegation.
- Impersonates target users directly via 'sub' claim—zero need for user passwords.
- Provisions new users with cryptographically secure temporary passwords and enforces
  'changePasswordAtNextLogin: true'.
- Ephemeral in-memory token cache per impersonated user.
"""

import json
import time
import base64
import urllib.request
import urllib.parse
import urllib.error
import secrets
import string
from typing import List, Dict, Optional, Any, Tuple

from security.vault import EphemeralVault
from security.sanitizer import setup_secure_logger, sanitize_dict
from security.validator import validate_google_service_account_json
from connectors.base import CalendarEvent, ContactRecord
from engine.rate_limiter import TokenBucket, retry_with_backoff

logger = setup_secure_logger("google_client")

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    HAS_CRYPTO_RSA = True
except ImportError:
    HAS_CRYPTO_RSA = False


class GoogleClientError(Exception):
    """Raised when Google Workspace API requests fail."""
    pass


def _b64url_encode(data: bytes) -> str:
    """Encodes bytes into URL-safe base64 without padding."""
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def generate_secure_temporary_password(length: int = 18) -> str:
    """Generates a cryptographically secure random password meeting Google complexity rules."""
    upper = string.ascii_uppercase
    lower = string.ascii_lowercase
    digits = string.digits
    symbols = "!@#$%^&*"

    # Ensure at least one of each category
    password = [
        secrets.choice(upper),
        secrets.choice(lower),
        secrets.choice(digits),
        secrets.choice(symbols),
    ]

    all_chars = upper + lower + digits + symbols
    for _ in range(length - 4):
        password.append(secrets.choice(all_chars))

    # Shuffle characters
    secrets.SystemRandom().shuffle(password)
    return "".join(password)


class GoogleWorkspaceAdminClient:
    """Google Workspace Admin SDK, Gmail, Calendar, and People API client with DWD."""

    def __init__(
        self,
        vault: EphemeralVault,
        sa_key_name: str = "google_sa_json",
        admin_subject_email: Optional[str] = None,
        rate_limit_rps: float = 15.0,
    ):
        self.vault = vault
        self.sa_key_name = sa_key_name
        self.admin_subject_email = admin_subject_email
        self.rate_limiter = TokenBucket(rate_per_second=rate_limit_rps, capacity=30.0)

        # In-memory token cache: (subject, tuple_of_scopes) -> (token, expires_at)
        self._user_token_cache: Dict[Tuple[str, str], Tuple[str, float]] = {}

        # Validate SA JSON exists in vault
        sa_json_str = self.vault.retrieve(self.sa_key_name)
        if not sa_json_str:
            raise GoogleClientError(f"Missing Google Service Account JSON in vault under '{sa_key_name}'.")

        self.sa_info = validate_google_service_account_json(sa_json_str)
        self._parsed_sa = json.loads(sa_json_str)

    def _sign_jwt_assertion(self, header: Dict, payload: Dict, private_key_pem: str) -> str:
        """Signs a JWT with RS256 using the Service Account private key."""
        encoded_header = _b64url_encode(json.dumps(header).encode("utf-8"))
        encoded_payload = _b64url_encode(json.dumps(payload).encode("utf-8"))
        signing_input = f"{encoded_header}.{encoded_payload}".encode("utf-8")

        if HAS_CRYPTO_RSA:
            private_key = serialization.load_pem_private_key(
                private_key_pem.encode("utf-8"),
                password=None
            )
            signature = private_key.sign(
                signing_input,
                padding.PKCS1v15(),
                hashes.SHA256()
            )
            encoded_signature = _b64url_encode(signature)
            return f"{encoded_header}.{encoded_payload}.{encoded_signature}"
        else:
            raise GoogleClientError(
                "Cryptography package is required for RSA JWT signing. "
                "Please run 'pip install cryptography'."
            )

    def get_delegated_token(self, subject_email: Optional[str], scopes: List[str]) -> str:
        """
        Obtains an OAuth2 access token for the given subject (impersonation) and scopes.
        """
        cache_key = (subject_email or self.sa_info["client_email"], " ".join(sorted(scopes)))
        now = time.time()

        if cache_key in self._user_token_cache:
            token, exp = self._user_token_cache[cache_key]
            if now < exp - 60:
                return token

        # Build JWT Claims
        iat = int(now)
        exp = iat + 3600

        payload = {
            "iss": self.sa_info["client_email"],
            "scope": " ".join(scopes),
            "aud": self.sa_info["token_uri"],
            "exp": exp,
            "iat": iat,
        }

        if subject_email:
            payload["sub"] = subject_email

        header = {"alg": "RS256", "typ": "JWT"}
        assertion = self._sign_jwt_assertion(header, payload, self._parsed_sa["private_key"])

        token_req_data = urllib.parse.urlencode({
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        }).encode("utf-8")

        req = urllib.request.Request(self.sa_info["token_uri"], data=token_req_data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                access_token = resp_data["access_token"]
                expires_in = resp_data.get("expires_in", 3600)
                self._user_token_cache[cache_key] = (access_token, now + expires_in)
                return access_token
        except urllib.error.HTTPError as he:
            err = he.read().decode("utf-8", errors="ignore")
            raise GoogleClientError(
                f"Google DWD Token exchange failed for {subject_email or 'SA'}: {err}"
            )

    def _request(
        self,
        url: str,
        method: str = "GET",
        data: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
        subject_email: Optional[str] = None,
        scopes: Optional[List[str]] = None,
    ) -> Any:
        """Executes authenticated Google Workspace API request."""
        self.rate_limiter.acquire()
        if not scopes:
            scopes = ["https://www.googleapis.com/auth/admin.directory.user"]

        token = self.get_delegated_token(subject_email, scopes)
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/json")

        if headers:
            for k, v in headers.items():
                req.add_header(k, v)

        def _do_request():
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    resp_bytes = resp.read()
                    if not resp_bytes:
                        return {}
                    return json.loads(resp_bytes.decode("utf-8"))
            except urllib.error.HTTPError as he:
                err_body = he.read().decode("utf-8", errors="ignore")
                raise GoogleClientError(f"Google API Error {he.code} on {url}: {err_body}")

        return retry_with_backoff(_do_request, max_retries=3, initial_delay=1.0)

    # --- Pre-flight Check ---

    def test_connection(self) -> Dict[str, Any]:
        """Validates Service Account token generation and Directory API access."""
        subject = self.admin_subject_email
        scopes = ["https://www.googleapis.com/auth/admin.directory.user.readonly"]
        token = self.get_delegated_token(subject, scopes)

        url = "https://admin.googleapis.com/admin/directory/v1/users?customer=my_customer&maxResults=1"
        try:
            resp = self._request(url, subject_email=subject, scopes=scopes)
            return {
                "status": "connected",
                "client_email": self.sa_info["client_email"],
                "project_id": self.sa_info["project_id"],
                "impersonated_admin": subject or "Direct SA",
            }
        except GoogleClientError as e:
            return {
                "status": "warning",
                "client_email": self.sa_info["client_email"],
                "error": str(e),
            }

    # --- User Provisioning ---

    def provision_user(
        self,
        email: str,
        first_name: str,
        last_name: str,
        aliases: Optional[List[str]] = None,
        org_unit_path: str = "/"
    ) -> Dict[str, Any]:
        """
        Provisions a new user in Google Workspace via Admin SDK.
        Enforces random strong password + force change on first login.
        """
        temp_pwd = generate_secure_temporary_password(20)
        user_body = {
            "primaryEmail": email.lower().strip(),
            "name": {
                "givenName": first_name or "User",
                "familyName": last_name or "Account",
            },
            "password": temp_pwd,
            "changePasswordAtNextLogin": True,
            "orgUnitPath": org_unit_path,
        }

        scopes = ["https://www.googleapis.com/auth/admin.directory.user"]
        url = "https://admin.googleapis.com/admin/directory/v1/users"

        payload_bytes = json.dumps(user_body).encode("utf-8")
        headers = {"Content-Type": "application/json"}

        try:
            res = self._request(
                url,
                method="POST",
                data=payload_bytes,
                headers=headers,
                subject_email=self.admin_subject_email,
                scopes=scopes
            )
            logger.info(f"Successfully provisioned Google Workspace user: {email}")

            # Add aliases if present
            if aliases:
                for alias in aliases:
                    self.add_user_alias(email, alias)

            return {
                "status": "CREATED",
                "email": email,
                "id": res.get("id"),
                "temp_password": temp_pwd,  # Provided once for admin report, never stored in DB
            }
        except GoogleClientError as e:
            if "409" in str(e) or "duplicate" in str(e).lower() or "already exists" in str(e).lower():
                logger.info(f"User {email} already exists in Google Workspace. Skipping creation.")
                return {"status": "EXISTS", "email": email}
            raise e

    def add_user_alias(self, primary_email: str, alias_email: str) -> None:
        """Adds an email alias to a user."""
        scopes = ["https://www.googleapis.com/auth/admin.directory.user"]
        url = f"https://admin.googleapis.com/admin/directory/v1/users/{primary_email}/aliases"
        body = {"alias": alias_email.lower().strip()}

        try:
            self._request(
                url,
                method="POST",
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                subject_email=self.admin_subject_email,
                scopes=scopes
            )
            logger.info(f"Added alias {alias_email} for {primary_email}")
        except GoogleClientError as e:
            logger.warning(f"Could not add alias {alias_email} to {primary_email}: {e}")

    # --- Gmail Labels & Messages ---

    def ensure_label(self, user_email: str, label_name: str) -> str:
        """
        Ensures a Gmail label exists for the given user, creating it if needed.
        Returns the Google Label ID.
        """
        scopes = ["https://www.googleapis.com/auth/gmail.labels"]
        list_url = f"https://gmail.googleapis.com/gmail/v1/users/{user_email}/labels"

        try:
            resp = self._request(list_url, subject_email=user_email, scopes=scopes)
            for lab in resp.get("labels", []):
                if lab.get("name", "").lower() == label_name.lower():
                    return lab["id"]

            # Label doesn't exist, create it
            create_url = f"https://gmail.googleapis.com/gmail/v1/users/{user_email}/labels"
            body = {
                "name": label_name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            }
            res = self._request(
                create_url,
                method="POST",
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                subject_email=user_email,
                scopes=scopes
            )
            return res["id"]
        except GoogleClientError as e:
            logger.warning(f"Could not create/get label '{label_name}' for {user_email}: {e}")
            return "INBOX"

    def import_message(
        self,
        user_email: str,
        raw_rfc822_bytes: bytes,
        label_ids: Optional[List[str]] = None,
        is_read: bool = True,
        internal_date_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Inserts/imports a raw RFC822 email message directly into target user mailbox.
        Zero user password required.
        """
        scopes = ["https://www.googleapis.com/auth/gmail.insert"]

        # Gmail import upload endpoint (Multipart upload)
        upload_url = (
            f"https://gmail.googleapis.com/upload/gmail/v1/users/{user_email}/messages/import"
            f"?internalDateSource=dateHeader&neverMarkSpam=true&processForCalendar=false"
        )

        metadata: Dict[str, Any] = {
            "labelIds": label_ids or ["INBOX"],
        }
        if not is_read:
            metadata["labelIds"].append("UNREAD")

        if internal_date_ms:
            metadata["internalDate"] = str(internal_date_ms)

        # Multipart MIME boundary
        boundary = "===============MIGRATION_RFC822_BOUNDARY=="
        meta_json = json.dumps(metadata)

        multipart_body = (
            f"--{boundary}\r\n"
            f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{meta_json}\r\n"
            f"--{boundary}\r\n"
            f"Content-Type: message/rfc822\r\n\r\n"
        ).encode("utf-8") + raw_rfc822_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

        headers = {
            "Content-Type": f"multipart/related; boundary={boundary}",
            "Content-Length": str(len(multipart_body)),
        }

        res = self._request(
            upload_url,
            method="POST",
            data=multipart_body,
            headers=headers,
            subject_email=user_email,
            scopes=scopes,
        )
        return res

    # Alias for explicit naming
    import_message_rfc822 = import_message

    # --- Google Calendar Events ---

    def insert_calendar_event(self, user_email: str, event: CalendarEvent) -> Dict[str, Any]:
        """Inserts a calendar event into user's primary calendar."""
        scopes = ["https://www.googleapis.com/auth/calendar.events"]
        url = f"https://www.googleapis.com/calendar/v3/calendars/{user_email}/events"

        body: Dict[str, Any] = {
            "summary": event.title,
            "description": event.description or "",
            "location": event.location or "",
        }

        if event.is_all_day:
            body["start"] = {"date": event.start_time[:10]}
            body["end"] = {"date": event.end_time[:10]}
        else:
            body["start"] = {"dateTime": event.start_time}
            body["end"] = {"dateTime": event.end_time}

        if event.recurrence:
            body["recurrence"] = event.recurrence

        if event.attendees:
            body["attendees"] = [{"email": a} for a in event.attendees]

        res = self._request(
            url,
            method="POST",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            subject_email=user_email,
            scopes=scopes,
        )
        return res

    # --- Google People / Contacts ---

    def insert_contact(self, user_email: str, contact: ContactRecord) -> Dict[str, Any]:
        """Creates a contact in user's Google Contacts via People API."""
        scopes = ["https://www.googleapis.com/auth/contacts"]
        url = "https://people.googleapis.com/v1/people:createContact"

        body: Dict[str, Any] = {
            "names": [
                {
                    "givenName": contact.first_name,
                    "familyName": contact.last_name,
                    "displayName": contact.display_name,
                }
            ],
            "emailAddresses": [{"value": e} for e in contact.email_addresses],
            "phoneNumbers": [{"value": p} for p in contact.phone_numbers],
        }

        if contact.company or contact.job_title:
            body["organizations"] = [
                {
                    "name": contact.company or "",
                    "title": contact.job_title or "",
                }
            ]

        if contact.notes:
            body["biographies"] = [{"value": contact.notes, "contentType": "TEXT_PLAIN"}]

        res = self._request(
            url,
            method="POST",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            subject_email=user_email,
            scopes=scopes,
        )
        return res
