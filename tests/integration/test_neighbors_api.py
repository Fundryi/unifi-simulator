import json
import time
from types import SimpleNamespace

import httpx
import pytest

from mtunifi.emulator import Emulator
from mtunifi.protocol import BOOTSTRAP_KEY, decode, encode
from mtunifi.routeros import RouterOSSource
from mtunifi.routeros_api import RouterOSError
from mtunifi.storage import Store

PATH = "/ip/neighbor/print"
KEY = bytes.fromhex("1234567890abcdef1234567890abcdef")
NEIGHBOR = {
    "interface": "ether2,bridge-lan",
    "mac-address": "00:11:22:33:44:99",
    "interface-name": "Gi1/0/8",
    "identity": "upstream",
    "discovered-by": "lldp",
    "system-description": "fixture switch",
    "age": "3s",
}


@pytest.mark.parametrize("tls", [False, True])
async def test_neighbors_gcm_move_empty_unknown_recover_keep_adoption(
    routeros_peer, tmp_path, monkeypatch, tls
):
    peer = await routeros_peer(tls=tls)
    original = peer.tables
    neighbors = [NEIGHBOR.copy()]

    def tables():
        result = original()
        result[PATH] = neighbors.copy()
        return result

    monkeypatch.setattr(peer, "tables", tables)
    settings = peer.settings(routeros_poll_interval=5)
    store = Store(tmp_path / "state")
    with store.lock():
        state = store.load_or_create()
    tick = [100]
    monkeypatch.setattr(
        "mtunifi.emulator.time", SimpleNamespace(monotonic=lambda: tick[0], time=time.time)
    )
    app = Emulator(settings, store, state)
    packets = []

    def controller(request):
        key = KEY if packets else BOOTSTRAP_KEY
        packet = decode(request.content, key)
        packets.append(packet)
        body = (
            {"_type": "noop"}
            if len(packets) > 1
            else {"_type": "setparam", "mgmt_cfg": f"authkey={KEY.hex()}\nuse_aes_gcm=1"}
        )
        return httpx.Response(
            200, stream=httpx.ByteStream(encode(body, packet.mac, key, mode=packet.mode))
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(controller)) as client:
        assert await app.exchange(client)
        assert packets[-1].body["lldp_table"][0]["local_port_idx"] == 2
        tick[0] = 103
        assert await app.exchange(client) and peer.logins == 1
        assert packets[-1].mode == "gcm"
        summary = store.load_telemetry()
        assert summary["neighbors"] == 1 and summary["neighbor_observations"] == 1
        observation = summary["observations"]["neighbors"]["observations"][0]
        assert (
            observation["age_seconds"] == 3 and observation["evidence"] == "routeros-neighbor-cache"
        )
        assert len(packets[-1].body["port_table"][1]["mac_table"]) == 2
        neighbors[0]["interface"] = "ether1"
        tick[0] = 106
        assert await app.exchange(client)
        assert packets[-1].body["lldp_table"][0]["local_port_idx"] == 1
        peer.traps[PATH] = "not enough permissions PRIVATE"
        tick[0] = 112
        assert await app.exchange(client)
        assert "lldp_table" not in packets[-1].body
        summary = store.load_telemetry()
        assert summary["neighbors"] is None and summary["neighbors_status"] == "unavailable"
        assert summary["status"] == "degraded" and "PRIVATE" not in json.dumps(summary)
        peer.traps.clear()
        neighbors.clear()
        tick[0] = 118
        assert await app.exchange(client)
        assert packets[-1].body["lldp_table"] == []
        assert store.load_telemetry()["neighbors"] == 0
        neighbors.extend(
            [NEIGHBOR, {**NEIGHBOR, "discovered-by": "mndp", "mac-address": "00:11:22:33:44:98"}]
        )
        tick[0] = 124
        assert await app.exchange(client)
        assert len(packets[-1].body["lldp_table"]) == 1
        assert store.load_telemetry()["neighbor_observations"] == 2
        neighbors.append({})
        tick[0] = 130
        assert await app.exchange(client)
        assert "lldp_table" not in packets[-1].body
        summary = store.load_telemetry()
        assert summary["neighbors"] is None and summary["status"] == "degraded"
        assert len(summary["observations"]["neighbors"]["observations"]) == 3
        assert summary["observations"]["neighbors"]["invalid_rows"] == 1
    assert state.phase == "exchanging" and state.authkey.get_secret_value() == KEY.hex()
    assert len({p.mac for p in packets}) == 1
    command = next(cmd for cmd in peer.commands if cmd[0] == PATH)
    assert command == (
        PATH,
        "=.proplist=interface,mac-address,identity,interface-name,discovered-by,age,system-description",
    )


async def test_broken_neighbor_stream_suppresses_inform_not_synthetic_fallback(routeros_peer):
    peer = await routeros_peer()
    source = RouterOSSource(peer.settings())
    await source.collect(100, "02:00:00:00:00:01")
    peer.drop = PATH
    with pytest.raises(RouterOSError, match="connection-closed"):
        await source.collect(111, "02:00:00:00:00:01")
    assert source.last_summary["neighbors"] is None
    assert source.last_summary["status"] == "unavailable"
    assert source.last_summary["source_error_details"]["stage"] == PATH


async def test_oversized_neighbor_response_fails_poll_without_partial_topology(
    routeros_peer, monkeypatch
):
    peer = await routeros_peer()
    original = peer.tables

    def tables():
        result = original()
        result[PATH] = [NEIGHBOR] * 4097
        return result

    monkeypatch.setattr(peer, "tables", tables)
    source = RouterOSSource(peer.settings())
    with pytest.raises(RouterOSError, match="too-many-rows"):
        await source.collect(100, "02:00:00:00:00:01")
    assert source.last_summary["neighbors"] is None
