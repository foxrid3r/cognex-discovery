"""Tkinter interface for Cognex network discovery."""

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from .discovery import (
    DEFAULT_BROADCAST_IP,
    DEFAULT_TIMEOUT,
    CognexDevice,
    discover_cognex_devices,
    hex_dump,
    validate_discovery_settings,
)


class CognexDiscoveryApp(tk.Tk):
    """Display discovery results without blocking the Tk event loop."""

    def __init__(self) -> None:
        super().__init__()

        self.title("Cognex Network Discovery")
        self.geometry("1220x720")
        self.minsize(960, 600)

        self.devices = []
        self.discovery_thread = None

        self.broadcast_var = tk.StringVar(value=DEFAULT_BROADCAST_IP)
        self.local_ip_var = tk.StringVar(value="")
        self.timeout_var = tk.StringVar(value=str(DEFAULT_TIMEOUT))
        self.status_var = tk.StringVar(value="Ready.")

        self._events = queue.Queue()
        self._build_ui()
        self.after(50, self._poll_events)

    def _build_ui(self) -> None:
        # Controls

        controls = ttk.Frame(self, padding=10)
        controls.pack(fill="x")

        ttk.Label(controls, text="Broadcast IP:").grid(row=0, column=0, sticky="w")

        ttk.Entry(controls, textvariable=self.broadcast_var, width=18).grid(
            row=0, column=1, padx=(5, 15)
        )

        ttk.Label(controls, text="Local IP:").grid(row=0, column=2, sticky="w")

        ttk.Entry(controls, textvariable=self.local_ip_var, width=18).grid(
            row=0, column=3, padx=(5, 15)
        )

        ttk.Label(controls, text="Timeout (s):").grid(row=0, column=4, sticky="w")

        ttk.Entry(controls, textvariable=self.timeout_var, width=8).grid(
            row=0, column=5, padx=(5, 15)
        )

        self.discover_button = ttk.Button(
            controls, text="Discover Devices", command=self.start_discovery
        )
        self.discover_button.grid(row=0, column=6)

        ttk.Button(controls, text="Clear", command=self.clear_devices).grid(
            row=0, column=7, padx=(8, 0)
        )

        ttk.Label(
            controls, text="Leave Local IP blank to let the OS choose the adapter."
        ).grid(row=1, column=0, columnspan=8, pady=(8, 0), sticky="w")

        # Main split pane

        paned = ttk.Panedwindow(self, orient=tk.VERTICAL)
        paned.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # Device table

        top_frame = ttk.Frame(paned)
        paned.add(top_frame, weight=3)

        columns = (
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

        self.tree = ttk.Treeview(
            top_frame, columns=columns, show="headings", selectmode="browse"
        )

        headings = {
            "name": "Device Name",
            "model": "Model",
            "system": "System Name",
            "ip": "IP Address",
            "mac": "MAC Address",
            "serial": "Serial Number",
            "firmware": "Firmware",
            "subnet": "Subnet",
            "gateway": "Gateway",
        }

        widths = {
            "name": 160,
            "model": 100,
            "system": 170,
            "ip": 115,
            "mac": 145,
            "serial": 145,
            "firmware": 115,
            "subnet": 115,
            "gateway": 115,
        }

        for col in columns:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], minwidth=80, anchor="w")

        tree_scroll_y = ttk.Scrollbar(
            top_frame, orient="vertical", command=self.tree.yview
        )

        tree_scroll_x = ttk.Scrollbar(
            top_frame, orient="horizontal", command=self.tree.xview
        )

        self.tree.configure(
            yscrollcommand=tree_scroll_y.set, xscrollcommand=tree_scroll_x.set
        )

        self.tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll_y.grid(row=0, column=1, sticky="ns")
        tree_scroll_x.grid(row=1, column=0, sticky="ew")

        top_frame.rowconfigure(0, weight=1)
        top_frame.columnconfigure(0, weight=1)

        self.tree.bind("<<TreeviewSelect>>", self.on_device_selected)

        # Details notebook

        bottom_frame = ttk.Frame(paned)
        paned.add(bottom_frame, weight=2)

        self.notebook = ttk.Notebook(bottom_frame)
        self.notebook.pack(fill="both", expand=True)

        # Parsed details
        parsed_tab = ttk.Frame(self.notebook)
        self.notebook.add(parsed_tab, text="Parsed Details")

        self.details_text = tk.Text(
            parsed_tab, wrap="none", height=12, font=("Consolas", 10)
        )
        self.details_text.pack(fill="both", expand=True)

        # Raw packet
        raw_tab = ttk.Frame(self.notebook)
        self.notebook.add(raw_tab, text="Raw Response")

        self.raw_text = tk.Text(raw_tab, wrap="none", height=12, font=("Consolas", 10))

        raw_scroll_y = ttk.Scrollbar(
            raw_tab, orient="vertical", command=self.raw_text.yview
        )

        raw_scroll_x = ttk.Scrollbar(
            raw_tab, orient="horizontal", command=self.raw_text.xview
        )

        self.raw_text.configure(
            yscrollcommand=raw_scroll_y.set, xscrollcommand=raw_scroll_x.set
        )

        self.raw_text.grid(row=0, column=0, sticky="nsew")
        raw_scroll_y.grid(row=0, column=1, sticky="ns")
        raw_scroll_x.grid(row=1, column=0, sticky="ew")

        raw_tab.rowconfigure(0, weight=1)
        raw_tab.columnconfigure(0, weight=1)

        # Status bar

        status_bar = ttk.Label(
            self,
            textvariable=self.status_var,
            relief="sunken",
            anchor="w",
            padding=(6, 3),
        )
        status_bar.pack(fill="x", side="bottom")

    # UI actions

    def set_status_threadsafe(self, text: str) -> None:
        """Queue worker updates; only the main thread may call Tk APIs."""
        self._events.put(("status", text))

    def _poll_events(self) -> None:
        """Apply queued worker messages from the Tk event loop."""
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == "status":
                    self.status_var.set(payload)
                elif kind == "finished":
                    self.finish_discovery(payload)
                else:
                    self.discovery_failed(payload)
        except queue.Empty:
            pass
        self.after(50, self._poll_events)

    def start_discovery(self) -> None:
        if self.discovery_thread is not None and self.discovery_thread.is_alive():
            return

        broadcast_ip = self.broadcast_var.get().strip()
        local_ip = self.local_ip_var.get().strip()

        try:
            timeout_seconds = float(self.timeout_var.get().strip())
            validate_discovery_settings(broadcast_ip, local_ip, timeout_seconds)
        except ValueError as exc:
            messagebox.showerror("Invalid discovery settings", str(exc))
            return

        self.clear_devices()
        self.discover_button.configure(state="disabled")
        self.status_var.set("Discovering...")

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
        self.devices = devices

        for index, device in enumerate(devices):
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    device.device_name,
                    device.device_model,
                    device.system_name,
                    device.ip_address,
                    device.mac_address,
                    device.serial_number,
                    device.firmware_version,
                    device.subnet,
                    device.gateway,
                ),
            )

        self.discover_button.configure(state="normal")

        count = len(devices)

        if count == 1:
            self.status_var.set("Discovery complete: 1 device found.")
        else:
            self.status_var.set(f"Discovery complete: {count} devices found.")

        if devices:
            self.tree.selection_set("0")
            self.tree.focus("0")
            self.on_device_selected()

    def discovery_failed(self, message: str) -> None:
        self.discover_button.configure(state="normal")
        self.status_var.set("Discovery failed.")

        messagebox.showerror("Cognex Discovery", message)

    def clear_devices(self) -> None:
        self.devices = []

        for item in self.tree.get_children():
            self.tree.delete(item)

        self.details_text.delete("1.0", "end")
        self.raw_text.delete("1.0", "end")

        self.status_var.set("Ready.")

    def on_device_selected(self, event: tk.Event | None = None) -> None:
        selection = self.tree.selection()

        if not selection:
            return

        index = int(selection[0])

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

        self.details_text.delete("1.0", "end")
        self.details_text.insert("1.0", details)

        self.raw_text.delete("1.0", "end")
        self.raw_text.insert("1.0", hex_dump(device.raw_data))


def main() -> None:
    """Launch the desktop application."""
    app = CognexDiscoveryApp()
    app.mainloop()
