# Zoho to Google Workspace Enterprise Migration Agent (Atomic Architecture)

A secure, automated, and passwordless migration agent built for Zoho Global Administrators to migrate organization users, mailboxes, contacts, and calendars to Google Workspace without collecting or resetting individual user passwords.

---

## 🏗️ Atomic Agent Architecture

The agent is organized around the **Atomic Agent Pattern**—decoupling complex enterprise migration operations into 6 single-responsibility, bounded-execution agents coordinated by a master `MigrationSupervisor`:

```
                           ┌──────────────────────────────────────────────┐
                           │             MigrationSupervisor              │
                           │   • Ephemeral AES-256 Vault Session          │
                           │   • Checkpoint State Coordination            │
                           │   • Granular Error & Fault Boundaries        │
                           └──────────────────────┬───────────────────────┘
                                                  │
         ┌──────────────────┬─────────────────────┼─────────────────────┬──────────────────┐
         │                  │                     │                     │                  │
         ▼                  ▼                     ▼                     ▼                  ▼
┌──────────────────┐ ┌──────────────┐   ┌───────────────────┐ ┌───────────────────┐ ┌──────────────┐
│ SecurityAuditor  │ │  Discovery   │   │ UserProvisioning  │ │ CalendarMigration │ │ MailboxStream│
│      Agent       │ │    Agent     │   │       Agent       │ │       Agent       │ │    Agent     │
│ • Scope Auditing │ │ • Topology   │   │ • Account Creation│ │ • Event Sync      │ │ • In-Memory  │
│ • Key Validation │ │ • Sizing     │   │ • Temp Passwords  │ │ • RRULE Mapping   │ │   RFC822     │
│ • DWD Delegation │ │ • Pilot AI   │   │ • Alias Mapping   │ │ • Checkpointing   │ │ • 25MB Check │
└──────────────────┘ └──────────────┘   └───────────────────┘ └───────────────────┘ └──────────────┘
                                                  │
                                                  ▼
                                        ┌───────────────────┐
                                        │ ContactsMigration │
                                        │       Agent       │
                                        │ • People API Sync │
                                        │ • Deduplication   │
                                        └───────────────────┘
```

### 1. `SecurityAuditorAgent`
- **Responsibility**: Scope compliance and pre-flight verification.
- **Enforcement**: Validates Zoho regional TLDs, parses Google Service Account JSON, validates RSA keys, enforces strictly read-only OAuth scopes (rejecting any write/delete scopes), and tests live Domain-Wide Delegation.

### 2. `DiscoveryAssessmentAgent`
- **Responsibility**: Organization topology assessment & volumetric estimation.
- **Intelligence**: Scans all users, mailbox sizes, message counts, calendars, and contacts. Calculates a risk-weighted score and recommends the safest pilot cohort (users with small storage footprints and non-admin roles).

### 3. `UserProvisioningAgent`
- **Responsibility**: Idempotent Google Workspace user creation.
- **Features**: Generates 20-character cryptographically random passwords, assigns aliases, sets `changePasswordAtNextLogin: true`, and produces a one-time admin credential export (`provisioned_credentials_<timestamp>.csv`).

### 4. `CalendarMigrationAgent`
- **Responsibility**: Google Calendar synchronization.
- **Features**: Translates Zoho calendar events, handles ISO 8601 UTC timestamps, normalizes iCalendar RRULE recurrences, maps attendees, and isolates errors per event.

### 5. `ContactsMigrationAgent`
- **Responsibility**: Google Contacts & Address Book synchronization.
- **Features**: Converts Zoho address books to Google People API schemas, normalizes phone numbers and emails, and deduplicates contacts.

### 6. `MailboxStreamingAgent`
- **Responsibility**: Direct in-memory RFC822 email streaming.
- **Guardrails**: Maps custom Zoho folders to nested Gmail labels, streams RFC822 bodies directly through memory buffers (0 disk writes), enforces Gmail's 25MB per-message import limit, and paces API traffic via token-bucket rate limiters (~2.5 msgs/sec per user).

---

## 🛡️ Security Architecture & Guardrail Matrix

Because global admin credentials hold elevated access across both organizations, the agent is engineered around strict zero-trust and least-privilege security principles:

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

### 3. Run Migration (Local Web UI or CLI)

#### 🌟 Option A: Local Web User Interface (Recommended for non-technical users)
Run the self-contained local web dashboard (runs completely offline on `127.0.0.1`):
```bash
python3 ui.py
# or
python3 main.py --ui
```
This opens your browser at `http://127.0.0.1:8080` with a 5-step visual wizard:
1. **Setup & Credentials**: Zoho OAuth fields + Drag & drop Google Service Account JSON.
2. **Security Pre-Flight**: 1-click automated scope & DWD diagnostics with visual checkmarks.
3. **Discovery Dashboard**: Visual metrics for user counts, message volume, GBs, and pilot recommendations.
4. **Scope Selection**: 1-click Pilot presets (1-user pilot, 5-user pilot, full org, or searchable table) + Dry-run simulation toggle.
5. **Live Migration & Progress**: Real-time progress bar, stage timeline, live log stream, and 1-click password CSV / audit JSON downloads.

