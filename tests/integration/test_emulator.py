import asyncio
import json
import os
import socket
import time
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from mtunifi.config import Settings
from mtunifi.emulator import Emulator
from mtunifi.protocol import BOOTSTRAP_KEY, decode, encode
from mtunifi.storage import Store

KEY = bytes.fromhex("1234567890abcdef1234567890abcdef")
DEMO = Path(__file__).resolve().parents[2] / "examples/synthetic-switch.json"


def emulator(tmp_path):
    store = Store(tmp_path)
    return Emulator(Settings(), store, store.load_or_create())


async def test_loopback_http_adoption_key_rotation_restart_and_outage(tmp_path):
    """Real HTTP socket and encrypted responses; synthetic peer, not a UniFi controller."""
    requests = []

    async def peer(reader, writer):
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            length = int(
                next(
                    line.split(b":", 1)[1]
                    for line in header.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
            )
            key = BOOTSTRAP_KEY if not requests else KEY
            packet = decode(await reader.readexactly(length), key)
            requests.append(packet)
            body = (
                {"_type": "setparam", "mgmt_cfg": f"authkey={KEY.hex()}\nuse_aes_gcm=1"}
                if len(requests) == 1
                else {"_type": "noop", "interval": 10}
            )
            data = encode(body, packet.mac, key, mode=packet.mode)
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: "
                + str(len(data)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + data
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(peer, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    app = emulator(tmp_path)
    app.settings.inform_url = f"http://127.0.0.1:{port}/inform"
    async with server, httpx.AsyncClient(trust_env=False) as client:
        assert await app.exchange(client)
        assert app.state.phase == "key-received"
        assert await app.exchange(client)
        assert app.state.phase == "exchanging"
        # Process restart restores private key and negotiated encryption.
        restored = Emulator(app.settings, app.store, app.store.load())
        assert await restored.exchange(client)
        assert requests[2].mode == "gcm"
        assert requests[2].body["default"] is False
        assert len(requests[2].body["port_table"]) == 8
    async with httpx.AsyncClient(trust_env=False) as client:
        assert not await restored.exchange(client)
    assert restored.state.last_error == "ConnectError"
    # Controller restarting on the same endpoint can resume with retained identity/key.
    restarted = await asyncio.start_server(peer, "127.0.0.1", port)
    async with restarted, httpx.AsyncClient(trust_env=False) as client:
        assert await restored.exchange(client)
    assert restored.state.last_error is None


@pytest.mark.parametrize("status", [302, 400, 404, 500])
async def test_http_failure_preserves_identity(tmp_path, status):
    app = emulator(tmp_path)
    mac = app.state.mac
    transport = httpx.MockTransport(lambda _: httpx.Response(status, content=b"sensitive"))
    async with httpx.AsyncClient(transport=transport) as client:
        assert not await app.exchange(client)
    assert app.store.load().mac == mac
    assert app.state.last_inform is None
    assert app.state.authkey is None


@pytest.mark.parametrize("variant", ["wrong-mac", "bad-frame", "oversized", "downgrade"])
async def test_invalid_controller_response(tmp_path, variant):
    app = emulator(tmp_path)
    if variant == "downgrade":
        app.settings.encryption = "gcm"
    mac = "02:00:00:00:00:01" if variant == "wrong-mac" else app.state.mac
    data = encode({"_type": "noop"}, mac)
    if variant == "bad-frame":
        data = b"invalid response with secret"
    elif variant == "oversized":
        data = b"x" * (1024 * 1024 + 41)
    transport = httpx.MockTransport(lambda _: httpx.Response(200, stream=httpx.ByteStream(data)))
    async with httpx.AsyncClient(transport=transport) as client:
        assert not await app.exchange(client)
    assert app.state.last_inform is None


async def test_controller_change_requires_separate_identity(tmp_path):
    app = emulator(tmp_path)
    app.state.controller_url = "http://other:8080/inform"
    async with httpx.AsyncClient() as client:
        with pytest.raises(RuntimeError):
            await app.exchange(client)


async def test_dns_transport_keeps_hostname_but_advertises_ipv4(tmp_path, monkeypatch):
    app = emulator(tmp_path)
    app.settings.inform_url = "http://unifi-network-application:8080/inform"
    resolver = AsyncMock(
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.30.88.2", 8080))]
    )
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolver)

    def controller(request):
        assert request.url.host == "unifi-network-application"
        assert request.headers["host"] == "unifi-network-application:8080"
        packet = decode(request.content)
        # Regression for real UniFi 10.6.106: hostname here causes invalid inform_ip / 400.
        assert packet.body["inform_url"] == "http://172.30.88.2:8080/inform"
        response = encode({"_type": "setparam", "mgmt_cfg": f"authkey={KEY.hex()}"}, packet.mac)
        return httpx.Response(200, stream=httpx.ByteStream(response))

    async with httpx.AsyncClient(transport=httpx.MockTransport(controller)) as client:
        assert await app.exchange(client)
    assert app.state.phase == "key-received"
    assert app.store.load().controller_url == app.settings.inform_url


async def test_dns_error_is_persisted_without_sending_request(tmp_path, monkeypatch):
    app = emulator(tmp_path)
    app.settings.inform_url = "http://controller:8080/inform"
    resolver = AsyncMock(side_effect=socket.gaierror("private details"))
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolver)

    def unexpected(request):
        raise AssertionError("DNS failure must prevent HTTP request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        assert not await app.exchange(client)
    assert app.store.load().last_error == "Controller IPv4 DNS resolution failed"
    assert app.state.last_telemetry is None


async def test_dns_lookup_is_bounded_by_timeout(tmp_path, monkeypatch):
    app = emulator(tmp_path)
    app.settings.inform_url = "http://controller:8080/inform"
    app.settings.timeout = 0.01

    async def stalled_resolver(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", stalled_resolver)
    async with httpx.AsyncClient() as client:
        assert not await app.exchange(client)
    assert app.store.load().last_error == "Controller IPv4 DNS resolution failed"


async def test_scenario_reload_over_encrypted_inform_retains_adoption(tmp_path):
    scenario = json.loads(DEMO.read_text())
    config = tmp_path / "scene.json"
    config.write_text(json.dumps(scenario))
    store = Store(tmp_path)
    app = Emulator(Settings(synthetic_file=config), store, store.load_or_create())
    packets = []

    def controller(request):
        key = BOOTSTRAP_KEY if not packets else KEY
        packet = decode(request.content, key)
        packets.append(packet)
        response = (
            {"_type": "setparam", "mgmt_cfg": f"authkey={KEY.hex()}\nuse_aes_gcm=1"}
            if len(packets) == 1
            else {"_type": "noop"}
        )
        raw = encode(response, packet.mac, key, mode=packet.mode)
        return httpx.Response(200, stream=httpx.ByteStream(raw))

    async with httpx.AsyncClient(transport=httpx.MockTransport(controller)) as client:
        assert await app.exchange(client)
        scenario["ports"][1]["up"] = False
        config.write_text(json.dumps(scenario))
        assert await app.exchange(client)
        config.write_text('{"private":"never-log-this"}')
        assert await app.exchange(client)
    assert packets[0].body["port_table"][1]["mac_table"]
    assert packets[1].mode == "gcm"
    assert not packets[1].body["port_table"][1]["up"]
    assert packets[1].body["port_table"][1]["mac_table"] == []
    assert len(packets[1].body["lldp_table"]) == 1
    assert packets[2].body["port_table"][1]["mac_table"] == []
    assert store.load().phase == "exchanging"
    report = store.load_telemetry()
    assert report["status"] == "using-last-good" and report["active_clients"] == 2
    assert "authkey" not in json.dumps(report)
    assert len({packet.mac for packet in packets}) == 1


@pytest.mark.controller
async def test_real_controller_inform():
    if os.environ.get("MTUNIFI_ALLOW_CONTROLLER_TEST") != "yes":
        pytest.skip("Explicit authorization and lab inform URL required")
    url = os.environ["MTUNIFI_INFORM_URL"]
    directory = Path(os.environ["MTUNIFI_STATE_DIR"])
    settings = Settings.from_env()
    assert settings.inform_url == url
    store = Store(directory)
    with store.lock():
        app = Emulator(settings, store, store.load_or_create())
        before = time.time()
        assert await app.run(once=True), "No valid encrypted inform response; inspect safe CLI logs"
        assert app.state.last_inform >= before
        print(json.dumps(app.state.public()))
    # This test only establishes valid protocol exchange. UI adoption is a separate manual check.
