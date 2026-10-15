import asyncio
import socket
from unittest.mock import AsyncMock

import pytest

from mtunifi.protocol import ProtocolError
from mtunifi.transport import advertised_inform_url


@pytest.mark.parametrize("url", ["http://192.0.2.1:8080/inform", "https://192.0.2.1/inform"])
async def test_literal_address_needs_no_dns(url, monkeypatch):
    resolver = AsyncMock(side_effect=AssertionError("Unexpected DNS"))
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolver)
    assert await advertised_inform_url(url) == url
    resolver.assert_not_called()


@pytest.mark.parametrize("scheme,port", [("http", ":8080"), ("https", ""), ("https", ":8443")])
async def test_hostname_is_resolved_for_payload_only(scheme, port, monkeypatch):
    resolver = AsyncMock(
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.30.88.2", 8080))]
    )
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolver)
    url = f"{scheme}://unifi-network-application{port}/inform"
    assert await advertised_inform_url(url) == f"{scheme}://172.30.88.2{port}/inform"
    assert resolver.call_args.kwargs["family"] == socket.AF_INET


async def test_dns_failure_is_safe(monkeypatch):
    resolver = AsyncMock(side_effect=socket.gaierror("private DNS details"))
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolver)
    with pytest.raises(ProtocolError, match="^Controller IPv4 DNS resolution failed$"):
        await advertised_inform_url("http://controller:8080/inform")


async def test_ipv6_is_explicitly_unsupported():
    with pytest.raises(ProtocolError, match="must be IPv4"):
        await advertised_inform_url("http://[::1]:8080/inform")
