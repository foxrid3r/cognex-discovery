"""Regression coverage using synthetic packets; no hardware is contacted."""

import socket
import unittest
from unittest.mock import Mock, patch

from cognex_discovery.discovery import (
    DISCOVERY_PAYLOAD,
    discover_cognex_devices,
    extract_tlv_value,
    hex_dump,
    parse_cognex_response,
)


def field(identifier: int, value: bytes) -> bytes:
    return bytes((identifier, len(value))) + value


PACKET = (
    b"CGNM\x04\x00"
    + field(0x21, bytes.fromhex("001122334455"))
    + field(0x25, b"Model")
    + field(0x28, b"System")
    + field(0x27, b"1.0")
    + field(0x26, b"12345")
    + field(0x22, b"Camera\x00")
    + field(0x23, bytes([10, 1, 168, 192]))
    + field(
        0x24,
        b"\x00"
        + bytes([0, 255, 255, 255])
        + bytes([1, 1, 168, 192])
        + bytes([8, 8, 8, 8])
        + b"\x00example.local\x00",
    )
)


class ParserTests(unittest.TestCase):
    def test_known_fields_and_raw_packet(self):
        device = parse_cognex_response(PACKET, "192.168.1.20", 1069)
        expected = {
            "mac_address": "00-11-22-33-44-55",
            "device_model": "Model",
            "system_name": "System",
            "firmware_version": "1.0",
            "serial_number": "12345",
            "device_name": "Camera",
            "ip_address": "192.168.1.10",
            "subnet": "255.255.255.0",
            "gateway": "192.168.1.1",
            "dns": "8.8.8.8",
            "domain": "example.local",
            "source_ip": "192.168.1.20",
            "source_port": 1069,
            "raw_data": PACKET,
        }
        for name, value in expected.items():
            with self.subTest(field=name):
                self.assertEqual(getattr(device, name), value)

    def test_missing_identifier_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_cognex_response(b"unrelated")

    def test_all_truncated_packet_prefixes_are_safe(self):
        for length in range(4, len(PACKET)):
            with self.subTest(length=length):
                device = parse_cognex_response(PACKET[:length], "10.0.0.1")
                self.assertTrue(device.ip_address)

    def test_missing_ip_uses_source(self):
        self.assertEqual(
            parse_cognex_response(b"CGNM", "10.0.0.1").ip_address, "10.0.0.1"
        )

    def test_incomplete_field_is_ignored(self):
        self.assertEqual(extract_tlv_value(b"\x22\x05ab", 0x22), (None, -1, -1))

    def test_hex_dump(self):
        dump = hex_dump(b"A\x00\xff", width=2)
        self.assertIn("41 00", dump)
        self.assertIn("A.", dump)
        self.assertIn("0002", dump)
        with self.assertRaises(ValueError):
            hex_dump(b"A", 0)


class DiscoveryTests(unittest.TestCase):
    def test_duplicates_invalid_replies_and_receive_timeout(self):
        sock = Mock()
        sock.getsockname.return_value = ("0.0.0.0", 54321)
        sock.recvfrom.side_effect = [
            (b"unrelated", ("10.0.0.2", 1069)),
            (PACKET, ("10.0.0.3", 1069)),
            (PACKET, ("10.0.0.3", 1069)),
            socket.timeout(),
        ]
        status = Mock()
        with (
            patch("cognex_discovery.discovery.socket.socket", return_value=sock),
            patch(
                "cognex_discovery.discovery.time.monotonic",
                side_effect=[0, 0, 0.1, 0.2, 0.3, 1],
            ),
        ):
            devices = discover_cognex_devices(timeout_seconds=1, status_callback=status)
        self.assertEqual(len(devices), 1)
        sock.bind.assert_called_once_with(("0.0.0.0", 0))
        sock.sendto.assert_called_once_with(
            DISCOVERY_PAYLOAD, ("255.255.255.255", 1069)
        )
        sock.close.assert_called_once()
        self.assertTrue(
            any("unparsed" in call.args[0] for call in status.call_args_list)
        )

    def test_socket_closed_on_failure(self):
        sock = Mock()
        sock.bind.side_effect = OSError("adapter unavailable")
        with patch("cognex_discovery.discovery.socket.socket", return_value=sock):
            with self.assertRaisesRegex(OSError, "adapter unavailable"):
                discover_cognex_devices()
        sock.close.assert_called_once()

    def test_invalid_settings_do_not_open_socket(self):
        for options in [
            {"timeout_seconds": float("inf")},
            {"timeout_seconds": float("nan")},
            {"timeout_seconds": 0},
            {"timeout_seconds": -1},
            {"broadcast_ip": "invalid"},
            {"local_ip": "::1"},
        ]:
            with self.subTest(options=options):
                with patch("cognex_discovery.discovery.socket.socket") as factory:
                    with self.assertRaises(ValueError):
                        discover_cognex_devices(**options)
                    factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
