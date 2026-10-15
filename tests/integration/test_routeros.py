import asyncio
import json
import os
import sys
import time

import httpx
import pytest
from pydantic import SecretStr

from mtunifi.emulator import Emulator
from mtunifi.protocol import BOOTSTRAP_KEY, decode, encode
from mtunifi.routeros import RouterOSSource
from mtunifi.routeros_api import RouterOSError
from mtunifi.storage import Store

KEY = bytes.fromhex("1234567890abcdef1234567890abcdef")


@pytest.mark.parametrize("tls", [False, True])
async def test_actual_api_wire_normalization_and_encrypted_inform(routeros_peer, tmp_path, tls):
    peer = await routeros_peer(tls=tls)
    settings = peer.settings()
    source = RouterOSSource(settings)
    store = Store(tmp_path / "state")
    with store.lock():
        state = store.load_or_create()
    app = Emulator(settings, store, state, source)
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
        return httpx.Response(
            200, stream=httpx.ByteStream(encode(response, packet.mac, key, mode=packet.mode))
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(controller)) as client:
        assert await app.exchange(client)
        assert await app.exchange(client)
    assert peer.logins == 1  # Provisioning informs reuse the bounded polling cache.
    assert packets[1].mode == "gcm" and store.load().phase == "exchanging"
    body = packets[-1].body
    assert body["uptime"] == 93784 and body["system-stats"] == {"cpu": "23", "mem": "50.0"}
    assert len(body["port_table"]) == 8
    p1, p2, p3, _, p5, p6, p7, p8 = body["port_table"]
    assert p1["rx_bytes"] == peer.rx + 1  # Preserve integers above float precision.
    assert p1["tx_bytes"] == 1001 and p1["speed"] == 1000 and p1["is_uplink"]
    assert p2["speed"] == 100 and p2["up"]
    assert not p3["up"] and p3["speed"] == 0
    assert not p5["enable"] and not p6["up"]
    assert not p7["enable"] and not p8["enable"] and "rx_bytes" not in p7
    assert "lldp_table" not in body and all("mac_table" not in p for p in body["port_table"])
    assert body["mac"] == app.state.mac and not body["mac"].startswith("dc:2c:6e")
    report = store.load_telemetry()
    assert report["source"] == "routeros" and report["active_clients"] is None
    observation = report["observations"]
    assert len(observation["ports"]) == 6 and len(observation["logical_interfaces"]) == 3
    vlan = next(i for i in observation["logical_interfaces"] if i["kind"] == "vlan")
    assert vlan["vlan_id"] == 20 and vlan["parent"] == "bridge-lan"
    bond = next(i for i in observation["logical_interfaces"] if i["kind"] == "bonding")
    assert bond["members"] == ["ether3", "ether4"]
    member = observation["bridge_members"][0]
    assert member["pvid"] == 20 and member["hardware_offload_requested"] is True
    assert member["hardware_offload_active"] is None  # Configured hw=yes is not proof of offload.
    assert all(
        cmd[0] == "/login" or cmd[0].endswith(("/print", "/monitor")) for cmd in peer.commands
    )


async def test_poll_reconnect_optional_denial_unknown_link_details_and_reset(routeros_peer):
    peer = await routeros_peer()
    peer.traps["/interface/ethernet/monitor"] = "not enough permissions: PRIVATE-SERVER-MESSAGE"
    peer.traps["/interface/bridge/port/print"] = "no such command: PRIVATE-SERVER-MESSAGE"
    source = RouterOSSource(peer.settings(routeros_poll_interval=5))
    body = await source.collect(100, "")
    assert source.status == "degraded" and body["port_table"][0]["up"]
    assert "speed" not in body["port_table"][0]
    assert "full_duplex" not in body["port_table"][0]
    assert source.last_summary["observations"]["bridge_members"] is None
    assert "PRIVATE-SERVER-MESSAGE" not in json.dumps(source.last_summary)
    await source.collect(102, "")
    assert peer.logins == 1
    peer.traps["/interface/print"] = "not enough permissions: PRIVATE-SERVER-MESSAGE"
    with pytest.raises(RouterOSError, match="permission-denied"):
        await source.collect(106, "")
    assert source.status == "unavailable"
    assert source.last_summary["observations"]  # Stale data is for diagnosis only.
    with pytest.raises(RouterOSError):
        await source.collect(107, "")
    assert peer.logins == 2
    peer.traps.clear()
    peer.uptime, peer.rx = "2s", 1
    restored = await source.collect(111, "")
    assert peer.logins == 3 and restored["uptime"] == 2
    assert restored["port_table"][0]["rx_bytes"] == 2
    assert "counters:reset" in source.last_summary["observations"]["warnings"]
    assert source.last_summary["source_error"] is None


