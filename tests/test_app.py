"""Exercise worker delivery without creating a graphical window."""

import queue
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cognex_discovery.app import CognexDiscoveryApp


class WorkerTests(unittest.TestCase):
    def test_worker_errors_survive_exception_scope_without_tk_calls(self):
        for error in [OSError("socket failed"), RuntimeError("unexpected failure")]:
            with self.subTest(error=error):
                app = SimpleNamespace(
                    _events=queue.Queue(), set_status_threadsafe=Mock()
                )
                with patch(
                    "cognex_discovery.app.discover_cognex_devices", side_effect=error
                ):
                    CognexDiscoveryApp._discovery_worker(app, "255.255.255.255", "", 1)
                kind, message = app._events.get_nowait()
                self.assertEqual(kind, "error")
                self.assertIn(str(error), message)

    def test_results_are_delivered_via_queue(self):
        app = SimpleNamespace(_events=queue.Queue(), set_status_threadsafe=Mock())
        with patch("cognex_discovery.app.discover_cognex_devices", return_value=[]):
            CognexDiscoveryApp._discovery_worker(app, "255.255.255.255", "", 1)
        self.assertEqual(app._events.get_nowait(), ("finished", []))


if __name__ == "__main__":
    unittest.main()
