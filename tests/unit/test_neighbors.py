from dataclasses import replace

import pytest

from mtunifi.neighbors import collect_neighbors
from mtunifi.snapshot import (
    BridgeMember,
    DeviceHealth,
    DeviceSnapshot,
    LogicalInterface,
    PhysicalPort,
    unifi_snapshot,
)

A = "00:11:22:33:44:55"
B = "00:11:22:33:44:66"
LOCAL = "dc:2c:6e:00:00:01"


def row(**kwargs):
    return {
        "interface": "ether2",
        "mac-address": A,
        "identity": "upstream",
        "interface-name": "Gi1/0/8",
        "discovered-by": "lldp",
        "age": "1m2s",
        **kwargs,
    }


def scene(rows, **kwargs):
    snapshot = DeviceSnapshot(
        DeviceHealth("7.24.4", 100),
        tuple(
            PhysicalPort(i, f"ether{i}", None, LOCAL, i != 3, i != 3, 1000, True, True, 0, 0)
            for i in range(1, 4)
        ),
        (
            LogicalInterface("br", "bridge"),
            LogicalInterface("bond", "bonding", members=("ether1",)),
        ),
        (BridgeMember("ether2", "br", 1, True, None, True),),
    )
    return collect_neighbors(snapshot, rows, **kwargs)


def test_reported_lldp_identity_port_and_age_no_fdb_topology():
    snapshot = scene([row(), row(age="3s")])
    (n,) = snapshot.neighbors.observations
    assert n.mac == A and n.age_seconds == 3 and n.protocols == ("lldp",)
    assert n.evidence == "routeros-neighbor-cache" and n.certainty == "reported"
    (wire,) = unifi_snapshot(snapshot)["lldp_table"]
    assert wire == {
        "chassis_id": A,
        "chassis_descr": "upstream",
        "local_port_idx": 2,
        "local_port_name": "ether2",
        "port_id": "Gi1/0/8",
        "is_wired": True,
    }
    aliases = scene([row(), row(interface="ether2,br")])
    assert len(aliases.neighbors.observations) == 2
    assert len(unifi_snapshot(aliases)["lldp_table"]) == 1


@pytest.mark.parametrize(
    "fields,reason",
    [
        ({"discovered-by": "mndp"}, "protocol-not-lldp"),
        ({"discovered-by": "cdp"}, "protocol-not-lldp"),
        ({"discovered-by": ""}, "protocol-not-lldp"),
        ({"discovered-by": "mndp,lldp"}, "merged-protocols"),
        ({"interface-name": ""}, "remote-port-unknown"),
        ({"interface-name": "a\nb"}, "remote-port-unknown"),
        ({"mac-address": "01:11:22:33:44:55"}, "remote-mac-unknown"),
        ({"interface": "br"}, "unresolved-local-interface"),
        ({"interface": "bond"}, "unresolved-local-interface"),
        ({"interface": "unmapped"}, "unresolved-local-interface"),
        ({"interface": "ether1,ether2"}, "unresolved-local-interface"),
        ({"interface": "ether2,bond"}, "unresolved-local-interface"),
        ({"interface": "ether1"}, "bond-member"),
        ({"interface": "ether3"}, "local-port-down"),
        ({"interface": "ether3,br"}, "unverified-bridge-alias"),
    ],
)
def test_incomplete_or_unattributable_evidence_stays_diagnostic(fields, reason):
    snapshot = scene([row(**fields)])
    assert unifi_snapshot(snapshot)["lldp_table"] == []
    assert snapshot.neighbors.observations[0].export_reason == reason


def test_bridge_alias_needs_observed_membership_and_does_not_create_a_port():
    snapshot = scene([row(interface="br,ether2")])
    assert unifi_snapshot(snapshot)["lldp_table"][0]["local_port_idx"] == 2
    assert snapshot.neighbors.observations[0].interfaces == ("br", "ether2")
    snapshot = replace(snapshot, ports=tuple(replace(p, up=False) for p in snapshot.ports))
    # Fresh normalization uses current link status, not the previously cached eligibility.
    snapshot = collect_neighbors(snapshot, [row(interface="br,ether2")])
    assert unifi_snapshot(snapshot)["lldp_table"] == []
    snapshot = replace(snapshot, logical_interfaces=(LogicalInterface("bond", "bonding"),))
    snapshot = collect_neighbors(snapshot, [row()])
    assert snapshot.neighbors.observations[0].export_reason == "bond-membership-unknown"
    assert unifi_snapshot(snapshot)["lldp_table"] == []


def test_multiple_or_conflicting_advertisements_do_not_choose_a_direct_link():
    snapshot = scene([row(), row(**{"mac-address": B})])
    assert len(snapshot.neighbors.observations) == 2
    assert all(n.export_reason == "ambiguous-local-port" for n in snapshot.neighbors.observations)
    assert unifi_snapshot(snapshot)["lldp_table"] == []
    snapshot = scene([row(), row(identity="conflicting")])
    assert unifi_snapshot(snapshot)["lldp_table"] == []


def test_unknown_empty_invalid_and_local_exclusion():
    assert "lldp_table" not in unifi_snapshot(scene(None))
    assert unifi_snapshot(scene([]))["lldp_table"] == []
    snapshot = scene([row(), row(interface="")])
    assert "lldp_table" not in unifi_snapshot(snapshot)
    assert snapshot.neighbors.invalid_rows == 1 and snapshot.warnings
    assert len(snapshot.neighbors.observations) == 2  # Partial evidence stays diagnostic only.
    snapshot = scene(
        [row(**{"mac-address": LOCAL}), row(**{"mac-address": B})], local_macs=(B.upper(),)
    )
    assert snapshot.neighbors.excluded_local == 2
    assert unifi_snapshot(snapshot)["lldp_table"] == []


def test_missing_description_and_age_not_invented():
    snapshot = scene([row(identity="", age="bad")])
    assert snapshot.neighbors.observations[0].age_seconds is None
    assert "chassis_descr" not in unifi_snapshot(snapshot)["lldp_table"][0]


def test_uplink_claimed_only_from_the_mapped_uplink_port():
    snapshot = scene([row()])
    assert "uplink" not in unifi_snapshot(snapshot)
    ports = tuple(replace(p, is_uplink=p.port_idx == 2) for p in snapshot.ports)
    body = unifi_snapshot(replace(snapshot, ports=ports))
    assert body["uplink"] == {
        "type": "wire",
        "port_idx": 2,
        "uplink_mac": A,
        "up": True,
        "full_duplex": True,
        "speed": 1000,
    }
    numbered = scene([row(**{"interface-name": "7"})])
    body = unifi_snapshot(replace(numbered, ports=ports))
    assert body["uplink"]["uplink_remote_port"] == 7