async def test_required_api_failure_suppresses_inform_and_keeps_adoption(routeros_peer, tmp_path):
    peer = await routeros_peer()
    settings = peer.settings()
    store = Store(tmp_path / "state")
    with store.lock():
        state = store.load_or_create()
    state.authkey = SecretStr(KEY.hex())
    state.phase, state.encryption = "exchanging", "gcm"
    store.save(state)
    peer.drop = "/interface/print"
    app = Emulator(settings, store, state)

    def unexpected(request):
        raise AssertionError("No inform can send failed or synthetic replacement observations")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        assert not await app.exchange(client)
    restored = store.load()
    assert restored.mac == state.mac and restored.authkey.get_secret_value() == KEY.hex()
    assert restored.phase == "exchanging" and restored.last_error == "RouterOS connection-closed"
    assert store.load_telemetry()["status"] == "unavailable"
    assert store.load_telemetry()["source_error_details"]["stage"] == "/interface/print"


async def test_tls_untrusted_certificate_is_rejected_before_credentials(routeros_peer):
    peer = await routeros_peer(tls=True)
    settings = peer.settings().model_copy(update={"routeros_ca_file": None})
    source = RouterOSSource(settings)
    with pytest.raises(RouterOSError, match="tls-verification-failed"):
        await source.collect(1, "")
    assert peer.logins == 0


async def test_tls_hostname_mismatch_rejected_before_credentials(routeros_peer, monkeypatch):
    peer = await routeros_peer(tls=True)
    settings = peer.settings().model_copy(update={"routeros_host": "wrong.example"})
    real_open = asyncio.open_connection

    async def loopback(host, port, **kwargs):
        return await real_open("127.0.0.1", port, **kwargs)

    monkeypatch.setattr(asyncio, "open_connection", loopback)
    with pytest.raises(RouterOSError, match="tls-verification-failed"):
        await RouterOSSource(settings).collect(1, "")
    assert peer.logins == 0


async def test_api_timeout_is_bounded(routeros_peer):
    peer = await routeros_peer()
    peer.stall = "/system/resource/print"
    started = time.monotonic()
    with pytest.raises(RouterOSError, match="timeout"):
        await RouterOSSource(peer.settings(routeros_timeout=1)).collect(1, "")
    assert time.monotonic() - started < 3


async def test_wrong_version_and_missing_named_port_are_explicit_failures(routeros_peer):
    peer = await routeros_peer()
    peer.version = "7.23.9"
    with pytest.raises(RouterOSError, match="requires-routeros-7.24"):
        await RouterOSSource(peer.settings()).collect(1, "")
    peer.version = "7.24.4"
    mapping = peer.directory / "bad-map.json"
    mapping.write_text(json.dumps({"ports": [{"port_idx": 1, "interface": "bridge-lan"}]}))
    settings = peer.settings().model_copy(update={"routeros_map_file": mapping})
    with pytest.raises(RouterOSError, match="mapped-physical-interface-missing"):
        await RouterOSSource(settings).collect(1, "")


async def test_authentication_error_is_redacted_and_not_treated_as_empty_data(routeros_peer):
    peer = await routeros_peer()
    peer.traps["/login"] = "invalid user name or password: PRIVATE-SERVER-MESSAGE"
    source = RouterOSSource(peer.settings())
    with pytest.raises(RouterOSError, match="authentication-failed") as exc:
        await source.collect(1, "")
    assert "PRIVATE-SERVER-MESSAGE" not in str(exc.value) + json.dumps(source.last_summary)
    assert source.last_summary["observations"] is None
    assert source.last_summary["source_error_details"]["stage"] == "/login"
    assert peer.commands == [("/login",)]


