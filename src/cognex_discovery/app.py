"""PySide6 interface for Cognex network discovery."""

import queue
import sys
import threading
from ipaddress import IPv4Address

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .discovery import (
    DEFAULT_BROADCAST_IP,
    DEFAULT_TIMEOUT,
    CognexDevice,
    discover_cognex_devices,
    hex_dump,
    validate_discovery_settings,
)
from .interfaces import get_network_interfaces

AUTOMATIC_INTERFACE = "Automatic (OS default)"
COLUMNS = (
    "number",
    "name",
    "model",
    "system",
    "ip",
    "mac",
    "serial",
    "firmware",
    "subnet",
    "gateway",
)
HEADINGS = (
    "Number",
    "Device Name",
    "Model",
    "System Name",
    "IP Address",
    "MAC Address",
    "Serial Number",
    "Firmware",
    "Subnet",
    "Gateway",
)


def table_sort_key(column: str, value: str) -> tuple:
    """Sort numbers and IPv4 addresses by value, and text without case."""
    if column == "number":
        return (0, int(value))
    if column in {"ip", "subnet", "gateway"}:
        try:
            return (0, int(IPv4Address(value)))
        except ValueError:
            return (1, value.casefold())
    return (0, value.casefold())


class DeviceTableItem(QTableWidgetItem):
    """Keep numeric and address ordering when Qt sorts a column."""

    def __lt__(self, other: QTableWidgetItem) -> bool:
        column = COLUMNS[self.column()]
        return table_sort_key(column, self.text()) < table_sort_key(
            column, other.text()
        )


