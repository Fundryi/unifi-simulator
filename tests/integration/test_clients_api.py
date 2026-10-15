import json
import time
from types import SimpleNamespace

import httpx
import pytest

from mtunifi.emulator import Emulator
from mtunifi.protocol import BOOTSTRAP_KEY, decode, encode
from mtunifi.routeros import RouterOSSource
from mtunifi.storage import Store

A = "00:11:22:33:44:55"
B = "02:ab:00:00:00:02"
KEY = bytes.fromhex("1234567890abcdef1234567890abcdef")


async def test_live_clients_move_expire_fdb_gap_recover_keep_gcm_adoption(
    routeros_peer, tmp_path, monkeypatch
):
    peer = await routeros_peer()
    settings = peer.settings(routeros_poll_interval=5)
    store = Store(tmp_path)
    state = store.load_or_create()
    tick = [100]
    monkeypatch.setattr(
        "mtunifi.emulator.time", SimpleNamespace(monotonic=lambda: tick[0], time=time.time)
    )
    original = peer.tables
    phase = ["initial"]

    def tables():
        result = original()
        if phase[0] == "empty":
            result["/interface/bridge/host/print"] = []
        else:
            result["/interface/bridge/host/print"].append(
                {
                    "mac-address": state.mac,
                    "bridge": "bridge-lan",
                    "on-interface": "ether2",
                    "dynamic": "true",
                }
            )
        if phase[0] == "move":
            result["/interface/bridge/host/print"][0]["on-interface"] = "ether1"
            result["/ip/arp/print"] = []
            result["/ip/dhcp-server/lease/print"] = []
        return result

    monkeypatch.setattr(peer, "tables", tables)
    app = Emulator(settings, store, state)
    packets = []

    def controller(request):
        key = KEY if packets else BOOTSTRAP_KEY
        packet = decode(request.content, key)
        packets.append(packet)
        response = (
            {"_type": "noop"}
            if len(packets) > 1
            else {"_type": "setparam", "mgmt_cfg": f"authkey={KEY.hex()}\nuse_aes_gcm=1"}
        )
        return httpx.Response(
            200, stream=httpx.ByteStream(encode(response, packet.mac, key, mode=packet.mode))
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(controller)) as client:
        assert await app.exchange(client)
        initial = packets[-1].body["port_table"][1]["mac_table"]
        assert len(initial) == 2
        assert next(c for c in initial if c["mac"] == A)["hostname"] == "fixture-client"
        assert "ip" not in next(c for c in initial if c["mac"] == B)
        assert store.load_telemetry()["observations"]["wired"]["excluded_local"] == 3
        tick[0] = 103
        assert await app.exchange(client)
        assert packets[-1].body["port_table"][1]["mac_table"][0]["age"] == 3
        assert peer.logins == 1
        phase[0], tick[0] = "move", 106
        assert await app.exchange(client)
        moved = packets[-1].body["port_table"][0]["mac_table"]
        assert [c["mac"] for c in moved] == [A] and moved[0]["uptime"] == 0
        assert "ip" not in moved[0]
        phase[0], tick[0] = "empty", 112
        assert await app.exchange(client)
        assert all(p["mac_table"] == [] for p in packets[-1].body["port_table"])
        assert store.load_telemetry()["active_clients"] == 0
        peer.traps["/interface/bridge/host/print"] = "not enough permissions: PRIVATE"
        phase[0], tick[0] = "initial", 118
        assert await app.exchange(client)
        assert all("mac_table" not in p for p in packets[-1].body["port_table"])
        summary = store.load_telemetry()
        assert summary["clients_status"] == "unavailable" and summary["active_clients"] is None
        assert summary["status"] == "degraded" and "PRIVATE" not in json.dumps(summary)
        peer.traps.clear()
        tick[0] = 124
        assert await app.exchange(client)
        assert store.load_telemetry()["active_clients"] == 2
    assert all(p.mode == "gcm" for p in packets[1:])
    assert len({p.mac for p in packets}) == 1 and state.phase == "exchanging"
    assert state.authkey.get_secret_value() == KEY.hex()