async def test_probe_cli_connection_refused_has_target_stage_and_no_secret(routeros_peer, tmp_path):
    peer = await routeros_peer()
    settings = peer.settings(state_dir=tmp_path / "unused")
    peer.server.close()
    await peer.server.wait_closed()
    env = os.environ.copy()
    for name, value in settings.model_dump(mode="json").items():
        if value is not None:
            env["MTUNIFI_" + name.upper()] = str(value)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "mtunifi.main",
        "probe-routeros",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), 15)
    error = json.loads(stderr)
    assert process.returncode == 2 and not stdout
    assert error["reason"] == "RouterOS connection-refused" and error["stage"] == "connect"
    assert error["errno"] is not None
    assert error["routeros_target"] == {
        "host": "127.0.0.1",
        "port": settings.routeros_port,
        "tls": False,
    }
    assert b"fixture-read-only-password" not in stdout + stderr
    assert not settings.state_dir.exists()


@pytest.mark.parametrize("invalid", ["-1", "", "18446744073709551616", "private-value"])
async def test_missing_or_invalid_required_counter_never_becomes_zero(
    routeros_peer, monkeypatch, invalid
):
    peer = await routeros_peer()
    original = peer.tables

    def broken_tables():
        tables = original()
        row = next(i for i in tables["/interface/print"] if i["name"] == "ether1")
        row["rx-byte"] = invalid
        return tables

    monkeypatch.setattr(peer, "tables", broken_tables)
    with pytest.raises(RouterOSError, match="required-interface-data-missing"):
        await RouterOSSource(peer.settings()).collect(1, "")


async def test_empty_optional_tables_and_unknown_health_stay_distinct(routeros_peer, monkeypatch):
    peer = await routeros_peer()
    original = peer.tables

    def optional_missing():
        tables = original()
        tables["/interface/bridge/port/print"] = []
        tables["/system/resource/print"][0].update({"cpu-load": "101", "free-memory": "999999999"})
        return tables

    monkeypatch.setattr(peer, "tables", optional_missing)
    source = RouterOSSource(peer.settings())
    body = await source.collect(1, "")
    assert source.status == "degraded" and "system-stats" not in body
    assert source.last_summary["observations"]["bridge_members"] == ()
    assert source.last_summary["observations"]["health"]["cpu_percent"] is None


async def test_probe_cli_has_no_controller_request_identity_or_secret_output(
    routeros_peer, tmp_path
):
    peer = await routeros_peer()
    settings = peer.settings(state_dir=tmp_path / "unused-state")
    env = os.environ.copy()
    for name, value in settings.model_dump(mode="json").items():
        if value is not None:
            env["MTUNIFI_" + name.upper()] = str(value)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "mtunifi.main",
        "probe-routeros",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), 15)
    assert process.returncode == 0, stderr.decode()
    assert json.loads(stdout)["observations"]["health"]["board"] == "RB760iGS"
    assert b"fixture-read-only-password" not in stdout + stderr
    assert not settings.state_dir.exists()


async def test_unavailable_or_old_routeros_data_makes_health_fail(
    routeros_peer, tmp_path, monkeypatch, capsys
):
    from mtunifi.main import main

    peer = await routeros_peer()
    settings = peer.settings(state_dir=tmp_path / "state")
    source = RouterOSSource(settings)
    await source.collect(100, "")
    store = Store(settings.state_dir)
    with store.lock():
        state = store.load_or_create()
    state.last_inform, state.controller_url = time.time(), settings.inform_url
    store.save(state)
    store.save_telemetry(source.last_summary)
    for name, value in settings.model_dump(mode="json").items():
        if value is not None:
            monkeypatch.setenv("MTUNIFI_" + name.upper(), str(value))
    monkeypatch.setattr("sys.argv", ["mtunifi", "health"])
    assert main() == 0
    stale = source.last_summary | {"observed_at": time.time() - 1000}
    store.save_telemetry(stale)
    assert main() == 1
    store.save_telemetry(source.last_summary | {"status": "unavailable"})
    assert main() == 1
    monkeypatch.setattr("sys.argv", ["mtunifi", "diagnose"])
    assert main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["discovered_ports"] == 6 and report["synthetic_ports"] == 0
    assert report["wired_clients"] is None and report["routeros"] == "unavailable"
    # Switching source must not label the persisted M1 demo as live MikroTik observations.
    store.save_telemetry({"source": "synthetic", "status": "active", "active_clients": 3})
    assert main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["routeros_telemetry"] is None and report["wired_clients"] is None
