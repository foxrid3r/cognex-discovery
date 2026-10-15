"""Exercise the Qt interface offscreen and verify worker delivery."""

import os
import queue
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from cognex_discovery.app import AUTOMATIC_INTERFACE, CognexDiscoveryApp, table_sort_key
from cognex_discovery.discovery import CognexDevice


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

    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        with patch("cognex_discovery.app.get_network_interfaces", return_value={}):
            self.window = CognexDiscoveryApp()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.qt_app.processEvents()

    def test_sort_preserves_device_details_and_discovery_numbers(self):
        self.window.finish_discovery(
            [
                CognexDevice(
                    device_name="Ten", ip_address="10.0.0.10", raw_data=b"ten"
                ),
                CognexDevice(device_name="Two", ip_address="10.0.0.2", raw_data=b"two"),
            ]
        )
        self.window.table.sortItems(4, Qt.SortOrder.AscendingOrder)
        self.assertEqual(self.window.table.item(0, 0).text(), "2")
        self.window.table.selectRow(0)
        self.assertIn("Two", self.window.details_text.toPlainText())
        self.assertIn("74 77 6F", self.window.raw_text.toPlainText())
        self.window.table.sortItems(4, Qt.SortOrder.DescendingOrder)
        self.assertEqual(self.window.table.item(0, 0).text(), "1")
        self.assertIn("Two", self.window.details_text.toPlainText())

    def test_adapter_refresh_preserves_or_resets_selection(self):
        with patch(
            "cognex_discovery.app.get_network_interfaces",
            return_value={"Ethernet (10.0.0.2)": "10.0.0.2"},
        ):
            self.window.refresh_interfaces()
            self.window.interface_combo.setCurrentText("Ethernet (10.0.0.2)")
            self.window.refresh_interfaces()
        self.assertEqual(
            self.window.interface_combo.currentText(), "Ethernet (10.0.0.2)"
        )
        with patch("cognex_discovery.app.get_network_interfaces", return_value={}):
            self.window.refresh_interfaces()
        self.assertEqual(self.window.interface_combo.currentText(), AUTOMATIC_INTERFACE)

    def test_scan_delivers_results_through_gui_timer(self):
        devices = [CognexDevice(device_name="Camera")]
        with patch(
            "cognex_discovery.app.discover_cognex_devices", return_value=devices
        ):
            self.window.start_discovery()
            self.assertFalse(self.window.discover_button.isEnabled())
            self.window.discovery_thread.join(timeout=2)
        self.window._poll_events()
        self.assertTrue(self.window.discover_button.isEnabled())
        self.assertEqual(self.window.table.rowCount(), 1)
        self.assertIn("Camera", self.window.details_text.toPlainText())
        self.window.clear_devices()
        self.assertEqual(self.window.table.rowCount(), 0)
        self.assertEqual(self.window.details_text.toPlainText(), "")
        self.assertEqual(self.window.raw_text.toPlainText(), "")

    def test_download_selection_tracks_devices_after_sorting(self):
        from PySide6.QtCore import QItemSelectionModel

        devices = [
            CognexDevice(device_name="Z Camera"),
            CognexDevice(device_name="A Camera"),
        ]
        self.window.finish_discovery(devices)
        self.window.table.sortItems(1, Qt.SortOrder.AscendingOrder)
        selection = self.window.table.selectionModel()
        selection.clearSelection()
        selection.select(
            self.window.table.model().index(0, 0),
            QItemSelectionModel.SelectionFlag.Select
            | QItemSelectionModel.SelectionFlag.Rows,
        )
        self.assertEqual(self.window.selected_devices(), [devices[1]])
        selection.select(
            self.window.table.model().index(1, 0),
            QItemSelectionModel.SelectionFlag.Select
            | QItemSelectionModel.SelectionFlag.Rows,
        )
        self.assertEqual(len(self.window.selected_devices()), 2)

    def test_download_failure_continues_to_next_camera_and_releases_busy_state(self):
        from pathlib import Path

        from cognex_discovery.ftp_download import DownloadResult

        devices = [CognexDevice(device_name="One"), CognexDevice(device_name="Two")]
        self.window.finish_discovery(devices)
        with patch(
            "cognex_discovery.app.download_camera",
            side_effect=[
                DownloadResult(devices[0], error="Login failed"),
                DownloadResult(devices[1], files=3, bytes_copied=42),
            ],
        ) as download:
            self.window.start_download(devices, Path("."))
            self.assertFalse(self.window.discover_button.isEnabled())
            self.assertFalse(self.window.clear_button.isEnabled())
            self.window.download_thread.join(timeout=2)
        self.window._poll_events()
        self.assertEqual(download.call_count, 2)
        self.assertFalse(self.window._download_active)
        self.assertTrue(self.window.discover_button.isEnabled())
        self.assertTrue(self.window.download_button.isEnabled())
        self.assertIn(
            "1 cameras downloaded, 1 failed", self.window.transfer_log.toPlainText()
        )

    def test_invalid_settings_do_not_start_worker(self):
        self.window.timeout_edit.setText("nan")
        with patch("cognex_discovery.app.QMessageBox.critical") as error:
            self.window.start_discovery()
        error.assert_called_once()
        self.assertIsNone(self.window.discovery_thread)
        self.assertTrue(self.window.discover_button.isEnabled())


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
