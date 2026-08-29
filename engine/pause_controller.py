"""
Pause Controller and Network Watchdog Failsafe.

Provides thread-safe state synchronization for manual Pause/Resume
and automatic network loss detection with background reconnection probes.
"""

import socket
import time
import threading
import urllib.request
import urllib.error
from enum import Enum
from typing import Optional, Callable, List
from security.sanitizer import setup_secure_logger

logger = setup_secure_logger("pause_controller")


class PauseState(str, Enum):
    RUNNING = "RUNNING"
    PAUSED_MANUAL = "PAUSED_MANUAL"
    PAUSED_NETWORK_LOST = "PAUSED_NETWORK_LOST"
    CANCELLED = "CANCELLED"


class PauseController:
    """
    Thread-safe controller managing execution pause/resume states and cancel requests.
    """

    def __init__(self, on_state_change: Optional[Callable[[PauseState, str], None]] = None):
        self._lock = threading.Lock()
        self._resume_event = threading.Event()
        self._resume_event.set()  # Initially running
        self._state = PauseState.RUNNING
        self._pause_reason: str = ""
        self._on_state_change = on_state_change
        self._watchdog = NetworkWatchdog(self)

    @property
    def state(self) -> PauseState:
        with self._lock:
            return self._state

    @property
    def is_paused(self) -> bool:
        with self._lock:
            return self._state in (PauseState.PAUSED_MANUAL, PauseState.PAUSED_NETWORK_LOST)

    @property
    def is_cancelled(self) -> bool:
        with self._lock:
            return self._state == PauseState.CANCELLED

    @property
    def pause_reason(self) -> str:
        with self._lock:
            return self._pause_reason

    def pause(self, reason: str = "Manual pause by administrator") -> None:
        """Pauses worker threads safely."""
        with self._lock:
            if self._state == PauseState.CANCELLED:
                return
            self._state = PauseState.PAUSED_MANUAL
            self._pause_reason = reason
            self._resume_event.clear()
            logger.info(f"Migration PAUSED (Manual): {reason}")

        if self._on_state_change:
            self._on_state_change(PauseState.PAUSED_MANUAL, reason)

    def handle_network_disconnect(self, error_msg: str = "Network connection lost") -> None:
        """Triggered automatically when a network failure occurs."""
        with self._lock:
            if self._state == PauseState.CANCELLED:
                return
            if self._state == PauseState.PAUSED_MANUAL:
                return  # Already manually paused
            self._state = PauseState.PAUSED_NETWORK_LOST
            self._pause_reason = f"Network disconnected: {error_msg}"
            self._resume_event.clear()
            logger.warning(f"Migration AUTO-PAUSED (Network Loss): {error_msg}")

        if self._on_state_change:
            self._on_state_change(PauseState.PAUSED_NETWORK_LOST, self._pause_reason)

        # Launch watchdog recovery in background
        self._watchdog.start_monitoring_recovery()

    def resume(self) -> None:
        """Resumes worker threads."""
        with self._lock:
            if self._state == PauseState.CANCELLED:
                return
            prev_state = self._state
            self._state = PauseState.RUNNING
            self._pause_reason = ""
            self._resume_event.set()
            logger.info(f"Migration RESUMED (was {prev_state.value}).")

        if self._on_state_change:
            self._on_state_change(PauseState.RUNNING, "Resumed")

    def cancel(self, reason: str = "Migration cancelled by user") -> None:
        """Cancels migration execution."""
        with self._lock:
            self._state = PauseState.CANCELLED
            self._pause_reason = reason
            self._resume_event.set()  # Unblock any waiting threads to let them exit
            logger.info(f"Migration CANCELLED: {reason}")

        if self._on_state_change:
            self._on_state_change(PauseState.CANCELLED, reason)

    def wait_if_paused(self, interval: float = 0.5) -> bool:
        """
        Blocks while paused until resume() or cancel() is called.
        Raises InterruptedError if cancelled.
        Returns True if execution can proceed, False if cancelled.
        """
        while self.is_paused:
            # Sleep briefly and check resume event
            if self._resume_event.wait(timeout=interval):
                break
            if self.is_cancelled:
                raise InterruptedError(self._pause_reason or "Migration was cancelled.")

        if self.is_cancelled:
            raise InterruptedError(self._pause_reason or "Migration was cancelled.")

        return True


class NetworkWatchdog:
    """
    Monitors network connectivity and automatically triggers resume when online.
    """

    PROBE_ENDPOINTS = [
        "https://www.google.com/generate_204",
        "https://mail.zoho.com",
        "https://www.google.com",
    ]

    def __init__(self, controller: PauseController, poll_interval: float = 3.0):
        self.controller = controller
        self.poll_interval = poll_interval
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitoring_active = threading.Event()

    @staticmethod
    def is_network_error(exc: Exception) -> bool:
        """Determines if an exception is caused by network / socket disconnection."""
        if isinstance(exc, (socket.gaierror, socket.timeout, TimeoutError, ConnectionResetError, ConnectionRefusedError)):
            return True
        if isinstance(exc, urllib.error.URLError):
            # Check inner reason
            reason = getattr(exc, "reason", None)
            if isinstance(reason, (socket.gaierror, socket.timeout, TimeoutError, ConnectionResetError, ConnectionRefusedError)):
                return True
            reason_str = str(reason).lower() if reason else ""
            if any(k in reason_str for k in ["nodename", "name resolution", "temporary failure", "network is unreachable", "connection refused"]):
                return True
        err_msg = str(exc).lower()
        if any(k in err_msg for k in ["temporary failure in name resolution", "network is unreachable", "connection reset by peer", "broken pipe", "no route to host"]):
            return True
        return False

    @classmethod
    def check_connectivity(cls, timeout: float = 3.0) -> bool:
        """
        Tests internet connectivity using lightweight socket and HTTP probes.
        """
        # Quick DNS / TCP probe to public DNS
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(("8.8.8.8", 53))
            sock.close()
            return True
        except Exception:
            pass

        # Fallback HTTP probe
        for endpoint in cls.PROBE_ENDPOINTS:
            try:
                req = urllib.request.Request(endpoint, headers={"User-Agent": "NetworkWatchdogProbe/1.0"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    if resp.status in (200, 204):
                        return True
            except Exception:
                continue

        return False

    def start_monitoring_recovery(self) -> None:
        """Starts background monitoring thread to auto-resume when online."""
        if self._monitor_thread and self._monitor_thread.is_alive():
            return

        self._monitoring_active.set()
        self._monitor_thread = threading.Thread(target=self._recovery_loop, daemon=True, name="NetworkWatchdogProbe")
        self._monitor_thread.start()

    def _recovery_loop(self) -> None:
        logger.info("NetworkWatchdog started probing for internet recovery...")
        consecutive_successes = 0

        while self._monitoring_active.is_set() and self.controller.state == PauseState.PAUSED_NETWORK_LOST:
            time.sleep(self.poll_interval)

            if self.check_connectivity(timeout=2.5):
                consecutive_successes += 1
                if consecutive_successes >= 2:  # 2 consecutive successful pings for stability
                    logger.info("Internet connectivity confirmed restored! Auto-resuming migration...")
                    self._monitoring_active.clear()
                    self.controller.resume()
                    break
            else:
                consecutive_successes = 0

        self._monitoring_active.clear()
