"""Adapter enumeration tests do not depend on the host's network setup."""

import socket
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cognex_discovery.interfaces import get_network_interfaces


class InterfaceTests(unittest.TestCase):
    def test_multiple_addresses_and_excluded_addresses(self):
        def address(ip, family=socket.AF_INET):
            return SimpleNamespace(address=ip, family=family)

        with patch(
            "cognex_discovery.interfaces.psutil.net_if_addrs",
            return_value={
                "Ethernet": [
                    address("192.168.1.2"),
                    address("10.0.0.2"),
                    address("::1", socket.AF_INET6),
                ],
                "Loopback": [address("127.0.0.1")],
                "Unconfigured": [address("0.0.0.0")],
            },
        ):
            self.assertEqual(
                get_network_interfaces(),
                {
                    "Ethernet (192.168.1.2)": "192.168.1.2",
                    "Ethernet (10.0.0.2)": "10.0.0.2",
                },
            )
