import unittest
import json
import socket
import threading
import time
import urllib.error
from unittest.mock import MagicMock, patch

from engine.pause_controller import PauseController, PauseState, NetworkWatchdog
from engine.rate_limiter import retry_with_backoff
from tests.test_ui_server import simulate_http_request
from ui.server import MigrationUIHandler


class TestPauseController(unittest.TestCase):
    """Tests PauseController lifecycle and thread synchronization."""

    def test_initial_state_running(self):
        controller = PauseController()
        self.assertEqual(controller.state, PauseState.RUNNING)
        self.assertFalse(controller.is_paused)
        self.assertFalse(controller.is_cancelled)
        # Should return immediately
        self.assertTrue(controller.wait_if_paused(interval=0.01))

    def test_manual_pause_and_resume(self):
        events = []

        def callback(state, reason):
            events.append((state, reason))

        controller = PauseController(on_state_change=callback)

        controller.pause("Moving laptop to another conference room")
        self.assertEqual(controller.state, PauseState.PAUSED_MANUAL)
        self.assertTrue(controller.is_paused)
        self.assertEqual(controller.pause_reason, "Moving laptop to another conference room")

        # Worker thread waiting
        worker_done = threading.Event()

        def worker():
            controller.wait_if_paused(interval=0.05)
            worker_done.set()

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        time.sleep(0.1)
        self.assertFalse(worker_done.is_set(), "Worker should be blocked while paused")

        controller.resume()
        self.assertEqual(controller.state, PauseState.RUNNING)
        self.assertFalse(controller.is_paused)

        worker_done.wait(timeout=1.0)
        self.assertTrue(worker_done.is_set(), "Worker should unblock on resume")

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0][0], PauseState.PAUSED_MANUAL)
        self.assertEqual(events[1][0], PauseState.RUNNING)

    def test_cancel_raises_interrupted_error(self):
        controller = PauseController()
        controller.pause("Paused")

        raised_errors = []

        def worker():
            try:
                controller.wait_if_paused(interval=0.05)
            except InterruptedError as e:
                raised_errors.append(e)

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        time.sleep(0.1)
        controller.cancel("User cancelled the migration")

        t.join(timeout=1.0)
        self.assertEqual(len(raised_errors), 1)
        self.assertIn("cancelled", str(raised_errors[0]))


class TestNetworkWatchdog(unittest.TestCase):
    """Tests NetworkWatchdog exception discrimination and recovery detection."""

    def test_is_network_error_classification(self):
        self.assertTrue(NetworkWatchdog.is_network_error(socket.gaierror(8, "nodename nor servname provided, or not known")))
        self.assertTrue(NetworkWatchdog.is_network_error(socket.timeout("timed out")))
        self.assertTrue(NetworkWatchdog.is_network_error(ConnectionResetError("Connection reset by peer")))
        self.assertTrue(NetworkWatchdog.is_network_error(ConnectionRefusedError("Connection refused")))
        self.assertTrue(NetworkWatchdog.is_network_error(urllib.error.URLError("Temporary failure in name resolution")))
        self.assertTrue(NetworkWatchdog.is_network_error(Exception("Connection reset by peer")))
        self.assertTrue(NetworkWatchdog.is_network_error(Exception("Network is unreachable")))

        # Should NOT be classified as network errors
        self.assertFalse(NetworkWatchdog.is_network_error(ValueError("Invalid payload format")))
        self.assertFalse(NetworkWatchdog.is_network_error(KeyError("missing_field")))

    def test_auto_pause_on_network_disconnect(self):
        controller = PauseController()
        # Mock watchdog to prevent actual background polling
        controller._watchdog.start_monitoring_recovery = MagicMock()

        controller.handle_network_disconnect("WiFi disconnected")
        self.assertEqual(controller.state, PauseState.PAUSED_NETWORK_LOST)
        self.assertTrue(controller.is_paused)
        self.assertIn("WiFi disconnected", controller.pause_reason)
        controller._watchdog.start_monitoring_recovery.assert_called_once()

    def test_rate_limiter_auto_pause_on_network_error(self):
        controller = PauseController()
        controller._watchdog.start_monitoring_recovery = MagicMock()

        attempts = 0

        def flaky_network_call():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                # First attempt raises network error
                raise socket.gaierror(-2, "Name or service not known")
            return "SUCCESS"

        # Start a thread to auto-resume the controller after 0.2s
        def resume_after_delay():
            time.sleep(0.2)
            controller.resume()

        threading.Thread(target=resume_after_delay, daemon=True).start()

        result = retry_with_backoff(
            flaky_network_call,
            max_retries=3,
            initial_delay=0.01,
            pause_controller=controller
        )

        self.assertEqual(result, "SUCCESS")
        self.assertEqual(attempts, 2)


class TestUIPauseEndpoints(unittest.TestCase):
    """Tests Web UI /api/pause, /api/resume, /api/cancel API endpoints."""

    def setUp(self):
        MigrationUIHandler.last_progress_state = {
            "stage": "MAILBOX",
            "stage_name": "Stage 4/4: Mailbox Streaming",
            "percent": 50,
            "current_user": "test@domain.com",
            "item_current": 50,
            "item_total": 100,
            "detail": "Streaming messages",
            "log_messages": [],
            "is_running": True,
            "is_completed": False,
            "is_paused": False,
            "pause_state": "RUNNING",
            "pause_reason": "",
            "network_online": True,
            "summary": None,
            "error": None
        }
        MigrationUIHandler.supervisor = MagicMock()
        MigrationUIHandler.supervisor.pause_state = "PAUSED_MANUAL"

    def test_api_pause_success(self):
        code, headers, body = simulate_http_request("POST", "/api/pause", {"reason": "Moving laptop"})
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data.get("success"))
        MigrationUIHandler.supervisor.pause.assert_called_once_with("Moving laptop")

    def test_api_resume_success(self):
        code, headers, body = simulate_http_request("POST", "/api/resume")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data.get("success"))
        MigrationUIHandler.supervisor.resume.assert_called_once()

    def test_api_cancel_success(self):
        code, headers, body = simulate_http_request("POST", "/api/cancel")
        self.assertEqual(code, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertTrue(data.get("success"))
        MigrationUIHandler.supervisor.cancel.assert_called_once()


if __name__ == "__main__":
    unittest.main()
