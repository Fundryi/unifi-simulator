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


@dataclass(frozen=True)
class DeviceSnapshot:
    health: DeviceHealth
    ports: tuple[PhysicalPort, ...]
    logical_interfaces: tuple[LogicalInterface, ...] = ()
    bridge_members: tuple[BridgeMember, ...] | None = None
    warnings: tuple[str, ...] = ()

    def public(self) -> dict:
        return asdict(self)


def unifi_snapshot(snapshot: DeviceSnapshot) -> dict:
    by_slot = {port.port_idx: port for port in snapshot.ports}
    ports = []
    for slot in range(1, 9):
        port = by_slot.get(slot)
        if port is None:
            ports.append({"port_idx": slot, "enable": False, "up": False, "speed": 0})
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
    # No FDB, ARP, DHCP, neighbors or synthetic clients are inferred in milestone 2.
    return body
