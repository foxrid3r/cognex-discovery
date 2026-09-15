"""Exercise worker delivery without creating a graphical window."""

import queue
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cognex_discovery.app import AUTOMATIC_INTERFACE, CognexDiscoveryApp, table_sort_key


class TableAndAdapterTests(unittest.TestCase):
    def test_numeric_and_ip_sorting(self):
        for column, values, expected in [
            ("number", ["10", "2", "1"], ["1", "2", "10"]),
            ("ip", ["10.0.0.10", "", "10.0.0.2"], ["10.0.0.2", "10.0.0.10", ""]),
            ("name", ["beta", "Alpha"], ["Alpha", "beta"]),
        ]:
            with self.subTest(column=column):
                self.assertEqual(
                    sorted(values, key=lambda value: table_sort_key(column, value)),
                    expected,
                )

    def test_sort_toggles_and_preserves_row_ids(self):
        tree = Mock()
        tree.get_children.return_value = ["0", "1"]
        tree.set.side_effect = lambda item, column: {"0": "10.0.0.10", "1": "10.0.0.2"}[
            item
        ]
        app = SimpleNamespace(
            tree=tree,
            sort_column="number",
            sort_reverse=False,
            headings={"ip": "IP Address"},
        )
        CognexDiscoveryApp.sort_table(app, "ip")
        self.assertEqual(
            [call.args[0] for call in tree.move.call_args_list], ["1", "0"]
        )
        tree.move.reset_mock()
        CognexDiscoveryApp.sort_table(app, "ip")
        self.assertEqual(
            [call.args[0] for call in tree.move.call_args_list], ["0", "1"]
        )
        tree.selection_set.assert_not_called()

    def test_adapter_refresh_preserves_or_resets_selection(self):
        app = SimpleNamespace(interface_var=Mock(), interface_combo=Mock())
        app.interface_var.get.return_value = "Ethernet (10.0.0.2)"
        with patch(
            "cognex_discovery.app.get_network_interfaces",
            return_value={"Ethernet (10.0.0.2)": "10.0.0.2"},
        ):
            CognexDiscoveryApp.refresh_interfaces(app)
        app.interface_var.set.assert_not_called()
        self.assertEqual(app.interfaces["Ethernet (10.0.0.2)"], "10.0.0.2")
        with patch("cognex_discovery.app.get_network_interfaces", return_value={}):
            CognexDiscoveryApp.refresh_interfaces(app)
        app.interface_var.set.assert_called_once_with(AUTOMATIC_INTERFACE)


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
