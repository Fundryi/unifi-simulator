"""Read-only RouterOS collector with explicit physical-port mapping and reconnects."""

import asyncio
import re
import time
from dataclasses import replace
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from mtunifi.clients import ClientTracker, correlate_clients
from mtunifi.config import Settings
from mtunifi.neighbors import collect_neighbors
from mtunifi.routeros_api import RouterOSAPI, RouterOSError, Table, api_error, api_target
from mtunifi.routeros_values import boolean, number, text, uptime_seconds
from mtunifi.scenario import LabModel
from mtunifi.snapshot import (
    BridgeMember,
    DeviceHealth,
    DeviceSnapshot,
    LogicalInterface,
    PhysicalPort,
    unifi_snapshot,
)


class PortMapping(LabModel):
    port_idx: int = Field(ge=1, le=8)
    interface: str = Field(min_length=1, max_length=128)
    is_uplink: bool = False

    @field_validator("interface")
    @classmethod
    def printable_name(cls, value):
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("Control characters are not allowed")
        return value


class RouterOSMap(LabModel):
    schema_version: Literal[1] = 1
    ports: list[PortMapping] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def unique_ports(self):
        if len({p.port_idx for p in self.ports}) != len(self.ports) or len(
            {p.interface for p in self.ports}
        ) != len(self.ports):
            raise ValueError("Each slot and interface must have one mapping")
        return self


def load_mapping(path: Path) -> RouterOSMap:
    try:
        with path.open("rb") as handle:
            raw = handle.read(16 * 1024 + 1)
        if len(raw) > 16 * 1024:
            raise ValueError
        return RouterOSMap.model_validate_json(raw)
    except (OSError, ValueError, ValidationError):
        raise RouterOSError("port-map-invalid") from None


def link_speed(value: str | None) -> int | None:
    if not value:
        return None
    match = re.fullmatch(r"([0-9]{1,3}(?:\.[0-9]{1,3})?)([GM])bps", value)
    if match:
        rate = float(match[1]) * (1000 if match[2] == "G" else 1)
        if rate.is_integer() and 0 < rate <= 800_000:
            return int(rate)
    return None


def check_version(version: str | None) -> str:
    if version:
        match = re.fullmatch(r"7\.(\d{1,3})(?:\.\d{1,3})?(?: \([^\r\n]{1,32}\))?", version)
        if match and int(match[1]) >= 24:
            return version
    raise RouterOSError("requires-routeros-7.24-or-newer")


def indexed(rows: list[dict]) -> dict[str, dict]:
    result = {}
    for row in rows:
        name = text(row.get("name"))
        if name is None or name in result:
            raise RouterOSError("invalid-interface-table")
        result[name] = row
    return result


