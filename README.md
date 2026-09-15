# Cognex Discovery

A small Python desktop application for discovering Cognex devices on a local
IPv4 network. It sends a UDP discovery request and displays device information
alongside the original response bytes.

## Features

- Discover devices using a configurable broadcast address and local interface.
- Select a network adapter by name and IPv4 address, and refresh the adapter list.
- Sort numbered device rows by clicking any column header.
- View device names, models, firmware, serial numbers, MAC and IP addresses.
- Inspect subnet, gateway, DNS, domain, and a hexadecimal packet dump.
- Keep the interface responsive while discovery runs in a background thread.
- Use the discovery and parser functions independently of the GUI.

## Requirements

- Python 3.10 or newer, with Tkinter available.
- A graphical desktop to run the application.
- Network access to the devices and permission for UDP traffic on port 1069.

The application uses psutil to enumerate network adapters (installed by pip).
Tkinter is a
Python/OS component; it cannot be installed through this project's pip extras.
Run `python -m tkinter` to check availability. On Debian/Ubuntu, the package is
typically `python3-tk`; Windows Python installations can include Tcl/Tk support.

## Install and run

From a downloaded or cloned checkout:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m cognex_discovery
```

On macOS/Linux, use `.venv/bin/python` in place of
`.\.venv\Scripts\python.exe`. After activating the virtual environment, the
installed `cognex-discovery` command also opens the application.

## Usage

1. Leave **Broadcast IP** at `255.255.255.255`, or enter the broadcast address
   appropriate to your subnet.
2. Choose a **Network adapter** by name and IPv4 address, or leave **Automatic**
   selected for the OS-selected interface. Use **Refresh adapters** after network
   changes. Adapters with multiple IPv4 addresses have an entry for each address.
3. Choose a positive, finite **Timeout (s)** and click **Discover Devices**.
4. Select a result to inspect **Parsed Details** and **Raw Response**.
5. Click a column header to sort; click again to reverse the order. **Number**
   records the original discovery order and stays with its device when sorted.

Results appear when the scan finishes. Replies are deduplicated by MAC address,
falling back to IP address. **Clear** clears the displayed results; it does not
cancel an active scan.

If no devices appear, check the selected adapter, subnet, device power, and
firewall rules. Broadcast discovery usually stays within the local broadcast
domain. VPNs and multiple network adapters can affect routing.

## Python API

```python
from cognex_discovery import discover_cognex_devices

devices = discover_cognex_devices(
    broadcast_ip="255.255.255.255",
    local_ip="",  # Or an IPv4 address assigned to the desired local adapter.
    timeout_seconds=3.0,
    status_callback=print,
)
for device in devices:
    print(device.device_name, device.ip_address, device.mac_address)
```

This function blocks for the scan and invokes callbacks in the calling thread.
Invalid settings raise `ValueError`; socket failures propagate as `OSError`.
`parse_cognex_response(data, source_ip="", source_port=0)` parses saved bytes
without network access. Importing the package does not import Tkinter.

## Protocol scope and limitations

The tool retains the original script's inferred protocol: one request containing
`43 47 4E 4D 04 00 02 00 00` is sent to UDP port 1069. Responses are searched
for the `CGNM` identifier and known length-prefixed field markers. Network
addresses in those fields are interpreted as little-endian values.

Parsing is best effort, not a complete protocol implementation. Marker bytes
inside other values can be mistaken for fields, and field order or layout may
vary by device or firmware. Missing fields remain empty; an absent IP field
falls back to the UDP source address. Raw bytes remain available for inspection.
Hardware compatibility has not been established by the automated tests, which
use synthetic packets and mocked sockets. No device configuration is changed.

This is an independent utility and is not an official Cognex SDK.

## Development

```console
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
python -m ruff check .
python -m ruff format --check .
python -m build
```

GitHub Actions runs linting, formatting, tests, and package builds on Windows
and Linux with Python 3.10 and 3.14.

```text
src/cognex_discovery/
    discovery.py     Data model, packet parsing, and UDP discovery
    app.py           Tkinter interface and main-thread event handling
    interfaces.py    Network-adapter enumeration
    __main__.py      Module launcher
tests/              Parser, socket, and worker regression tests
pyproject.toml      Package metadata and development tools
```

A license has not yet been selected; no open-source license grant is included.
