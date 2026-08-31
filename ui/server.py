"""
Local Web UI Server for Zoho to Google Workspace Migration Agent.
Runs strictly on 127.0.0.1 (localhost) with multi-threaded request handling.
"""

import os
import sys
import json
import time
import queue
import threading
import mimetypes
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Optional, Dict, Any, List
from dataclasses import is_dataclass, asdict

from security.vault import EphemeralVault
from security.sanitizer import setup_secure_logger
from atomic_agents.supervisor import MigrationSupervisor
from engine.pause_controller import PauseController, PauseState

logger = setup_secure_logger("ui_server")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Multi-threaded HTTP server to handle SSE and API calls concurrently."""
    daemon_threads = True
    allow_reuse_address = True


class MigrationUIHandler(SimpleHTTPRequestHandler):
    """Custom HTTP Request Handler for Migration Web UI."""

    # Shared server context
    vault: EphemeralVault = EphemeralVault()
    supervisor: Optional[MigrationSupervisor] = None
    progress_queue: queue.Queue = queue.Queue(maxsize=1000)
    last_progress_state: Dict[str, Any] = {
        "stage": "IDLE",
        "stage_name": "Ready",
        "percent": 0,
        "current_user": "",
        "item_current": 0,
        "item_total": 0,
        "detail": "Ready to begin migration",
        "log_messages": [],
        "is_running": False,
        "is_completed": False,
        "is_paused": False,
        "pause_state": "RUNNING",
        "pause_reason": "",
        "network_online": True,
        "summary": None,
        "error": None
    }
    latest_credentials_csv: Optional[str] = None
    latest_audit_report_json: Optional[str] = None
    discovered_users_cache: List[Any] = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def log_message(self, format, *args):
        """Silence default noisy access logs, route to secure logger."""
        logger.debug("%s - - [%s] %s" % (self.client_address[0], self.log_date_time_string(), format % args))

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            self.close_connection = True

    def _send_json_response(self, status_code: int, data: Any):
        """Helper to send JSON response with security headers."""
        try:
            body = json.dumps(data).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            self.close_connection = True

    def _send_error_json(self, status_code: int, error_msg: str):
        self._send_json_response(status_code, {"success": False, "error": error_msg})

    def _read_json_body(self) -> Dict[str, Any]:
        """Parses JSON request body."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            return {}
        raw = self.rfile.read(content_length).decode("utf-8")
        return json.loads(raw)

    def do_OPTIONS(self):
        """Handles CORS preflight for localhost."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        """Handles static file serving and GET API endpoints."""
        path = self.path.split("?")[0]

        if path == "/api/status":
            self._send_json_response(200, {
                "success": True,
                "has_credentials": bool(self.vault.retrieve("zoho_client_id")),
                "progress": self.last_progress_state
            })
            return

        elif path == "/api/progress/stream":
            self._handle_sse_stream()
            return

        elif path == "/api/progress/poll":
            self._send_json_response(200, {"success": True, "data": self.last_progress_state})
            return

        elif path == "/api/download/credentials":
            self._handle_download_credentials()
            return

        elif path == "/api/download/report":
            self._handle_download_report()
            return

        # Serve static assets (index.html, styles.css, app.js)
        if path == "/":
            self.path = "/index.html"

        super().do_GET()

    def do_POST(self):
        """Handles REST API actions."""
        path = self.path.split("?")[0]

        try:
            if path == "/api/vault/store":
                self._handle_vault_store()
            elif path == "/api/preflight":
                self._handle_preflight()
            elif path == "/api/discover":
                self._handle_discover()
            elif path == "/api/migrate":
                self._handle_migrate()
            elif path == "/api/pause":
                self._handle_pause()
            elif path == "/api/resume":
                self._handle_resume()
            elif path == "/api/cancel":
                self._handle_cancel()
            elif path == "/api/reset":
                self._handle_reset()
            else:
                self._send_error_json(404, f"Endpoint '{path}' not found")
        except Exception as e:
            logger.error(f"Error handling {path}: {e}")
            self._send_error_json(500, str(e))

    def _handle_vault_store(self):
        """Ingests and validates credentials into EphemeralVault."""
        data = self._read_json_body()
        zoho_client_id = data.get("zoho_client_id", "").strip()
        zoho_client_secret = data.get("zoho_client_secret", "").strip()
        zoho_refresh_token = data.get("zoho_refresh_token", "").strip()
        zoho_domain = data.get("zoho_domain", "zoho.com").strip()
        google_sa_json = data.get("google_sa_json", "").strip()
        google_admin_email = data.get("google_admin_email", "").strip()

        if not zoho_client_id or not zoho_client_secret or not zoho_refresh_token:
            self._send_error_json(400, "Missing required Zoho OAuth credentials.")
            return

        if not google_sa_json or not google_admin_email:
            self._send_error_json(400, "Missing required Google Service Account key or Admin Email.")
            return

        # Store encrypted in memory
        self.vault.store("zoho_client_id", zoho_client_id)
        self.vault.store("zoho_client_secret", zoho_client_secret)
        self.vault.store("zoho_refresh_token", zoho_refresh_token)
        self.vault.store("zoho_domain", zoho_domain)
        self.vault.store("google_sa_json", google_sa_json)
        self.vault.store("google_admin_email", google_admin_email)

        # Initialize or update supervisor
        checkpoint_db = data.get("checkpoint_db", "migration_checkpoint.db")
        self.supervisor = MigrationSupervisor(vault=self.vault, checkpoint_db=checkpoint_db, dry_run=False)

        self._send_json_response(200, {
            "success": True,
            "message": "Credentials successfully stored in encrypted session vault."
        })

    def _handle_preflight(self):
        """Executes Pre-flight Security & Connectivity Audit."""
        if not self.supervisor:
            domain = self.vault.retrieve("zoho_domain") or "zoho.com"
            self.supervisor = MigrationSupervisor(vault=self.vault)

        domain = self.vault.retrieve("zoho_domain") or "zoho.com"
        try:
            report = self.supervisor.run_security_audit(zoho_domain=domain)
            report_data = report.to_dict() if hasattr(report, "to_dict") else asdict(report)
            self._send_json_response(200, {
                "success": True,
                "data": report_data
            })
        except Exception as e:
            self._send_json_response(200, {
                "success": False,
                "error": str(e),
                "data": {
                    "is_compliant": False,
                    "errors": [str(e)],
                    "checks_passed": [],
                    "warnings": []
                }
            })

    def _handle_discover(self):
        """Executes Organization Discovery & Volume Assessment."""
        if not self.supervisor:
            self.supervisor = MigrationSupervisor(vault=self.vault)

        domain = self.vault.retrieve("zoho_domain") or "zoho.com"
        try:
            discovery_res = self.supervisor.run_discovery(zoho_domain=domain, sample_items=True)
            report = discovery_res.report
            
            # Cache discovered users for migration lookups
            zoho_client = self.supervisor.get_zoho_client(domain=domain)
            MigrationUIHandler.discovered_users_cache = zoho_client.list_organization_users()

            recommended_emails = [u.email for u in discovery_res.recommended_pilot_cohort]

            self._send_json_response(200, {
                "success": True,
                "data": {
                    "report": report.to_dict(),
                    "recommended_pilot_cohort": recommended_emails
                }
            })
        except Exception as e:
            self._send_error_json(500, f"Discovery assessment failed: {e}")

    def _handle_pause(self):
        """Pauses the running migration."""
        data = self._read_json_body()
        reason = data.get("reason", "Manual pause requested by administrator")
        if MigrationUIHandler.supervisor and MigrationUIHandler.last_progress_state.get("is_running"):
            MigrationUIHandler.supervisor.pause(reason)
            self._send_json_response(200, {
                "success": True,
                "message": "Migration paused successfully.",
                "pause_state": MigrationUIHandler.supervisor.pause_state
            })
        else:
            self._send_error_json(400, "No active migration running to pause.")

    def _handle_resume(self):
        """Resumes the paused migration."""
        if MigrationUIHandler.supervisor and MigrationUIHandler.last_progress_state.get("is_running"):
            MigrationUIHandler.supervisor.resume()
            self._send_json_response(200, {
                "success": True,
                "message": "Migration resumed successfully.",
                "pause_state": MigrationUIHandler.supervisor.pause_state
            })
        else:
            self._send_error_json(400, "No active migration running to resume.")

    def _handle_cancel(self):
        """Cancels the running migration."""
        if MigrationUIHandler.supervisor and MigrationUIHandler.last_progress_state.get("is_running"):
            MigrationUIHandler.supervisor.cancel("Migration cancelled by administrator.")
            self._send_json_response(200, {
                "success": True,
                "message": "Migration cancellation initiated.",
                "pause_state": MigrationUIHandler.supervisor.pause_state
            })
        else:
            self._send_error_json(400, "No active migration running to cancel.")

    def _handle_migrate(self):
        """Starts live or simulated migration in background thread."""
        if MigrationUIHandler.last_progress_state["is_running"]:
            self._send_error_json(400, "A migration run is already currently in progress.")
            return

        body = self._read_json_body()
        dry_run = bool(body.get("dry_run", False))
        target_emails = body.get("target_emails", [])  # Empty means all users
        skip_calendar = bool(body.get("skip_calendar", False))
        skip_contacts = bool(body.get("skip_contacts", False))
        skip_mailbox = bool(body.get("skip_mailbox", False))

        def on_pause_state_change(state: PauseState, reason: str):
            is_p = state in (PauseState.PAUSED_MANUAL, PauseState.PAUSED_NETWORK_LOST)
            MigrationUIHandler.last_progress_state["is_paused"] = is_p
            MigrationUIHandler.last_progress_state["pause_state"] = state.value
            MigrationUIHandler.last_progress_state["pause_reason"] = reason
            MigrationUIHandler.last_progress_state["network_online"] = (state != PauseState.PAUSED_NETWORK_LOST)
            if is_p:
                MigrationUIHandler.last_progress_state["log_messages"].append(f"[PAUSED] {reason}")
            elif state == PauseState.RUNNING:
                MigrationUIHandler.last_progress_state["log_messages"].append(f"[RESUMED] Migration resumed.")

        pause_ctrl = PauseController(on_state_change=on_pause_state_change)

        if not self.supervisor:
            self.supervisor = MigrationSupervisor(vault=self.vault, dry_run=dry_run, pause_controller=pause_ctrl)
        else:
            self.supervisor.dry_run = dry_run
            self.supervisor.pause_controller = pause_ctrl

        domain = self.vault.retrieve("zoho_domain") or "zoho.com"
        zoho_client = self.supervisor.get_zoho_client(domain=domain)

        all_users = MigrationUIHandler.discovered_users_cache
        if not all_users:
            all_users = zoho_client.list_organization_users()
            MigrationUIHandler.discovered_users_cache = all_users

        if target_emails:
            target_set = set(e.strip().lower() for e in target_emails)
            selected_users = [u for u in all_users if u.email.lower() in target_set or any(a.lower() in target_set for a in u.aliases)]
        else:
            selected_users = all_users

        if not selected_users:
            self._send_error_json(400, "No valid users selected for migration.")
            return

        # Reset progress state
        MigrationUIHandler.last_progress_state = {
            "stage": "STARTING",
            "stage_name": "Initializing Migration",
            "percent": 0,
            "current_user": "",
            "item_current": 0,
            "item_total": 0,
            "detail": f"Preparing to migrate {len(selected_users)} user(s) (Dry Run: {dry_run})",
            "log_messages": [f"Starting migration run for {len(selected_users)} user(s)..."],
            "is_running": True,
            "is_completed": False,
            "is_paused": False,
            "pause_state": "RUNNING",
            "pause_reason": "",
            "network_online": True,
            "summary": None,
            "error": None
        }

        def run_migration_worker():
            try:
                def progress_cb(email: str, current: int, total: int, detail: str):
                    pct = int((current / max(total, 1)) * 100) if total > 0 else 0
                    MigrationUIHandler.last_progress_state["current_user"] = email
                    MigrationUIHandler.last_progress_state["item_current"] = current
                    MigrationUIHandler.last_progress_state["item_total"] = total
                    MigrationUIHandler.last_progress_state["detail"] = detail
                    MigrationUIHandler.last_progress_state["percent"] = pct
                    msg = f"[{email}] {detail} ({current}/{total})"
                    MigrationUIHandler.last_progress_state["log_messages"].append(msg)
                    if len(MigrationUIHandler.last_progress_state["log_messages"]) > 200:
                        MigrationUIHandler.last_progress_state["log_messages"].pop(0)

                # Stage 1: User Provisioning
                MigrationUIHandler.last_progress_state["stage"] = "PROVISIONING"
                MigrationUIHandler.last_progress_state["stage_name"] = "Stage 1/4: User Provisioning"
                MigrationUIHandler.last_progress_state["log_messages"].append("Provisioning Google Workspace user accounts...")
                prov_summary = self.supervisor.run_stage_provisioning(selected_users)
                if prov_summary.credentials_csv_path:
                    MigrationUIHandler.latest_credentials_csv = prov_summary.credentials_csv_path

                # Filter active users that exist in Google Workspace
                if not dry_run:
                    user_status_map = {u["email"]: u["status"] for u in self.supervisor.checkpoint_store.get_all_users()}
                    active_users = [u for u in selected_users if user_status_map.get(u.email.lower().strip()) in ("CREATED", "EXISTING")]
                    if len(active_users) < len(selected_users):
                        skipped_count = len(selected_users) - len(active_users)
                        MigrationUIHandler.last_progress_state["log_messages"].append(
                            f"Notice: Proceeding with {len(active_users)} active Google Workspace account(s). "
                            f"({skipped_count} user(s) skipped due to missing Google Workspace licenses)."
                        )
                else:
                    active_users = selected_users

                # Stage 2: Calendar Migration
                if not skip_calendar:
                    MigrationUIHandler.last_progress_state["stage"] = "CALENDAR"
                    MigrationUIHandler.last_progress_state["stage_name"] = "Stage 2/4: Calendar Migration"
                    cal_summary = self.supervisor.run_stage_calendar(active_users, zoho_domain=domain, progress_callback=progress_cb)
                else:
                    from atomic_agents.calendar_agent import CalendarSyncSummary
                    cal_summary = CalendarSyncSummary(skipped=len(active_users))
                    MigrationUIHandler.last_progress_state["log_messages"].append("Calendar migration skipped by configuration.")

                # Stage 3: Contacts Migration
                if not skip_contacts:
                    MigrationUIHandler.last_progress_state["stage"] = "CONTACTS"
                    MigrationUIHandler.last_progress_state["stage_name"] = "Stage 3/4: Contacts Migration"
                    cont_summary = self.supervisor.run_stage_contacts(active_users, zoho_domain=domain, progress_callback=progress_cb)
                else:
                    from atomic_agents.contacts_agent import ContactsSyncSummary
                    cont_summary = ContactsSyncSummary(skipped=len(active_users))
                    MigrationUIHandler.last_progress_state["log_messages"].append("Contacts migration skipped by configuration.")

                # Stage 4: Mailbox Streaming
                if not skip_mailbox:
                    MigrationUIHandler.last_progress_state["stage"] = "MAILBOX"
                    MigrationUIHandler.last_progress_state["stage_name"] = "Stage 4/4: Mailbox Streaming"
                    mail_summary = self.supervisor.run_stage_mailbox(active_users, zoho_domain=domain, progress_callback=progress_cb)
                else:
                    from atomic_agents.mailbox_agent import MailboxSyncSummary
                    mail_summary = MailboxSyncSummary()
                    MigrationUIHandler.last_progress_state["log_messages"].append("Mailbox migration skipped by configuration.")

                # Generate JSON Audit Report
                audit_file = f"migration_audit_report_{int(time.time())}.json"
                audit_data = {
                    "mode": "DRY_RUN" if dry_run else "LIVE",
                    "timestamp": time.time(),
                    "users_count": len(selected_users),
                    "summary": {
                        "provisioning": prov_summary.to_dict(),
                        "calendar": cal_summary.to_dict(),
                        "contacts": cont_summary.to_dict(),
                        "mailbox": mail_summary.to_dict(),
                        "database_stats": self.supervisor.checkpoint_store.get_summary_stats()
                    }
                }
                with open(audit_file, "w", encoding="utf-8") as f:
                    json.dump(audit_data, f, indent=2)
                MigrationUIHandler.latest_audit_report_json = audit_file

                MigrationUIHandler.last_progress_state["stage"] = "COMPLETED"
                MigrationUIHandler.last_progress_state["stage_name"] = "Migration Completed"
                MigrationUIHandler.last_progress_state["percent"] = 100
                MigrationUIHandler.last_progress_state["is_running"] = False
                MigrationUIHandler.last_progress_state["is_completed"] = True
                MigrationUIHandler.last_progress_state["summary"] = audit_data["summary"]
                MigrationUIHandler.last_progress_state["log_messages"].append(">>> Migration completed successfully!")

            except Exception as e:
                logger.error(f"Migration background task failed: {e}")
                MigrationUIHandler.last_progress_state["stage"] = "FAILED"
                MigrationUIHandler.last_progress_state["stage_name"] = "Migration Failed"
                MigrationUIHandler.last_progress_state["is_running"] = False
                MigrationUIHandler.last_progress_state["error"] = str(e)
                MigrationUIHandler.last_progress_state["log_messages"].append(f"FATAL ERROR: {e}")

        # Start background thread
        thread = threading.Thread(target=run_migration_worker, daemon=True)
        thread.start()

        self._send_json_response(200, {
            "success": True,
            "message": f"Migration initiated for {len(selected_users)} user(s)."
        })

    def _handle_reset(self):
        """Resets migration state and vault."""
        MigrationUIHandler.vault.purge()
        MigrationUIHandler.vault = EphemeralVault()
        MigrationUIHandler.last_progress_state = {
            "stage": "IDLE",
            "stage_name": "Ready",
            "percent": 0,
            "current_user": "",
            "item_current": 0,
            "item_total": 0,
            "detail": "Ready",
            "log_messages": [],
            "is_running": False,
            "is_completed": False,
            "is_paused": False,
            "pause_state": "RUNNING",
            "pause_reason": "",
            "network_online": True,
            "summary": None,
            "error": None
        }
        self._send_json_response(200, {"success": True, "message": "State reset successfully."})

    def _handle_download_credentials(self):
        """Downloads temporary passwords CSV if generated."""
        csv_file = MigrationUIHandler.latest_credentials_csv
        if not csv_file or not os.path.isfile(csv_file):
            self._send_error_json(404, "No credential export file available.")
            return

        with open(csv_file, "rb") as f:
            content = f.read()

        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(csv_file)}"')
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _handle_download_report(self):
        """Downloads JSON audit report."""
        report_file = MigrationUIHandler.latest_audit_report_json
        if not report_file or not os.path.isfile(report_file):
            self._send_error_json(404, "No audit report available.")
            return

        with open(report_file, "rb") as f:
            content = f.read()

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(report_file)}"')
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _handle_sse_stream(self):
        """Streams real-time migration progress via Server-Sent Events (SSE)."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1")
        self.end_headers()

        try:
            while True:
                data = json.dumps(MigrationUIHandler.last_progress_state)
                self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(0.5)
        except (BrokenPipeError, ConnectionResetError):
            pass


def run_ui_server(host: str = "127.0.0.1", port: int = 8080, open_browser: bool = True):
    """Starts the local Web UI HTTP server and optionally launches browser."""
    # Ensure port availability or increment
    actual_port = port
    server = None
    for attempt in range(10):
        try:
            server = ThreadedHTTPServer((host, actual_port), MigrationUIHandler)
            break
        except OSError:
            actual_port += 1

    if not server:
        raise RuntimeError(f"Could not bind to {host} on ports {port}-{port+9}")

    url = f"http://{host}:{actual_port}"
    print(f"\n================================================================================")
    print(f"       Zoho to Google Workspace Migration Agent - Local Web UI                 ")
    print(f"================================================================================")
    print(f"  • Running locally at: \033[1;36m{url}\033[0m")
    print(f"  • Security: In-Memory AES-256-GCM Vault (Localhost only)")
    print(f"  • Press Ctrl+C in terminal to stop server.\n")

    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping local UI server...")
        server.server_close()
        MigrationUIHandler.vault.purge()
        print("Security vault purged from memory.")