@pytest.mark.parametrize(
    "path,ip_source",
    [
        ("/ip/arp/print", "dhcp"),
        ("/ip/dhcp-server/lease/print", "arp"),
        ("/ip/dhcp-server/print", "arp"),
    ],
)
async def test_optional_enrichment_permission_failure_keeps_fdb_attachment(
    routeros_peer, path, ip_source
):
    peer = await routeros_peer()
    peer.traps[path] = "not enough permissions PRIVATE"
    source = RouterOSSource(peer.settings())
    body = await source.collect(100, "")
    assert source.last_summary["active_clients"] == 2 and source.status == "degraded"
    entry = next(c for c in body["port_table"][1]["mac_table"] if c["mac"] == A)
    assert entry["ip"] == "192.0.2.50"
    client = next(
        c for c in source.last_summary["observations"]["wired"]["clients"] if c["mac"] == A
    )
    assert client["ip_sources"] == (ip_source,)
    assert "PRIVATE" not in json.dumps(source.last_summary)


async def test_broken_optional_stream_stops_poll_and_invalidates_tracking(routeros_peer):
    peer = await routeros_peer()
    source = RouterOSSource(peer.settings(routeros_poll_interval=5))
    await source.collect(100, "")
    peer.drop = "/ip/arp/print"
    from mtunifi.routeros_api import RouterOSError

    with pytest.raises(RouterOSError, match="connection-closed"):
        await source.collect(106, "")
    assert source.last_summary["clients_status"] == "unavailable"
    assert source.last_summary["active_clients"] is None
    peer.drop = None
    body = await source.collect(112, "")
    assert body["port_table"][1]["mac_table"][0]["uptime"] == 0


async def test_native_api_vlan_aware_same_mac_is_scoped_to_two_vlan_interfaces(
    routeros_peer, monkeypatch
):
    peer = await routeros_peer()
    original = peer.tables

    def tables():
        result = original()
        result["/interface/bridge/print"][0]["vlan-filtering"] = "true"
        result["/interface/print"].append({"name": "vlan30", "type": "vlan"})
        result["/interface/vlan/print"].append(
            {"name": "vlan30", "interface": "bridge-lan", "vlan-id": "30"}
        )
        result["/interface/bridge/vlan/print"] = [
            {
                "bridge": "bridge-lan",
                "vlan-ids": "20,30",
                "tagged": "bridge-lan,ether1,ether2",
                "current-tagged": "bridge-lan,ether1,ether2",
                "current-untagged": "",
                "dynamic": "false",
            }
        ]
        result["/interface/bridge/host/print"] = [
            {
                "mac-address": A,
                "on-interface": p,
                "bridge": "bridge-lan",
                "vid": v,
                "dynamic": "true",
            }
            for p, v in (("ether2", "20"), ("ether1", "30"))
        ]
        result["/ip/arp/print"] = [
            {"mac-address": A, "address": ip, "interface": interface, "status": "reachable"}
            for interface, ip in (("vlan20", "192.0.2.20"), ("vlan30", "198.51.100.30"))
        ]
        result["/ip/dhcp-server/lease/print"] = []
        return result

    monkeypatch.setattr(peer, "tables", tables)
    source = RouterOSSource(peer.settings())
    body = await source.collect(100, "")
    assert body["port_table"][0]["mac_table"][0]["vlan"] == 30
    assert body["port_table"][0]["mac_table"][0]["ip"] == "198.51.100.30"
    assert body["port_table"][1]["mac_table"][0]["vlan"] == 20
    assert body["port_table"][1]["mac_table"][0]["ip"] == "192.0.2.20"
    assert len(source.last_summary["observations"]["bridge_vlans"]) == 1


async def test_oversized_fdb_is_not_silently_reported_as_complete(routeros_peer, monkeypatch):
    from mtunifi.routeros_api import MAX_ROWS, RouterOSError

    peer = await routeros_peer()
    original = peer.tables

    def tables():
        result = original()
        result["/interface/bridge/host/print"] = [result["/interface/bridge/host/print"][0]] * (
            MAX_ROWS + 1
        )
        return result

    monkeypatch.setattr(peer, "tables", tables)
    source = RouterOSSource(peer.settings())
    with pytest.raises(RouterOSError, match="too-many-rows"):
        await source.collect(100, "")
    assert source.last_summary["active_clients"] is None
    assert source.last_summary["source_error_details"]["stage"] == "/interface/bridge/host/print"
