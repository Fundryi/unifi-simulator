from dataclasses import replace

import pytest

from mtunifi.clients import ClientTracker, correlate_clients, mac_address, vlan_ranges
from mtunifi.snapshot import (
    BridgeMember,
    DeviceHealth,
    DeviceSnapshot,
    LogicalInterface,
    PhysicalPort,
    unifi_snapshot,
)

A = "00:11:22:33:44:55"  # Ordinary globally administered MACs are accepted for observations.
B = "02:ab:00:00:00:02"
OWN = "02:00:00:00:00:ff"
LOCAL = "dc:2c:6e:00:00:01"


def host(mac=A, interface="ether2", vid="20", bridge="br", **fields):
    return {
        "mac-address": mac,
        "on-interface": interface,
        "vid": vid,
        "bridge": bridge,
        "dynamic": "true",
        **fields,
    }


def scene(
    hosts=None,
    *,
    arp=None,
    leases=None,
    servers=None,
    filtering="true",
    bridge_rows=None,
    vlan_rows=None,
    snapshot=None,
    **overrides,
):
    ports = tuple(
        PhysicalPort(i, f"ether{i}", f"ether{i}", LOCAL, True, True, 1000, True, True, 0, 0)
        for i in range(1, 4)
    )
    snapshot = snapshot or DeviceSnapshot(
        DeviceHealth("7.24.4", 100),
        ports,
        (
            LogicalInterface("br", "bridge"),
            LogicalInterface("v20", "vlan", "br", 20),
            LogicalInterface("v30", "vlan", "br", 30),
            LogicalInterface("bond1", "bonding", members=("ether2", "ether3")),
        ),
        tuple(BridgeMember(f"ether{i}", "br", 1, True, None, True) for i in range(1, 4)),
    )
    interfaces = {p.name: {"name": p.name, "type": "ether", "mac-address": LOCAL} for p in ports}
    interfaces.update(
        {i.name: {"name": i.name, "type": i.kind} for i in snapshot.logical_interfaces}
    )
    arguments = dict(
        interfaces=interfaces,
        bridge_rows=bridge_rows
        if bridge_rows is not None
        else [
            {
                "name": "br",
                "mac-address": LOCAL,
                "vlan-filtering": filtering,
                "pvid": "1",
                "ageing-time": "5m",
            }
        ],
        vlan_rows=vlan_rows
        if vlan_rows is not None
        else [
            {"bridge": "br", "vlan-ids": "1", "current-untagged": "br,ether1"},
            {
                "bridge": "br",
                "vlan-ids": "20-30",
                "tagged": "br,ether2,ether3",
                "current-tagged": "br,ether2,ether3",
                "current-untagged": "",
                "dynamic": "true",
            },
        ],
        hosts=hosts if hosts is not None else [host()],
        arp=arp or [],
        leases=leases or [],
        servers=servers or [],
        device_mac=OWN,
    )
    arguments.update(overrides)
    return correlate_clients(snapshot, **arguments)


def entries(snapshot, **kwargs):
    return [
        c for p in unifi_snapshot(snapshot, **kwargs)["port_table"] for c in p.get("mac_table", [])
    ]


def test_fdb_authority_global_mac_unknown_ip_and_multiple_clients_per_port():
    snapshot = scene(
        [host(), host(B)],
        arp=[
            {
                "mac-address": "00:11:22:33:44:99",
                "address": "192.0.2.99",
                "interface": "v20",
                "status": "reachable",
            }
        ],
        leases=[
            {
                "mac-address": "00:11:22:33:44:98",
                "address": "192.0.2.98",
                "status": "bound",
                "server": "dhcp",
            }
        ],
    )
    table = unifi_snapshot(snapshot)["port_table"][1]["mac_table"]
    assert {c["mac"] for c in table} == {A, B} and len(table) == 2
    assert all(c["vlan"] == 20 and "ip" not in c for c in table)
    assert snapshot.wired.clients[0].ip is None


