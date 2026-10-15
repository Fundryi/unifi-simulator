"""Address reported to the controller, independent of the HTTP/TLS destination name."""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

from mtunifi.protocol import ProtocolError


async def advertised_inform_url(url: str) -> str:
    """UniFi 10.6.106 rejects DNS names in the device's inform_url during adoption.

    Keep using the configured hostname for HTTP routing, Host and TLS verification;
    only the device-side JSON reports its resolved IPv4 address. Never persist a
    transient Docker address as the controller binding.
    """
    parsed = urlsplit(url)
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        loop = asyncio.get_running_loop()
        try:
            addresses = await loop.getaddrinfo(
                parsed.hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                family=socket.AF_INET,
                type=socket.SOCK_STREAM,
            )
        except OSError:
            raise ProtocolError("Controller IPv4 DNS resolution failed") from None
        if not addresses:
            raise ProtocolError("Controller has no IPv4 address") from None
        address = ipaddress.ip_address(addresses[0][4][0])
    if address.version != 4:
        raise ProtocolError("Controller advertised address must be IPv4 for this profile")
    authority = str(address) + (f":{parsed.port}" if parsed.port is not None else "")
    return urlunsplit((parsed.scheme, authority, parsed.path, "", ""))
