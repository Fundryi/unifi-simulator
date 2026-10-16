"""Vendor-neutral observations and the current eight-slot UniFi mapping."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class DeviceHealth:
    version: str
    uptime_seconds: int
    identity: str | None = None
    cpu_percent: int | None = None
    memory_total_bytes: int | None = None
    memory_free_bytes: int | None = None
    board: str | None = None


@dataclass(frozen=True)
class PhysicalPort:
    port_idx: int
    name: str
    default_name: str | None
    mac: str | None
    enabled: bool
    up: bool
    speed_mbps: int | None
    full_duplex: bool | None
    autoneg: bool | None
    rx_bytes: int
    tx_bytes: int
    is_uplink: bool = False


@dataclass(frozen=True)
class LogicalInterface:
    name: str
    kind: str
    parent: str | None = None
    vlan_id: int | None = None
    members: tuple[str, ...] | None = None


@dataclass(frozen=True)
class BridgeMember:
    interface: str
    bridge: str
    pvid: int | None
    hardware_offload_requested: bool | None
    hardware_offload_active: bool | None
    enabled: bool | None = None


@dataclass(frozen=True)
class BridgeConfig:
    name: str
    vlan_filtering: bool | None
    pvid: int | None
    ageing_time_seconds: int | None


@dataclass(frozen=True)
class BridgeVLAN:
    bridge: str
    ranges: tuple[tuple[int, int], ...]
    tagged: tuple[str, ...] | None
    untagged: tuple[str, ...] | None
    current_tagged: tuple[str, ...] | None
    current_untagged: tuple[str, ...] | None
    dynamic: bool | None


@dataclass(frozen=True)
class WiredClient:
    mac: str
    bridge: str
    interface: str | None
    vlan: int | None
    port_idx: int | None
    attachment: str
    dynamic: bool | None
    external: bool | None
    ip: str | None = None
    hostname: str | None = None
    ip_sources: tuple[str, ...] = ()
    ip_candidates: tuple[str, ...] = ()
    observed_uptime_seconds: int = 0
    conflicting_interfaces: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClientInventory:
    clients: tuple[WiredClient, ...] | None
    sources: dict[str, str]
    fdb_rows: int = 0
    excluded_local: int = 0
    skipped_rows: int = 0


@dataclass(frozen=True)
class NeighborObservation:
    interfaces: tuple[str, ...]
    protocols: tuple[str, ...]
    mac: str | None
    identity: str | None
    remote_port: str | None
    system_description: str | None
    age_seconds: int | None
    port_idx: int | None
    export_reason: str
    evidence: str = "routeros-neighbor-cache"
    certainty: str = "reported"


@dataclass(frozen=True)
class NeighborInventory:
    observations: tuple[NeighborObservation, ...] | None
    source: str = "/ip/neighbor/print"
    excluded_local: int = 0
    invalid_rows: int = 0


@dataclass(frozen=True)
class DeviceSnapshot:
    health: DeviceHealth
    ports: tuple[PhysicalPort, ...]
    logical_interfaces: tuple[LogicalInterface, ...] = ()
    bridge_members: tuple[BridgeMember, ...] | None = None
    warnings: tuple[str, ...] = ()
    bridges: tuple[BridgeConfig, ...] | None = None
    bridge_vlans: tuple[BridgeVLAN, ...] | None = None
    wired: ClientInventory | None = None
    neighbors: NeighborInventory | None = None

    def public(self) -> dict:
        return asdict(self)


def unifi_snapshot(snapshot: DeviceSnapshot, *, age_seconds: int = 0) -> dict:
    by_slot = {port.port_idx: port for port in snapshot.ports}
    clients_by_port: dict[int, list] = {i: [] for i in range(1, 9)}
    clients = snapshot.wired.clients if snapshot.wired is not None else None
    for client in clients or ():
        port = by_slot.get(client.port_idx)
        if client.attachment != "physical" or port is None or not port.enabled or not port.up:
            continue
        entry = {
            "mac": client.mac,
            "age": max(0, age_seconds),
            "uptime": client.observed_uptime_seconds + max(0, age_seconds),
            "static": False,  # Only dynamic or externally learned FDB clients are emitted.
        }
        if client.vlan is not None:
            entry["vlan"] = client.vlan
        if client.ip is not None:
            entry["ip"] = client.ip
        if client.hostname is not None:
            entry["hostname"] = client.hostname
        clients_by_port[client.port_idx].append(entry)
    ports = []
    for slot in range(1, 9):
        port = by_slot.get(slot)
        if port is None:
            entry = {"port_idx": slot, "enable": False, "up": False, "speed": 0}
            if clients is not None:
                entry["mac_table"] = []
            ports.append(entry)
            continue
        entry = {
            "port_idx": slot,
            "enable": port.enabled,
            "up": port.up,
            "is_uplink": port.is_uplink,
            "rx_bytes": port.rx_bytes,
            "tx_bytes": port.tx_bytes,
        }
        # Unknown live speed/duplex is omitted, never replaced with a guessed gigabit link.
        if not port.up:
            entry.update(speed=0, full_duplex=False)
        else:
            if port.speed_mbps is not None:
                entry["speed"] = port.speed_mbps
            if port.full_duplex is not None:
                entry["full_duplex"] = port.full_duplex
        if port.autoneg is not None:
            entry["autoneg"] = port.autoneg
        if clients is not None:
            entry["mac_table"] = clients_by_port[slot]
        ports.append(entry)
    health = snapshot.health
    stats = {}
    if health.cpu_percent is not None:
        stats["cpu"] = str(health.cpu_percent)
    if health.memory_total_bytes and health.memory_free_bytes is not None:
        stats["mem"] = f"{100 * (1 - health.memory_free_bytes / health.memory_total_bytes):.1f}"
    body = {"port_table": ports, "uptime": health.uptime_seconds}
    if stats:
        body["system-stats"] = stats
    neighbors = (
        snapshot.neighbors.observations
        if snapshot.neighbors is not None and snapshot.neighbors.invalid_rows == 0
        else None
    )
    if neighbors is not None:
        body["lldp_table"] = []
        emitted = set()
        for neighbor in neighbors:
            if neighbor.export_reason != "eligible":
                continue
            port = by_slot.get(neighbor.port_idx)
            identity = (neighbor.port_idx, neighbor.mac, neighbor.remote_port)
            if port is None or not port.enabled or not port.up or identity in emitted:
                continue
            emitted.add(identity)
            entry = {
                "chassis_id": neighbor.mac,
                "local_port_idx": port.port_idx,
                "local_port_name": port.name,
                "port_id": neighbor.remote_port,
                "is_wired": True,
            }
            if neighbor.system_description or neighbor.identity:
                entry["chassis_descr"] = neighbor.system_description or neighbor.identity
            body["lldp_table"].append(entry)
    return body
