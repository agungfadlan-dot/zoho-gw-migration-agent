# Zoho to Google Workspace Enterprise Migration Agent

A secure, automated, and passwordless migration agent built for Zoho Global Administrators to migrate organization users, mailboxes, contacts, and calendars to Google Workspace without collecting or resetting individual user passwords.

---

## 🛡️ Security Architecture & Guardrail Matrix

Because global admin credentials hold elevated access across both organizations, the agent is engineered around strict zero-trust and least-privilege security principles:

flowchart TD
    subgraph AdminWorkstation["Admin Local Workstation (Isolated Security Enclave)"]
        MaskedPrompt[Masked Interactive Ingestion] --> Vault["AES-256-GCM Ephemeral Memory Vault<br/>• Session TTL<br/>• Zero Plaintext on Disk<br/>• Explicit Memory Zeroing"]
        Vault --> ScopeAuditor["Pre-Flight Scope & DC Verifier<br/>• Rejects Write/Delete Scopes<br/>• Validates Regional Endpoints"]
        ScopeAuditor --> Pipeline["In-Memory Direct Streaming Buffer<br/>• Zero RFC822 Disk Spooling"]
        Pipeline --> Sanitizer["Regex Output & Log Sanitizer<br/>• Strips Tokens, Private Keys, PII"]
        Pipeline --> Checkpoint[("(Checkpoint Store SQLite)<br/>• Nonces, Checksums & Status Only")]
    end

    subgraph Zoho["Zoho Organization"]
        ScopeAuditor -->|Zoho OAuth 2.0 Read Scopes| ZAPI[Zoho Mail & Directory Admin API]
        ZAPI --> Pipeline
    end

    subgraph Google["Google Workspace Domain"]
        Pipeline -->|Service Account + Domain-Wide Delegation| GAPI["Google Workspace APIs<br/>Admin SDK + Gmail API + People + Calendar"]
    end

### Core Security Guarantees:
1. **Passwordless Admin-to-Admin Migration**:
   - **Zoho Source**: Admin authorizes an OAuth 2.0 app with read-only organization scopes.
   - **Google Destination**: Google Cloud Service Account with **Domain-Wide Delegation (DWD)** impersonates destination users via JWT assertions—no individual Google or Zoho user passwords are required.
2. **Zero Plaintext Persistence to Disk**:
   - Tokens, client secrets, and private keys reside exclusively in memory protected by an ephemeral AES-256-GCM session key.
   - On exit, shutdown, or 2-hour TTL expiration, all sensitive memory buffers are explicitly zeroed out.
3. **Automated Least-Privilege Scope Auditor**:
   - Pre-flight checks audit OAuth scopes to ensure no destructive (`DELETE`, `WRITE`, `MANAGE`) scopes are present on Zoho.
4. **Real-Time Stream Redaction & Logging Filter**:
   - All console output, logging streams, and error handlers pass through regex redaction filters that scrub Bearer tokens, Zoho tokens, Google tokens, RSA private keys, and sensitive JSON keys.
5. **Direct Memory Streaming (Zero Disk Spooling)**:
   - Email RFC822 messages, contact payloads, and calendar events are streamed directly across network buffers from Zoho API to Google Workspace API without temporary files on disk.

---

## 📦 Prerequisites & Admin Setup

### 1. Zoho API Console Setup (Source)
1. Go to the [Zoho API Console](https://api-console.zoho.com).
2. Choose **Server-based Applications** or **Self-Client**.
3. Set the following **Read-Only Scopes**:
   - `ZohoMail.organization.accounts.READ`
   - `ZohoMail.accounts.READ`
   - `ZohoMail.messages.READ`
   - `ZohoDirectory.user.READ`
   - `ZohoCalendar.event.READ`
   - `ZohoContacts.user.READ`
4. Generate the **Client ID**, **Client Secret**, and **Refresh Token** (with `offline` access).

### 2. Google Cloud Service Account & DWD Setup (Destination)
1. Go to [Google Cloud Console](https://console.cloud.google.com).
2. Create a Project and enable:
   - **Admin SDK API**
   - **Gmail API**
   - **Google Calendar API**
   - **People API**
3. Create a **Service Account** and generate a **JSON Key**.
4. In the Service Account details, enable **Domain-Wide Delegation (DWD)** and note the **Client ID (OAuth 2 Client ID)**.
5. Go to [Google Workspace Admin Console](https://admin.google.com) > **Security** > **Access and data control** > **API controls** > **Domain-wide Delegation**.
6. Add API Client with the Service Account Client ID and grant the following OAuth Scopes:
   ```
   https://www.googleapis.com/auth/admin.directory.user,
   https://www.googleapis.com/auth/gmail.insert,
   https://www.googleapis.com/auth/gmail.labels,
   https://www.googleapis.com/auth/calendar.events,
   https://www.googleapis.com/auth/contacts
   ```

---

## 🚀 Installation & Usage

### 1. Requirements
- Python 3.9+
- Optional: `cryptography` (for hardware-accelerated AES-GCM and RSA JWT signing)

```bash
cd zoho-gw-migration-agent
pip install -r requirements.txt
```

### 2. Run Dry-Run Simulation (Recommended First Step)
Simulates discovery, scope validation, and migration planning without writing to Google Workspace:
```bash
python3 main.py --dry-run
```

### 3. Run Live Migration
```bash
python3 main.py
```

### 4. Resumability & Checkpointing
If a network interruption occurs, re-run the command with the existing checkpoint database:
```bash
python3 main.py --checkpoint-db migration_checkpoint.db
```
The agent checks SHA-256 nonces and skips already synced users, emails, contacts, and calendar events.

---

## 📊 Migration Workflow Stages

1. **Pre-flight Validation**: Tests connectivity, validates scopes, and ensures tenant data center regional isolation.
2. **Organization Discovery**: Analyzes directory users, mailbox sizes, estimated message counts, calendar events, and contacts.
3. **Stage 1: User Provisioning**: Auto-creates Google Workspace accounts for discovered Zoho users, configures aliases, generates cryptographically strong random passwords, and enforces `changePasswordAtNextLogin: true`. Generates a one-time admin credential file (`provisioned_credentials_<timestamp>.csv`).
4. **Stage 2: Calendar Migration**: Imports user calendar events, recurrence rules, and attendee relationships.
5. **Stage 3: Contacts Migration**: Imports user address books via Google People API.
6. **Stage 4: Mailbox Streaming**: Synchronizes folder structures into Gmail labels and streams RFC822 messages directly in memory.
7. **Audit & Report**: Generates `migration_audit_report_<timestamp>.json` with full entity-by-entity metrics.

---

## 🧪 Running Automated Tests

```bash
python3 -m unittest discover -s tests -v
```
Test suite verifies:
- `test_vault.py`: In-memory encryption, TTL expiration, and zero-disk purging.
- `test_sanitizer.py`: Regex secret and token redaction across stdout and logs.
- `test_validator.py`: Scope auditing and rejection of destructive permissions.
- `test_checkpoint.py`: SQLite state tracking and idempotency.
- `test_rate_limiter.py`: Token bucket rate limiting and exponential backoff.
- `test_zoho_client.py`: Mocked Zoho OAuth and API operations.
- `test_google_client.py`: Mocked Google DWD, user provisioning, Gmail, Calendar, and People APIs.
- `test_end_to_end_mock.py`: Full multi-stage migration pipeline simulation.
