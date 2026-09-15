"""Enumerate local IPv4 addresses for the network-adapter selector."""

import socket
from ipaddress import IPv4Address

import psutil


def get_network_interfaces() -> dict[str, str]:
    """Map adapter/address labels to bind addresses, excluding loopback."""
    interfaces = {}
    for name, addresses in sorted(psutil.net_if_addrs().items()):
        for address in addresses:
            if address.family != socket.AF_INET:
                continue
            ip = IPv4Address(address.address)
            if ip.is_loopback or ip.is_unspecified:
                continue
            interfaces[f"{name} ({ip})"] = str(ip)
    return interfaces