async def collect_device(
    api: RouterOSAPI, mapping: RouterOSMap, device_mac: str = ""
) -> DeviceSnapshot:
    resources = await api.table(Table.RESOURCE)
    if len(resources) != 1:
        raise RouterOSError("invalid-resource-table")
    resource = resources[0]
    version = check_version(resource.get("version"))
    uptime = uptime_seconds(resource.get("uptime"))
    interfaces = indexed(await api.table(Table.INTERFACES))
    ethernet = indexed(await api.table(Table.ETHERNET))
    warnings = []

    async def optional(table):
        try:
            return await api.table(table)
        except RouterOSError as exc:
            # Permission/unsupported table are recoverable; a broken stream is not reusable.
            if exc.code not in ("permission-denied", "endpoint-unavailable", "request-rejected"):
                raise
            warnings.append(table.name.lower() + ":" + exc.code)
            return None

    identity = await optional(Table.IDENTITY)
    total, free = number(resource.get("total-memory")), number(resource.get("free-memory"))
    if total is None or total == 0 or free is None or free > total:
        total, free = None, None
        warnings.append("memory:unavailable")
    cpu = number(resource.get("cpu-load"), 100)
    if cpu is None:
        warnings.append("cpu:unavailable")
    health = DeviceHealth(
        version=version,
        uptime_seconds=uptime,
        identity=text(identity[0].get("name")) if identity and len(identity) == 1 else None,
        cpu_percent=cpu,
        memory_total_bytes=total,
        memory_free_bytes=free,
        board=text(resource.get("board-name")),
    )
    ports = []
    for item in sorted(mapping.ports, key=lambda p: p.port_idx):
        eth, stats = ethernet.get(item.interface), interfaces.get(item.interface)
        if eth is None or stats is None or stats.get("type") != "ether":
            raise RouterOSError("mapped-physical-interface-missing")
        disabled, running = boolean(stats.get("disabled")), boolean(stats.get("running"))
        rx, tx = number(stats.get("rx-byte")), number(stats.get("tx-byte"))
        if disabled is None or running is None or rx is None or tx is None:
            raise RouterOSError("required-interface-data-missing")
        monitor = None
        try:
            records = await api.monitor(eth.get(".id", item.interface))
            if len(records) == 1:
                monitor = records[0]
        except RouterOSError as exc:
            if exc.code not in ("permission-denied", "endpoint-unavailable", "request-rejected"):
                raise
        if monitor is None:
            warnings.append(f"port-{item.port_idx}:monitor-unavailable")
        monitor = monitor or {}
        up = not disabled and running
        if monitor.get("status") in ("no-link", "link-ok"):
            up = not disabled and monitor["status"] == "link-ok"
        speed = link_speed(monitor.get("rate")) if up else None
        duplex = boolean(monitor.get("full-duplex")) if up else None
        if up and (speed is None or duplex is None):
            warnings.append(f"port-{item.port_idx}:link-details-unavailable")
        if speed is not None and speed > 1000:
            warnings.append(f"port-{item.port_idx}:exceeds-us8-profile")
        ports.append(
            PhysicalPort(
                port_idx=item.port_idx,
                name=item.interface,
                default_name=text(eth.get("default-name")),
                mac=text(eth.get("mac-address"), 17),
                enabled=not disabled,
                up=up,
                speed_mbps=speed,
                full_duplex=duplex,
                autoneg={"done": True, "failed": True, "disabled": False}.get(
                    monitor.get("auto-negotiation")
                ),
                rx_bytes=rx,
                tx_bytes=tx,
                is_uplink=item.is_uplink,
            )
        )
    # Logical interfaces are inventory/context only; never count them as physical ports.
    vlans = (
        await optional(Table.VLANS)
        if any(i.get("type") == "vlan" for i in interfaces.values())
        else []
    )
    bonds = (
        await optional(Table.BONDS)
        if any(i.get("type") == "bonding" for i in interfaces.values())
        else []
    )
    vlan_by_name = {r.get("name"): r for r in vlans or []}
    bond_by_name = {r.get("name"): r for r in bonds or []}
    logical = []
    for name, row in interfaces.items():
        kind = row.get("type")
        if kind not in ("bridge", "vlan", "bonding"):
            continue
        vlan, bond = vlan_by_name.get(name, {}), bond_by_name.get(name, {})
        slaves = text(bond.get("slaves"), 1024)
        logical.append(
            LogicalInterface(
                name=name,
                kind=kind,
                parent=text(vlan.get("interface")),
                vlan_id=number(vlan.get("vlan-id"), 4094),
                members=tuple(slaves.split(",")) if slaves else None,
            )
        )
    members = None
    if any(i.kind == "bridge" for i in logical):
        records = await optional(Table.BRIDGE_PORTS)
        if records is not None:
            members = []
            for row in records:
                interface, bridge = text(row.get("interface")), text(row.get("bridge"))
                if interface and bridge:
                    members.append(
                        BridgeMember(
                            interface=interface,
                            bridge=bridge,
                            pvid=number(row.get("pvid"), 4094),
                            hardware_offload_requested=boolean(row.get("hw")),
                            hardware_offload_active=boolean(row.get("hw-offload")),
                            enabled=(not boolean(row["disabled"]))
                            if boolean(row.get("disabled")) is not None
                            else None,
                        )
                    )
                else:
                    warnings.append("bridge_ports:invalid-row")
    snapshot = DeviceSnapshot(
        health,
        tuple(ports),
        tuple(logical),
        tuple(members) if members is not None else None,
        tuple(sorted(set(warnings))),
    )
    bridge_rows = await optional(Table.BRIDGES) if any(i.kind == "bridge" for i in logical) else []
    vlan_rows = await optional(Table.BRIDGE_VLANS) if bridge_rows != [] else []
    hosts = await optional(Table.BRIDGE_HOSTS)
    arp = await optional(Table.ARP)
    leases = await optional(Table.DHCP_LEASES)
    servers = await optional(Table.DHCP_SERVERS) if leases else None
    neighbors = await optional(Table.NEIGHBORS)
    snapshot = replace(snapshot, warnings=tuple(sorted(set(warnings))))
    snapshot = correlate_clients(
        snapshot,
        interfaces=interfaces,
        bridge_rows=bridge_rows,
        vlan_rows=vlan_rows,
        hosts=hosts,
        arp=arp,
        leases=leases,
        servers=servers,
        device_mac=device_mac,
        extra_local_macs=tuple(row.get("mac-address") for row in ethernet.values()),
    )
    return collect_neighbors(
        snapshot,
        neighbors,
        local_macs=(
            device_mac,
            *(r.get("mac-address") for r in interfaces.values()),
            *(r.get("mac-address") for r in ethernet.values()),
            *(r.get(k) for r in bridge_rows or () for k in ("mac-address", "admin-mac")),
        ),
    )