def test_vlan_scoped_arp_and_dhcp_do_not_cross_duplicate_mac_vlans():
    snapshot = scene(
        [host(), host(vid="30", interface="ether3")],
        arp=[
            {"mac-address": A, "address": "192.0.2.20", "interface": "v20", "status": "reachable"},
            {"mac-address": A, "address": "198.51.100.30", "interface": "v30", "status": "stale"},
        ],
        leases=[
            {
                "mac-address": A,
                "address": "192.0.2.20",
                "server": "d20",
                "status": "bound",
                "host-name": "workstation",
            }
        ],
        servers=[{"name": "d20", "interface": "v20"}],
    )
    by_vid = {c["vlan"]: c for c in entries(snapshot)}
    assert by_vid[20]["ip"] == "192.0.2.20" and by_vid[20]["hostname"] == "workstation"
    assert by_vid[30]["ip"] == "198.51.100.30" and "hostname" not in by_vid[30]
    assert snapshot.wired.clients[0].ip_sources == ("arp", "dhcp")


def test_unfiltered_bridge_unknown_vlan_is_not_invented_and_ip_can_be_enriched():
    snapshot = scene(
        [host(vid="0")],
        filtering="false",
        arp=[{"mac-address": A, "address": "192.0.2.3", "interface": "br", "complete": "true"}],
    )
    assert entries(snapshot)[0]["ip"] == "192.0.2.3"
    assert "vlan" not in entries(snapshot)[0]
    assert snapshot.wired.clients[0].vlan is None
    assert scene([host(vid="1")], filtering="false").wired.clients[0].vlan is None


def test_vlan_filtered_cpu_bridge_enrichment_requires_observed_untagged_membership():
    arp = [{"mac-address": A, "address": "192.0.2.1", "interface": "br", "status": "permanent"}]
    assert entries(scene([host(vid="1")], arp=arp))[0]["ip"] == "192.0.2.1"
    assert "ip" not in entries(scene([host(vid="1")], arp=arp, vlan_rows=[]))[0]
    assert "ip" not in entries(scene([host(vid="20")], arp=arp))[0]


def test_multiple_ip_conflict_is_unknown_and_disappearing_arp_keeps_client_attached():
    arp = [{"mac-address": A, "address": "192.0.2.1", "interface": "v20", "status": "reachable"}]
    leases = [{"mac-address": A, "address": "192.0.2.2", "server": "d20", "status": "bound"}]
    snapshot = scene(arp=arp, leases=leases, servers=[{"name": "d20", "interface": "v20"}])
    assert "ip" not in entries(snapshot)[0]
    assert snapshot.wired.clients[0].ip_candidates == ("192.0.2.1", "192.0.2.2")
    assert "clients:ip-conflict" in snapshot.warnings
    assert len(entries(scene(arp=[]))) == 1


@pytest.mark.parametrize("status", ["failed", "incomplete", "unknown"])
def test_incomplete_arp_does_not_enrich(status):
    snapshot = scene(
        arp=[{"mac-address": A, "address": "192.0.2.2", "interface": "v20", "status": status}]
    )
    assert "ip" not in entries(snapshot)[0]


def test_dhcp_bound_active_identity_and_server_scope_not_configured_or_waiting_lease():
    leases = [
        {
            "mac-address": B,
            "active-mac-address": A,
            "address": "192.0.2.99",
            "active-address": "192.0.2.20",
            "server": "d20",
            "status": "bound",
        },
        {"mac-address": A, "address": "192.0.2.30", "server": "d20", "status": "waiting"},
    ]
    snapshot = scene(leases=leases, servers=[{"name": "d20", "interface": "v20"}])
    assert entries(snapshot)[0]["ip"] == "192.0.2.20"
    assert "ip" not in entries(scene(leases=leases, servers=[]))[0]


def test_local_interface_bridge_admin_and_emulator_macs_are_excluded():
    admin = "dc:2c:6e:00:00:bb"
    bridge = [{"name": "br", "vlan-filtering": "true", "pvid": "1", "admin-mac": admin}]
    snapshot = scene(
        [
            host(),
            host(LOCAL.upper()),
            host(OWN),
            host(admin),
            host("00:11:22:33:44:88", local="true"),
            host("01:00:5e:00:00:01"),
            host("00:00:00:00:00:00"),
        ],
        bridge_rows=bridge,
    )
    assert [c["mac"] for c in entries(snapshot)] == [A]
    assert snapshot.wired.excluded_local == 4 and snapshot.wired.skipped_rows == 2
    assert mac_address(A) == A


@pytest.mark.parametrize(
    "interface, reason", [("bond1", "aggregate-interface"), ("ether9", "unmapped-interface")]
)
def test_no_arbitrary_physical_port_inference(interface, reason):
    snapshot = scene([host(interface=interface)])
    assert not entries(snapshot) and snapshot.wired.clients[0].attachment == reason


