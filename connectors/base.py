"""
Data models for Zoho and Google Workspace migration objects.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any


@dataclass
class ZohoUser:
    """Represents a user discovered from Zoho Directory."""
    zuid: str
    email: str
    first_name: str
    last_name: str
    display_name: str
    role: str
    is_active: bool
    aliases: List[str] = field(default_factory=list)
    mailbox_account_id: Optional[str] = None
    storage_used_bytes: int = 0


@dataclass
class MailFolder:
    """Represents a mailbox folder."""
    folder_id: str
    folder_name: str
    folder_path: str
    message_count: int = 0
    unread_count: int = 0


@dataclass
class MailMessageMeta:
    """Metadata for an email message."""
    message_id: str
    folder_id: str
    subject: str
    sender: str
    received_time_ms: int
    size_bytes: int
    is_read: bool = True
    has_attachment: bool = False


@dataclass
class CalendarEvent:
    """Represents a calendar event."""
    event_id: str
    title: str
    start_time: str      # ISO format or YYYY-MM-DD
    end_time: str        # ISO format or YYYY-MM-DD
    is_all_day: bool = False
    description: Optional[str] = None
    location: Optional[str] = None
    recurrence: List[str] = field(default_factory=list)
    attendees: List[str] = field(default_factory=list)
    organizer: Optional[str] = None


@dataclass
class ContactRecord:
    """Represents a contact in the user's address book."""
    contact_id: str
    first_name: str
    last_name: str
    display_name: str
    email_addresses: List[str] = field(default_factory=list)
    phone_numbers: List[str] = field(default_factory=list)
    company: Optional[str] = None
    job_title: Optional[str] = None
    notes: Optional[str] = None
