"""UDP discovery and best-effort parsing of Cognex device responses."""

import math
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from ipaddress import IPv4Address

COGNEX_DISCOVERY_PORT = 1069
DISCOVERY_PAYLOAD = bytes.fromhex("43 47 4E 4D 04 00 02 00 00")
COGNEX_IDENTIFIER = b"CGNM"

DEFAULT_BROADCAST_IP = "255.255.255.255"
DEFAULT_TIMEOUT = 3.0


@dataclass
class CognexDevice:
    """Known discovery fields and the original UDP response for inspection."""

    source_ip: str = ""
    source_port: int = 0

    mac_address: str = ""
    device_name: str = ""
    ip_address: str = ""
    subnet: str = ""
    gateway: str = ""
    dns: str = ""
    domain: str = ""

    device_model: str = ""
    system_name: str = ""
    firmware_version: str = ""
    serial_number: str = ""

    raw_data: bytes = b""


def search_bytes(data: bytes, pattern: bytes, start_index: int = 0) -> int:
    """
    Returns -1 if the pattern is not found.
    """
    return data.find(pattern, start_index)


def get_mac_from_byte_array(data: bytes) -> str:
    if len(data) != 6:
        return ""
    return "-".join(f"{b:02X}" for b in data)


def get_address_from_hex_array(data: bytes) -> str:
    if len(data) != 4:
        return ""
    return ".".join(str(b) for b in data)


def decode_ascii(data: bytes) -> str:
    """Decode device text, preserving undecodable bytes as replacement characters."""
    return data.decode("ascii", errors="replace").rstrip("\x00")


