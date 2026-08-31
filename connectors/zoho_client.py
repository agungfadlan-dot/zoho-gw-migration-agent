"""
Zoho Mail, Directory, Calendar & Contacts API Client.

Security Guardrails:
- Credentials retrieved from EphemeralVault on demand.
- Scoped to read-only OAuth tokens.
- No plain text logging of tokens or request bodies.
- Streams RFC822 messages via generator buffers without writing to disk.
"""

import json
import time
import urllib.request
import urllib.parse
import urllib.error
from typing import List, Dict, Optional, Generator, Tuple, Any

from security.vault import EphemeralVault
from security.sanitizer import sanitize_dict, setup_secure_logger
from connectors.base import ZohoUser, MailFolder, MailMessageMeta, CalendarEvent, ContactRecord
from engine.rate_limiter import TokenBucket, retry_with_backoff

logger = setup_secure_logger("zoho_client")

ZOHO_ACCOUNTS_URLS = {
    "zoho.com": "https://accounts.zoho.com",
    "zoho.eu": "https://accounts.zoho.eu",
    "zoho.in": "https://accounts.zoho.in",
    "zoho.com.au": "https://accounts.zoho.com.au",
    "zoho.com.cn": "https://accounts.zoho.com.cn",
    "zohocloud.ca": "https://accounts.zohocloud.ca",
}

ZOHO_MAIL_URLS = {
    "zoho.com": "https://mail.zoho.com",
    "zoho.eu": "https://mail.zoho.eu",
    "zoho.in": "https://mail.zoho.in",
    "zoho.com.au": "https://mail.zoho.com.au",
    "zoho.com.cn": "https://mail.zoho.com.cn",
    "zohocloud.ca": "https://mail.zohocloud.ca",
}


class ZohoClientError(Exception):
    """Raised when Zoho API requests encounter an error."""
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class NonRetryableZohoError(ZohoClientError):
    """Raised when Zoho returns client errors (400, 401, 403, 404) that should not be retried."""
    pass


class ZohoEndpointUnsupportedError(ZohoClientError):
    """Raised when an endpoint is not supported or configured on Zoho Mail (e.g., 404 URL_RULE_NOT_CONFIGURED)."""
    pass