#### Option B: Interactive CLI Mode (Prompt will ask for Pilot or Full Migration)
```bash
python3 main.py
```
The agent scans all users and displays an interactive scope menu:
```text
Select migration scope:
  [1] Migrate ALL organization users (Full Migration)
  [2] Quick Pilot Test: Migrate first 1 user
  [3] Quick Pilot Test: Migrate first 5 users
  [4] Select specific users by Email / UPN (comma-separated)
  [5] Select users by list index numbers (e.g. 1, 3, 5)
```

#### Option C: Target Specific Users by Email (UPN)
```bash
python3 main.py --users alice@yourcompany.com,bob@yourcompany.com
```

#### Option D: Run a Quick Pilot Test (First N Users)
```bash
python3 main.py --pilot 5
```

#### Option E: Target Users from a Text File
```bash
python3 main.py --users-file pilot_users.txt
```

---

## ☁️ Deploying to Google Cloud Run (Serverless Web App)

The agent is fully containerized as a lightweight serverless web app (<80 MB container) with zero external Node.js/npm dependencies:

### 1. One-Click Deploy via Google Cloud SDK
```bash
chmod +x deploy_cloudrun.sh
./deploy_cloudrun.sh
```
Or deploy directly with `gcloud`:
```bash
gcloud run deploy zoho-gw-migration-agent \
    --source . \
    --region asia-southeast2 \
    --platform managed \
    --cpu 1 \
    --memory 2Gi \
    --timeout 3600 \
    --no-allow-unauthenticated
```

### 2. Run via Docker Locally
```bash
# Build lightweight Docker image
docker build -t zoho-gw-migration-agent .

# Run container on port 8080
docker run -d -p 8080:8080 --name migration-agent zoho-gw-migration-agent
```
Open `http://localhost:8080` in your browser.

---

### 4. Interactive Live Pause, Resume & Laptop Mobility
When running locally on an administrator machine, you can freely pause migration to relocate the computer:
- **Web UI**: Click the **"⏸️ Pause Migration"** button at any time. The progress bar transitions to a paused striped state and worker threads halt safely at their current item boundary. Click **"▶️ Resume Migration"** once settled to continue without missing or duplicating any records.
- **CLI**: The supervisor listens for signals and handles pause/resume state transitions safely.

### 5. Automatic Network Loss Watchdog & Recovery Failsafe
If the internet connection drops or the Wi-Fi disconnects during active migration:
- **Automatic Pause**: The `NetworkWatchdog` detects socket/network errors (`socket.gaierror`, `ConnectionResetError`, `URLError`, etc.) and immediately suspends all atomic agent worker loops and backoff retry counters to avoid burning through API quota or retry budgets.
- **Background Health Probes**: The watchdog silently probes Google and Zoho gateway endpoints (`8.8.8.8:53` DNS sockets and HTTP 204 endpoints) every 3 seconds.
- **Self-Healing Resume**: Once two consecutive successful connection probes are verified, the agent automatically restores the pipeline and unblocks all streaming threads with zero data loss or corrupt entries.

### 6. Resumability & Checkpointing
If the process is terminated or cancelled, re-run with the checkpoint database:
```bash
python3 main.py --checkpoint-db migration_checkpoint.db
```
The agent checks SHA-256 nonces and skips already synced users, emails, contacts, and calendar events.

---

## 📊 Migration Workflow Stages

1. **Pre-flight Security Audit**: `SecurityAuditorAgent` verifies tokens, scopes, and tenant endpoints.
2. **Organization Discovery & Volume Assessment**: `DiscoveryAssessmentAgent` estimates volume and recommends low-risk pilot cohorts.
3. **Stage 1: User Provisioning**: `UserProvisioningAgent` provisions Google Workspace accounts, aliases, and generates temporary passwords.
4. **Stage 2: Calendar Migration**: `CalendarMigrationAgent` imports calendar events and attendee relations.
5. **Stage 3: Contacts Migration**: `ContactsMigrationAgent` imports user address books via Google People API.
6. **Stage 4: Mailbox Streaming**: `MailboxStreamingAgent` streams RFC822 messages directly in memory to Gmail labels.
7. **Audit & Report**: Supervisor exports `migration_audit_report_<timestamp>.json` with full entity metrics.

---

## 🧪 Running Automated Tests

```bash
python3 -m unittest discover -s tests -v
```
Test suite verifies:
- `test_atomic_agents.py`: Isolated testing for all 6 atomic agents, error boundaries, and security policies.
- `test_pause_controller.py`: PauseController thread-safe synchronization, NetworkWatchdog disconnect classification, recovery probing, and UI pause/resume/cancel endpoints.
- `test_vault.py`: In-memory encryption, TTL expiration, and zero-disk purging.
- `test_sanitizer.py`: Regex secret and token redaction across stdout and logs.
- `test_validator.py`: Scope auditing and rejection of destructive permissions.
- `test_checkpoint.py`: SQLite state tracking and idempotency.
- `test_rate_limiter.py`: Token bucket rate limiting and exponential backoff.
- `test_user_filtering.py`: Target user selection, pilot scoping, and interactive menus.
- `test_ui_server.py`: Local Web UI server routes, REST endpoints, static assets, and in-memory mock requests.
- `test_zoho_client.py`: Mocked Zoho OAuth and API operations.
- `test_google_client.py`: Mocked Google DWD, user provisioning, Gmail, Calendar, and People APIs.
- `test_end_to_end_mock.py`: Full multi-stage migration pipeline simulation.