def hex_dump(data: bytes, width: int = 16) -> str:
    """Format bytes as offsets, hexadecimal values, and printable ASCII."""
    if width <= 0:
        raise ValueError("width must be greater than zero")
    lines = []

    for offset in range(0, len(data), width):
        chunk = data[offset : offset + width]

        hex_part = " ".join(f"{b:02X}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b <= 126 else "." for b in chunk)

        lines.append(f"{offset:04X}   {hex_part:<{width * 3}} {ascii_part}")

    return "\n".join(lines)


def extract_tlv_value(
    data: bytes, field_id: int, start_index: int = 0
) -> tuple[bytes | None, int, int]:
    """
    Search for:
        [field_id] [length] [value...]

    Returns:
        (value, field_index, length_index)

    or:
        (None, -1, -1)
    """
    idx = search_bytes(data, bytes([field_id]), start_index)

    if idx < 0 or idx + 1 >= len(data):
        return None, -1, -1

    length = data[idx + 1]
    value_start = idx + 2
    value_end = value_start + length

    if value_end > len(data):
        return None, -1, -1

    return data[value_start:value_end], idx, idx + 1


def parse_cognex_response(
    reply_array: bytes, source_ip: str = "", source_port: int = 0
) -> CognexDevice:
    """
    Parse known fields using the original marker-search strategy:
      - locate CGNM
      - locate known property identifiers
      - extract only known fields
      - do NOT assume the entire payload is a flat generic TLV stream
    """

    device = CognexDevice(
        source_ip=source_ip,
        source_port=source_port,
        raw_data=reply_array,
    )

    cognex_ident_idx = search_bytes(reply_array, COGNEX_IDENTIFIER)

    if cognex_ident_idx < 0:
        raise ValueError("CGNM identifier not found in response.")

    search_mac_idx = cognex_ident_idx + len(COGNEX_IDENTIFIER)

    # MAC address: 0x21 0x06 <6 bytes>

    mac_ident_idx = search_bytes(reply_array, bytes([0x21, 0x06]), search_mac_idx)

    mac_idx = -1
    search_array = reply_array[search_mac_idx:]

    if mac_ident_idx >= 0:
        mac_idx = mac_ident_idx + 2

        if mac_idx + 6 <= len(reply_array):
            mac_array = reply_array[mac_idx : mac_idx + 6]
            device.mac_address = get_mac_from_byte_array(mac_array)

            # Search subsequent fields after the MAC to avoid matching its bytes.
            search_array = reply_array[mac_idx + 6 :]

    # Device Model: 0x25

    value, _, _ = extract_tlv_value(search_array, 0x25)
    if value is not None:
        device.device_model = decode_ascii(value)

    # System Name: 0x28

    value, system_idx, _ = extract_tlv_value(search_array, 0x28)
    if value is not None:
        device.system_name = decode_ascii(value)

    # Firmware: 0x27

    value, _, _ = extract_tlv_value(search_array, 0x27)
    if value is not None:
        device.firmware_version = decode_ascii(value)

    # Serial Number: 0x26
    # Prefer searching after the system-name field if available.

    serial_start = 0

    if system_idx >= 0:
        sys_len = search_array[system_idx + 1]
        serial_start = system_idx + 2 + sys_len

    value, serial_idx, _ = extract_tlv_value(search_array, 0x26, serial_start)
    if value is not None:
        device.serial_number = decode_ascii(value)

    # Device Name: 0x22
    # Prefer searching after serial number.

    name_start = 0

    if serial_idx >= 0:
        serial_len = search_array[serial_idx + 1]
        name_start = serial_idx + 2 + serial_len

    value, name_idx, _ = extract_tlv_value(search_array, 0x22, name_start)
    if value is not None:
        device.device_name = decode_ascii(value)

    # IP Address: 0x23
    # Stored little-endian.

    ip_start = 0

    if name_idx >= 0:
        name_len = search_array[name_idx + 1]
        ip_start = name_idx + 2 + name_len

    value, _, _ = extract_tlv_value(search_array, 0x23, ip_start)

    if value is not None and len(value) == 4:
        device.ip_address = get_address_from_hex_array(value[::-1])

    # Network Information: 0x24
    # Inferred layout retained from the original implementation:
    #
    # byte 0       : unknown / flags
    # bytes 1..4   : subnet, little-endian
    # bytes 5..8   : gateway, little-endian
    # bytes 9..12  : DNS, little-endian
    # byte 13      : unknown / reserved
    # bytes 14..N  : domain string

    network_start = 0

    if name_idx >= 0:
        name_len = search_array[name_idx + 1]
        network_start = name_idx + 2 + name_len

    value, _, _ = extract_tlv_value(search_array, 0x24, network_start)

    if value is not None:
        network_info = value

        if len(network_info) >= 5:
            subnet_array = network_info[1:5][::-1]
            device.subnet = get_address_from_hex_array(subnet_array)

        if len(network_info) >= 9:
            gateway_array = network_info[5:9][::-1]
            device.gateway = get_address_from_hex_array(gateway_array)

        if len(network_info) >= 13:
            dns_array = network_info[9:13][::-1]
            device.dns = get_address_from_hex_array(dns_array)

        if len(network_info) > 14:
            device.domain = decode_ascii(network_info[14:])
        else:
            device.domain = ""

    # Fallback to UDP source IP if 0x23 was absent.
    if not device.ip_address:
        device.ip_address = source_ip

    return device


def validate_discovery_settings(
    broadcast_ip: str, local_ip: str, timeout_seconds: float
) -> None:
    """Reject invalid IPv4 addresses and nonfinite or nonpositive timeouts."""
    IPv4Address(broadcast_ip)
    if local_ip.strip():
        IPv4Address(local_ip.strip())
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Timeout must be a finite number greater than zero.")


def discover_cognex_devices(
    broadcast_ip: str = DEFAULT_BROADCAST_IP,
    local_ip: str = "",
    timeout_seconds: float = DEFAULT_TIMEOUT,
    status_callback: Callable[[str], None] | None = None,
) -> list[CognexDevice]:
    """Send one UDP discovery request and collect unique replies until timeout.

    The optional status callback runs synchronously in the calling thread.
    Socket errors propagate to the caller; unrecognized replies are skipped.
    """
    validate_discovery_settings(broadcast_ip, local_ip, timeout_seconds)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        bind_ip = local_ip.strip() if local_ip.strip() else "0.0.0.0"
        sock.bind((bind_ip, 0))

        local_endpoint = sock.getsockname()

        if status_callback:
            status_callback(
                f"Sending discovery from {local_endpoint[0]}:{local_endpoint[1]} "
                f"to {broadcast_ip}:{COGNEX_DISCOVERY_PORT}"
            )

        sock.sendto(DISCOVERY_PAYLOAD, (broadcast_ip, COGNEX_DISCOVERY_PORT))

        deadline = time.monotonic() + timeout_seconds
        devices = []

        # Deduplicate primarily by MAC, secondarily by IP/source IP.
        seen = set()

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(min(0.20, remaining))
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue

            source_ip, source_port = addr

            try:
                device = parse_cognex_response(
                    data, source_ip=source_ip, source_port=source_port
                )
            except ValueError as exc:
                if status_callback:
                    status_callback(f"Received unparsed reply from {source_ip}: {exc}")
                continue

            key = device.mac_address or device.ip_address or device.source_ip

            if key in seen:
                continue

            seen.add(key)
            devices.append(device)

            if status_callback:
                name = (
                    device.device_name
                    or device.device_model
                    or device.ip_address
                    or source_ip
                )
                status_callback(f"Found: {name}")

        return devices

    finally:
        sock.close()