class ZohoAdminClient:
    """Client for Zoho Organization Directory, Mail, Calendar, and Contacts APIs."""

    def __init__(
        self,
        vault: EphemeralVault,
        client_id_key: str = "zoho_client_id",
        client_secret_key: str = "zoho_client_secret",
        refresh_token_key: str = "zoho_refresh_token",
        domain: str = "zoho.com",
        rate_limit_rps: float = 8.0,
    ):
        self.vault = vault
        self.client_id_key = client_id_key
        self.client_secret_key = client_secret_key
        self.refresh_token_key = refresh_token_key
        self.domain = domain.lower().strip()

        self.accounts_base = ZOHO_ACCOUNTS_URLS.get(self.domain, "https://accounts.zoho.com")
        self.mail_base = ZOHO_MAIL_URLS.get(self.domain, "https://mail.zoho.com")

        self.rate_limiter = TokenBucket(rate_per_second=rate_limit_rps, capacity=16.0)
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    def _get_access_token(self) -> str:
        """Retrieves or refreshes OAuth 2.0 Access Token."""
        now = time.time()
        if self._access_token and now < self._token_expires_at - 60:
            return self._access_token

        client_id = self.vault.retrieve(self.client_id_key)
        client_secret = self.vault.retrieve(self.client_secret_key)
        refresh_token = self.vault.retrieve(self.refresh_token_key)

        if not client_id or not client_secret or not refresh_token:
            raise ZohoClientError("Missing Zoho credentials in EphemeralVault.")

        token_url = f"{self.accounts_base}/oauth/v2/token"
        payload = urllib.parse.urlencode({
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }).encode("utf-8")

        req = urllib.request.Request(token_url, data=payload, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if "error" in data:
                    raise ZohoClientError(f"Zoho token refresh failed: {data.get('error')}")

                self._access_token = data["access_token"]
                expires_in = data.get("expires_in", 3600)
                self._token_expires_at = now + expires_in
                logger.info("Successfully refreshed Zoho OAuth 2.0 access token.")
                return self._access_token
        except Exception as e:
            raise ZohoClientError(f"Failed to communicate with Zoho OAuth server: {e}")

    def _api_request(
        self,
        endpoint: str,
        method: str = "GET",
        data: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
        raw_response: bool = False
    ) -> Any:
        """Executes rate-limited, authenticated API request against Zoho."""
        self.rate_limiter.acquire()
        token = self._get_access_token()

        url = f"{self.mail_base}{endpoint}" if not endpoint.startswith("http") else endpoint
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Zoho-oauthtoken {token}")
        req.add_header("Accept", "application/json")

        if headers:
            for k, v in headers.items():
                req.add_header(k, v)

        def _do_request():
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    if raw_response:
                        return resp.read()
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as he:
                err_body = he.read().decode("utf-8", errors="ignore")
                if he.code in (400, 401, 403, 404):
                    raise NonRetryableZohoError(f"Zoho API Error {he.code} on {endpoint}: {err_body}", status_code=he.code)
                raise ZohoClientError(f"Zoho API Error {he.code} on {endpoint}: {err_body}", status_code=he.code)

        try:
            return retry_with_backoff(
                _do_request,
                max_retries=3,
                initial_delay=1.0,
                retryable_exceptions=(ZohoClientError, urllib.error.URLError, TimeoutError, ConnectionError)
            )
        except NonRetryableZohoError as nre:
            raise ZohoClientError(str(nre), status_code=nre.status_code) from nre

    # --- Directory Discovery ---

    def test_connection(self) -> Dict[str, Any]:
        """Tests connectivity and verifies organization admin access."""
        try:
            resp = self._api_request("/api/organization")
            data = resp.get("data", {})
            return {
                "status": "connected",
                "org_name": data.get("orgName", "Unknown"),
                "org_id": data.get("orgId", ""),
                "domain": self.domain,
                "user_count": data.get("userCount", 0),
            }
        except ZohoClientError:
            # Fallback to /api/accounts (requires standard ZohoMail.accounts.READ / ALL)
            resp = self._api_request("/api/accounts")
            data = resp.get("data", [])
            primary_account = data[0] if isinstance(data, list) and data else {}
            return {
                "status": "connected",
                "org_name": primary_account.get("accountName", "Zoho Mail Organization"),
                "org_id": primary_account.get("accountId", ""),
                "domain": self.domain,
                "user_count": len(data) if isinstance(data, list) else 1,
            }

    def get_organization_id(self) -> Optional[str]:
        """Retrieves the organization ID (zoid) from Zoho Mail."""
        try:
            resp = self._api_request("/api/organization")
            data = resp.get("data", {})
            if isinstance(data, dict):
                org_id = data.get("orgId") or data.get("zoid") or data.get("organizationId")
                if org_id:
                    return str(org_id)
        except Exception:
            pass

        try:
            resp = self._api_request("/api/accounts")
            data = resp.get("data", [])
            if isinstance(data, list):
                for acc in data:
                    org_id = acc.get("zoid") or acc.get("orgId") or acc.get("organizationId")
                    if org_id:
                        return str(org_id)
        except Exception:
            pass
        return None

    @staticmethod
    def _parse_single_storage_val(val: Any) -> int:
        if val is None or val == "" or val == "-":
            return 0
        if isinstance(val, (int, float)):
            # If float/int is small (< 1024), it represents GB (e.g. 29.58 GB from CSV/API)
            if 0 < val <= 1024:
                return int(val * 1024 * 1024 * 1024)
            # If medium (< 1,000,000), it represents MB (e.g. 29580 MB)
            if 1024 < val <= 1024 * 1024:
                return int(val * 1024 * 1024)
            return int(val)
        if isinstance(val, str):
            s = val.strip().lower()
            if not s or s == "-":
                return 0
            try:
                if "gb" in s:
                    num = float(s.replace("gb", "").strip())
                    return int(num * 1024 * 1024 * 1024)
                if "mb" in s:
                    num = float(s.replace("mb", "").strip())
                    return int(num * 1024 * 1024)
                if "kb" in s:
                    num = float(s.replace("kb", "").strip())
                    return int(num * 1024)
                num = float(s)
                if 0 < num <= 1024:
                    return int(num * 1024 * 1024 * 1024)
                if 1024 < num <= 1024 * 1024:
                    return int(num * 1024 * 1024)
                return int(num)
            except ValueError:
                return 0
        return 0

    @classmethod
    def _parse_storage_bytes(cls, data: Dict[str, Any]) -> int:
        """Extracts and parses storage bytes from a Zoho user dictionary."""
        candidates = [
            data.get("usedMailStorage"),
            data.get("mailStorage"),
            data.get("usedStorage"),
            data.get("storageUsed"),
            data.get("storage"),
            data.get("size"),
            data.get("consumedStorage"),
            data.get("storageDetails"),
            data.get("diskUsage"),
        ]
        for val in candidates:
            if val is None or val == "" or val == "-":
                continue
            if isinstance(val, dict):
                sub = val.get("used") or val.get("usedMailStorage") or val.get("mailStorage") or val.get("usedStorage")
                if sub is not None:
                    parsed = cls._parse_single_storage_val(sub)
                    if parsed > 0:
                        return parsed
            else:
                parsed = cls._parse_single_storage_val(val)
                if parsed > 0:
                    return parsed
        return 0

    def list_organization_users(self) -> List[ZohoUser]:
        """Fetches all users from Zoho Organization Directory."""
        users: List[ZohoUser] = []
        zoid = self.get_organization_id()

        # Candidate endpoint formats supported across Zoho Mail versions
        endpoints_to_try = []
        if zoid:
            endpoints_to_try.extend([
                f"/api/organization/{zoid}/accounts",
                f"/api/organization/{zoid}/users",
                f"/api/organization/{zoid}/mailaccounts",
            ])
        endpoints_to_try.extend([
            "/api/organization/accounts",
            "/api/organization/users",
            "/api/accounts",
        ])

        for ep_base in endpoints_to_try:
            start_index = 1
            limit = 100
            collected: List[ZohoUser] = []
            try:
                while True:
                    delim = "&" if "?" in ep_base else "?"
                    url = f"{ep_base}{delim}start={start_index}&limit={limit}"
                    resp = self._api_request(url)
                    user_list = resp.get("data", [])
                    if not user_list:
                        break

                    if isinstance(user_list, dict):
                        user_list = [user_list]

                    for u in user_list:
                        email = (
                            u.get("primaryEmailAddress")
                            or u.get("mailboxAddress")
                            or u.get("email")
                            or u.get("emailAddress")
                            or u.get("accountName", "")
                        ).lower().strip()

                        if not email or "@" not in email:
                            continue

                        # Extract aliases
                        raw_aliases = u.get("aliasList") or u.get("aliases") or []
                        aliases = []
                        if isinstance(raw_aliases, list):
                            for a in raw_aliases:
                                if isinstance(a, dict):
                                    ae = a.get("aliasEmail") or a.get("email")
                                    if ae:
                                        aliases.append(ae.lower().strip())
                                elif isinstance(a, str):
                                    aliases.append(a.lower().strip())

                        storage_bytes = self._parse_storage_bytes(u)

                        acc_id = str(u.get("accountId") or u.get("account_id") or u.get("zuid") or "")
                        first_name = u.get("firstName") or ""
                        last_name = u.get("lastName") or ""
                        disp_name = (
                            u.get("displayName")
                            or f"{first_name} {last_name}".strip()
                            or u.get("accountName")
                            or email.split("@")[0]
                        )

                        user_obj = ZohoUser(
                            zuid=str(u.get("zuid") or acc_id),
                            email=email,
                            first_name=first_name,
                            last_name=last_name,
                            display_name=disp_name,
                            role=u.get("role") or ("admin" if u.get("isAdmin") else "member"),
                            is_active=bool(u.get("accountStatus", u.get("isActive", True))),
                            aliases=aliases,
                            mailbox_account_id=acc_id,
                            storage_used_bytes=storage_bytes,
                        )
                        collected.append(user_obj)

                    if len(user_list) < limit:
                        break
                    start_index += limit

                if collected:
                    users = collected
                    logger.info(f"Discovered {len(users)} organization users from Zoho endpoint: {ep_base}")
                    break
            except ZohoClientError as err:
                logger.debug(f"Zoho endpoint {ep_base} returned error: {err}. Trying next candidate.")
                continue

        logger.info(f"Discovered {len(users)} organization users from Zoho Directory.")
        return users

    # --- Mailbox & Folders ---

    def list_user_folders(self, account_id: str, *args, **kwargs) -> List[MailFolder]:
        """Lists mail folders for a given Zoho account."""
        if args and str(args[0]).isdigit():
            account_id = str(args[0])
        elif kwargs.get("account_id"):
            account_id = str(kwargs["account_id"])
        elif kwargs.get("mailbox_account_id"):
            account_id = str(kwargs["mailbox_account_id"])

        resp = self._api_request(f"/api/accounts/{account_id}/folders")
        folder_list = resp.get("data", [])
        folders: List[MailFolder] = []

        for f in folder_list:
            count = int(f.get("totalCount") or f.get("messageCount") or f.get("count") or 0)
            unread = int(f.get("unreadCount") or f.get("unread") or 0)
            folders.append(MailFolder(
                folder_id=str(f.get("folderId", "")),
                folder_name=f.get("folderName", ""),
                folder_path=f.get("folderPath", f.get("folderName", "")),
                message_count=count,
                unread_count=unread,
            ))

        return folders

    def list_folder_messages(self, account_id: str = "", folder_id: str = "", start: int = 1, limit: int = 100, *args, **kwargs) -> List[MailMessageMeta]:
        """Lists message headers/metadata in a folder."""
        if kwargs.get("account_id"):
            account_id = str(kwargs["account_id"])
        if kwargs.get("folder_id"):
            folder_id = str(kwargs["folder_id"])
        if kwargs.get("start"):
            start = int(kwargs["start"])
        if kwargs.get("limit"):
            limit = int(kwargs["limit"])

        resp = self._api_request(f"/api/accounts/{account_id}/folders/{folder_id}/messages?start={start}&limit={limit}")
        msg_list = resp.get("data", [])
        messages: List[MailMessageMeta] = []

        for m in msg_list:
            messages.append(MailMessageMeta(
                message_id=str(m.get("messageId", "")),
                folder_id=folder_id,
                subject=m.get("subject", "(No Subject)"),
                sender=m.get("sender", ""),
                received_time_ms=int(m.get("receivedTime", time.time() * 1000)),
                size_bytes=int(m.get("size", 0)),
                is_read=bool(m.get("status") != "unread"),
                has_attachment=bool(m.get("hasAttachment", False)),
            ))

        return messages

    def stream_raw_message_rfc822(self, account_id: str = "", message_id: str = "", *args, **kwargs) -> bytes:
        """
        Streams raw RFC822 / MIME message data in memory.
        Zero disk persistence.
        """
        if kwargs.get("account_id"):
            account_id = str(kwargs["account_id"])
        if kwargs.get("message_id"):
            message_id = str(kwargs["message_id"])

        endpoint = f"/api/accounts/{account_id}/messages/{message_id}/content"
        raw_rfc822 = self._api_request(endpoint, raw_response=True)
        return raw_rfc822

    # --- Calendar Events ---

    def list_calendar_events(self, account_id: str = "", user_email: Optional[str] = None, *args, **kwargs) -> List[CalendarEvent]:
        """Fetches user calendar events."""
        if args and str(args[0]).isdigit():
            account_id = str(args[0])
        if kwargs.get("account_id"):
            account_id = str(kwargs["account_id"])
        if kwargs.get("user_email"):
            user_email = str(kwargs["user_email"])
        if not user_email and "@" in account_id:
            user_email = account_id

        endpoint = f"/api/accounts/{account_id}/events"
        try:
            resp = self._api_request(endpoint)
            events_data = resp.get("data", [])
            events: List[CalendarEvent] = []

            for ev in events_data:
                events.append(CalendarEvent(
                    event_id=str(ev.get("eventId", "")),
                    title=ev.get("title", "Untitled Event"),
                    start_time=ev.get("startTime", ""),
                    end_time=ev.get("endTime", ""),
                    is_all_day=bool(ev.get("isAllDay", False)),
                    description=ev.get("description"),
                    location=ev.get("location"),
                    recurrence=ev.get("recurrence", []),
                    attendees=[a.get("email") for a in ev.get("attendees", []) if a.get("email")],
                    organizer=ev.get("organizer", user_email or ""),
                ))
            return events
        except ZohoClientError as e:
            if "URL_RULE_NOT_CONFIGURED" in str(e) or getattr(e, "status_code", None) == 404:
                raise ZohoEndpointUnsupportedError(f"Calendar endpoint is not configured on Zoho Mail API for {account_id}: {e}") from e
            logger.warning(f"Could not fetch calendar events for account {account_id}: {e}")
            return []

    # --- Contacts ---

    def list_contacts(self, account_id: str = "", *args, **kwargs) -> List[ContactRecord]:
        """Fetches user address book contacts."""
        if args and str(args[0]).isdigit():
            account_id = str(args[0])
        if kwargs.get("account_id"):
            account_id = str(kwargs["account_id"])

        endpoint = f"/api/accounts/{account_id}/contacts"
        try:
            resp = self._api_request(endpoint)
            contacts_data = resp.get("data", [])
            contacts: List[ContactRecord] = []

            for c in contacts_data:
                first = c.get("firstName", "")
                last = c.get("lastName", "")
                display = c.get("displayName", f"{first} {last}".strip()) or "Unknown Contact"

                emails = [e.get("emailAddress") for e in c.get("emailList", []) if e.get("emailAddress")]
                if not emails and c.get("email"):
                    emails.append(c.get("email"))

                phones = [p.get("phoneNo") for p in c.get("phoneList", []) if p.get("phoneNo")]

                contacts.append(ContactRecord(
                    contact_id=str(c.get("contactId", "")),
                    first_name=first,
                    last_name=last,
                    display_name=display,
                    email_addresses=emails,
                    phone_numbers=phones,
                    company=c.get("companyName"),
                    job_title=c.get("jobTitle"),
                    notes=c.get("notes"),
                ))
            return contacts
        except ZohoClientError as e:
            if "URL_RULE_NOT_CONFIGURED" in str(e) or getattr(e, "status_code", None) == 404:
                raise ZohoEndpointUnsupportedError(f"Contacts endpoint is not configured on Zoho Mail API for {account_id}: {e}") from e
            logger.warning(f"Could not fetch contacts for account {account_id}: {e}")
            return []