def test_conflicting_fdb_ports_and_bridge_namespaces_are_suppressed():
    snapshot = scene([host(), host(interface="ether3")])
    assert not entries(snapshot) and snapshot.wired.clients[0].port_idx is None
    assert snapshot.wired.clients[0].conflicting_interfaces == ("ether2", "ether3")
    snapshot = scene([host(), host(bridge="other", interface="ether3")])
    assert not entries(snapshot)
    assert "clients:ambiguous-unifi-namespace" in snapshot.warnings
    assert len(entries(scene([host(), host()]))) == 1


def test_external_offloaded_learning_is_kept_static_and_missing_vlan_are_not_fabricated():
    snapshot = scene([host(dynamic="false", external="true")])
    assert len(entries(snapshot)) == 1 and snapshot.wired.clients[0].external is True
    assert entries(snapshot)[0]["static"] is False
    assert not entries(scene([host(dynamic="false", external="false")]))
    assert not entries(scene([host(vid="0")]))
    assert scene([host(vid="0")]).wired.clients[0].attachment == "vlan-unresolved"


def test_fdb_unavailable_malformed_and_empty_are_three_distinct_states():
    empty = scene(hosts=[])
    assert empty.wired.clients == ()  # Complete, authoritative empty FDB.
    base = scene()
    # Explicit None bypasses the helper's default populated table.
    unavailable = correlate_clients(
        base,
        interfaces={},
        bridge_rows=[],
        vlan_rows=[],
        hosts=None,
        arp=None,
        leases=None,
        servers=None,
    )
    assert unavailable.wired.clients is None
    assert all("mac_table" not in p for p in unifi_snapshot(unavailable)["port_table"])
    invalid = scene([host(), {"mac-address": "bad-private-data"}])
    assert invalid.wired.clients is None and invalid.wired.sources["fdb"] == "invalid"
    assert "bad-private-data" not in str(invalid.public())


def test_tracking_age_move_removal_reappearance_and_unknown_gap():
    tracker = ClientTracker()
    first = tracker.observe(scene(), 100)
    assert entries(first)[0]["uptime"] == 0
    later = tracker.observe(scene(), 110)
    assert entries(later, age_seconds=3)[0]["uptime"] == 13
    assert entries(later, age_seconds=3)[0]["age"] == 3
    moved = tracker.observe(scene([host(interface="ether3")]), 120)
    assert entries(moved)[0]["uptime"] == 0
    assert not entries(tracker.observe(scene([]), 130))
    assert entries(tracker.observe(scene(), 140))[0]["uptime"] == 0
    tracker.observe(replace(first, wired=replace(first.wired, clients=None)), 150)
    assert entries(tracker.observe(scene(), 160))[0]["uptime"] == 0


def test_vlan_ranges_are_bounded_and_preserve_dynamic_operational_membership():
    assert vlan_ranges("30,10-20") == ((10, 20), (30, 30))
    for value in ("0", "4095", "20-10", "private-value", ",".join(["1"] * 65)):
        assert vlan_ranges(value) is None
    snapshot = scene()
    assert snapshot.bridge_vlans[1].dynamic is True
    assert snapshot.bridge_vlans[1].current_tagged == ("br", "ether2", "ether3")


def test_down_links_and_disabled_bridge_members_never_emit_clients():
    base = scene()
    down = replace(
        base, ports=tuple(replace(p, up=False) if p.port_idx == 2 else p for p in base.ports)
    )
    assert not entries(scene(snapshot=down))
    disabled = replace(
        base,
        bridge_members=tuple(
            replace(m, enabled=False) if m.interface == "ether2" else m for m in base.bridge_members
        ),
    )
    assert not entries(scene(snapshot=disabled))


def test_unusable_dhcp_hostname_is_not_copied_or_logged():
    snapshot = scene(
        leases=[
            {
                "mac-address": A,
                "address": "192.0.2.20",
                "server": "d20",
                "status": "bound",
                "host-name": "<PRIVATE>\nINJECT",
            }
        ],
        servers=[{"name": "d20", "interface": "v20"}],
    )
    assert entries(snapshot)[0]["ip"] == "192.0.2.20" and "hostname" not in entries(snapshot)[0]
    assert "PRIVATE" not in str(snapshot.public()) and "dhcp:hostname-unusable" in snapshot.warnings