class RouterOSSource:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.mapping = load_mapping(settings.routeros_map_file)
        self.status = "unavailable"
        self.last_summary = None
        self._snapshot: DeviceSnapshot | None = None
        self._next_poll = 0.0
        self._observed_at: float | None = None
        self._failures = 0
        self._error: RouterOSError | None = None
        self._clients = ClientTracker()
        self._observed_mono = 0.0

    async def collect(self, now: float, device_mac: str) -> dict:
        if now >= self._next_poll:
            api = RouterOSAPI(self.settings)
            try:
                async with asyncio.timeout(self.settings.routeros_timeout):
                    # One bounded session per poll: failures reconnect without sharing credentials
                    # or a socket with another device. Secret rotations apply on the next poll.
                    async with api:
                        snapshot = await collect_device(api, self.mapping, device_mac)
                previous = self._snapshot
                reset = previous is not None and (
                    snapshot.health.uptime_seconds < previous.health.uptime_seconds
                    or any(
                        p.rx_bytes < old.rx_bytes or p.tx_bytes < old.tx_bytes
                        for p, old in zip(snapshot.ports, previous.ports, strict=True)
                    )
                )
                if reset:
                    snapshot = replace(snapshot, warnings=(*snapshot.warnings, "counters:reset"))
                    self._clients.clear()
                snapshot = self._clients.observe(snapshot, now)
                self._snapshot = snapshot
                self._observed_at = time.time()
                self._observed_mono = now
                self.status = "degraded" if snapshot.warnings else "active"
                self._error, self._failures = None, 0
                self._next_poll = now + self.settings.routeros_poll_interval
            except (RouterOSError, OSError, TimeoutError, asyncio.IncompleteReadError) as exc:
                self._error = api_error(exc, api.stage)
                self._failures = min(5, self._failures + 1)
                self._next_poll = now + min(
                    60, self.settings.routeros_poll_interval * 2 ** (self._failures - 1)
                )
                self.status = "unavailable"
                self._clients.clear()
        snapshot = self._snapshot
        body = (
            unifi_snapshot(snapshot, age_seconds=max(0, int(now - self._observed_mono)))
            if (snapshot is not None and self._error is None)
            else None
        )
        clients = snapshot.wired.clients if snapshot and snapshot.wired else None
        self.last_summary = {
            "source": "routeros",
            "target": api_target(self.settings),
            "status": self.status,
            "observed_at": self._observed_at,
            "age_seconds": max(0, time.time() - self._observed_at)
            if self._observed_at is not None
            else None,
            "active_clients": sum(len(p.get("mac_table", [])) for p in body["port_table"])
            if body is not None and clients is not None
            else None,
            "clients_status": "unavailable" if clients is None or body is None else "observed",
            "unresolved_clients": sum(c.attachment != "physical" for c in clients)
            if clients is not None and body is not None
            else None,
            "neighbors": len(body["lldp_table"])
            if body is not None and "lldp_table" in body
            else None,
            "neighbors_status": "observed"
            if body is not None and "lldp_table" in body
            else "unavailable",
            "neighbor_observations": len(snapshot.neighbors.observations)
            if body is not None
            and snapshot.neighbors
            and snapshot.neighbors.observations is not None
            else None,
            "mapped_physical_ports": len(self.mapping.ports),
            "source_error": self._error.code if self._error else None,
            "source_error_details": self._error.public() if self._error else None,
            "observations": snapshot.public() if snapshot else None,
        }
        if self._error is not None:
            # Retain the previous snapshot only for diagnosis, never send stale observations
            # as fresh telemetry or silently fall back to synthetic ports/clients.
            raise self._error
        return body