class CognexDiscoveryApp(QMainWindow):
    """Display discovery results while network work runs in a daemon thread."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Cognex Network Discovery")
        self.resize(1220, 720)
        self.setMinimumSize(960, 600)
        self.devices = []
        self.discovery_thread = None
        self.interfaces = {AUTOMATIC_INTERFACE: ""}
        self._events = queue.Queue()
        self._build_ui()
        self.refresh_interfaces()
        # The worker only touches the queue; Qt widgets stay on the GUI thread.
        self.event_timer = QTimer(self)
        self.event_timer.timeout.connect(self._poll_events)
        self.event_timer.start(50)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        controls = QGridLayout()
        controls.setHorizontalSpacing(12)
        controls.setVerticalSpacing(4)
        controls.setColumnStretch(6, 1)
        layout.addLayout(controls)
        self.broadcast_edit = QLineEdit(DEFAULT_BROADCAST_IP)
        address_width = (
            self.broadcast_edit.fontMetrics().horizontalAdvance("255.255.255.255") + 32
        )
        self.broadcast_edit.setFixedWidth(address_width)
        self.interface_combo = QComboBox()
        # Long adapter names must not force the window or other controls wider.
        self.interface_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.interface_combo.setMinimumContentsLength(24)
        self.interface_combo.setFixedWidth(
            self.interface_combo.fontMetrics().horizontalAdvance("M" * 32) + 32
        )
        self.interface_combo.setToolTip(
            "Select an adapter and IPv4 address, or use Automatic."
        )
        self.timeout_edit = QLineEdit(str(DEFAULT_TIMEOUT))
        self.timeout_edit.setFixedWidth(
            self.timeout_edit.fontMetrics().horizontalAdvance("0000.00") + 24
        )
        for column, (title, widget) in enumerate(
            [
                ("Broadcast IP:", self.broadcast_edit),
                ("Network adapter:", self.interface_combo),
                ("Timeout (s):", self.timeout_edit),
            ]
        ):
            label = QLabel(title)
            label.setBuddy(widget)
            controls.addWidget(label, 0, column)
            controls.addWidget(widget, 1, column)

        refresh_button = QPushButton("Refresh adapters")
        refresh_button.clicked.connect(self.refresh_interfaces)
        controls.addWidget(refresh_button, 1, 3)
        self.discover_button = QPushButton("Discover Devices")
        self.discover_button.clicked.connect(self.start_discovery)
        controls.addWidget(self.discover_button, 1, 4)
        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self.clear_devices)
        controls.addWidget(clear_button, 1, 5)

        splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(splitter)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(HEADINGS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        for index, width in enumerate(
            [70, 160, 100, 170, 115, 145, 145, 115, 115, 115]
        ):
            self.table.setColumnWidth(index, width)
        self.table.setSortingEnabled(True)
        self.table.sortItems(0, Qt.SortOrder.AscendingOrder)
        self.table.itemSelectionChanged.connect(self.on_device_selected)
        splitter.addWidget(self.table)
        self.notebook = QTabWidget()
        self.details_text = QPlainTextEdit()
        self.raw_text = QPlainTextEdit()
        for title, editor in [
            ("Parsed Details", self.details_text),
            ("Raw Response", self.raw_text),
        ]:
            editor.setReadOnly(True)
            editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            editor.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
            self.notebook.addTab(editor, title)
        splitter.addWidget(self.notebook)
        splitter.setSizes([390, 260])
        self.statusBar().showMessage("Ready.")

    def refresh_interfaces(self) -> None:
        """Refresh addresses while preserving a selection that still exists."""
        try:
            interfaces = {AUTOMATIC_INTERFACE: "", **get_network_interfaces()}
        except OSError as exc:
            QMessageBox.critical(
                self, "Network adapters", f"Could not list adapters:\n\n{exc}"
            )
            return
        previous = self.interface_combo.currentText()
        self.interfaces = interfaces
        self.interface_combo.clear()
        self.interface_combo.addItems(list(interfaces))
        self.interface_combo.setCurrentText(
            previous if previous in interfaces else AUTOMATIC_INTERFACE
        )

    def set_status_threadsafe(self, text: str) -> None:
        """Queue worker updates for delivery on the GUI thread."""
        self._events.put(("status", text))

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == "status":
                    self.statusBar().showMessage(payload)
                elif kind == "finished":
                    self.finish_discovery(payload)
                else:
                    self.discovery_failed(payload)
        except queue.Empty:
            pass

    def start_discovery(self) -> None:
        if self.discovery_thread is not None and self.discovery_thread.is_alive():
            return
        broadcast_ip = self.broadcast_edit.text().strip()
        local_ip = self.interfaces[self.interface_combo.currentText()]
        try:
            timeout_seconds = float(self.timeout_edit.text().strip())
            validate_discovery_settings(broadcast_ip, local_ip, timeout_seconds)
        except ValueError as exc:
            QMessageBox.critical(self, "Invalid discovery settings", str(exc))
            return
        self.clear_devices()
        self.discover_button.setEnabled(False)
        self.statusBar().showMessage("Discovering...")
        self.discovery_thread = threading.Thread(
            target=self._discovery_worker,
            args=(broadcast_ip, local_ip, timeout_seconds),
            daemon=True,
        )
        self.discovery_thread.start()

    def _discovery_worker(
        self, broadcast_ip: str, local_ip: str, timeout_seconds: float
    ) -> None:
        try:
            devices = discover_cognex_devices(
                broadcast_ip=broadcast_ip,
                local_ip=local_ip,
                timeout_seconds=timeout_seconds,
                status_callback=self.set_status_threadsafe,
            )

            self._events.put(("finished", devices))
        except OSError as exc:
            self._events.put(("error", f"Network error:\n\n{exc}"))
        except Exception as exc:
            # Always release the busy state if an unexpected worker error occurs.
            self._events.put(("error", f"Discovery failed:\n\n{exc}"))

    def finish_discovery(self, devices: list[CognexDevice]) -> None:
        self.clear_devices()
        self.devices = devices
        header = self.table.horizontalHeader()
        sort_column, sort_order = (
            header.sortIndicatorSection(),
            header.sortIndicatorOrder(),
        )
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(devices))
        for index, device in enumerate(devices):
            values = (
                index + 1,
                device.device_name,
                device.device_model,
                device.system_name,
                device.ip_address,
                device.mac_address,
                device.serial_number,
                device.firmware_version,
                device.subnet,
                device.gateway,
            )
            for column, value in enumerate(values):
                item = DeviceTableItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, index)
                self.table.setItem(index, column, item)
        self.table.setSortingEnabled(True)
        self.table.sortItems(sort_column, sort_order)
        self.discover_button.setEnabled(True)
        count = len(devices)
        noun = "device" if count == 1 else "devices"
        self.statusBar().showMessage(f"Discovery complete: {count} {noun} found.")
        if devices:
            # Select the first discovered device even when the table is sorted.
            for row in range(count):
                if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) == 0:
                    self.table.selectRow(row)
                    break

    def discovery_failed(self, message: str) -> None:
        self.discover_button.setEnabled(True)
        self.statusBar().showMessage("Discovery failed.")
        QMessageBox.critical(self, "Cognex Discovery", message)

    def clear_devices(self) -> None:
        self.devices = []
        self.table.setRowCount(0)
        self.details_text.clear()
        self.raw_text.clear()
        self.statusBar().showMessage("Ready.")

    def on_device_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0 or not self.table.selectedItems():
            return
        index = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not (0 <= index < len(self.devices)):
            return
        device = self.devices[index]
        details = (
            f"Device Name:       {device.device_name}\n"
            f"Device Model:      {device.device_model}\n"
            f"System Name:       {device.system_name}\n"
            f"Serial Number:     {device.serial_number}\n"
            f"Firmware Version:  {device.firmware_version}\n"
            f"\n"
            f"MAC Address:       {device.mac_address}\n"
            f"IP Address:        {device.ip_address}\n"
            f"Subnet Mask:       {device.subnet}\n"
            f"Gateway:           {device.gateway}\n"
            f"DNS:               {device.dns}\n"
            f"Domain:            {device.domain}\n"
            f"\n"
            f"UDP Source:        {device.source_ip}:{device.source_port}\n"
            f"Response Length:   {len(device.raw_data)} bytes\n"
        )

        self.details_text.setPlainText(details)
        self.raw_text.setPlainText(hex_dump(device.raw_data))


def main() -> None:
    """Launch the desktop application."""
    app = QApplication(sys.argv)
    window = CognexDiscoveryApp()
    window.show()
    sys.exit(app.exec())
