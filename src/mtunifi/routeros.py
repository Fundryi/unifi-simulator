"""Read-only RouterOS collector with explicit physical-port mapping and reconnects."""

import asyncio
import re
import ssl
import time
from dataclasses import replace
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from mtunifi.config import Settings
from mtunifi.routeros_api import RouterOSAPI, RouterOSError, Table
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


def text(value: str | None, limit: int = 128) -> str | None:
    if value and len(value) <= limit and all(32 <= ord(c) != 127 for c in value):
        return value
    return None


def boolean(value: str | None) -> bool | None:
    return {"true": True, "yes": True, "false": False, "no": False}.get(value)


def number(value: str | None, maximum: int = 2**64 - 1) -> int | None:
    if value is not None and re.fullmatch(r"[0-9]{1,20}", value):
        parsed = int(value)
        if parsed <= maximum:
            return parsed
    return None


def uptime_seconds(value: str | None) -> int:
    if value:
        match = re.fullmatch(
            r"(?:(\d{1,8})w)?(?:(\d{1,8})d)?(?:(\d{1,8})h)?"
            r"(?:(\d{1,8})m)?(?:(\d{1,8})s)?",
            value,
        )
        if match and any(part is not None for part in match.groups()):
            return sum(
                int(n or 0) * unit
                for n, unit in zip(match.groups(), (604800, 86400, 3600, 60, 1), strict=True)
            )
        match = re.fullmatch(r"(?:(\d{1,8})d)?(\d{1,2}):(\d{2}):(\d{2})", value)
        if match:
            days, hours, minutes, seconds = (int(n or 0) for n in match.groups())
            if hours < 24 and minutes < 60 and seconds < 60:
                return days * 86400 + hours * 3600 + minutes * 60 + seconds
    raise RouterOSError("invalid-uptime")


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


async def collect_device(api: RouterOSAPI, mapping: RouterOSMap) -> DeviceSnapshot:
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
                        )
                    )
                else:
                    warnings.append("bridge_ports:invalid-row")
    return DeviceSnapshot(
        health,
        tuple(ports),
        tuple(logical),
        tuple(members) if members is not None else None,
        tuple(sorted(set(warnings))),
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

    async def collect(self, now: float, device_mac: str) -> dict:
        if now >= self._next_poll:
            try:
                async with asyncio.timeout(self.settings.routeros_timeout):
                    # One bounded session per poll: failures reconnect without sharing credentials
                    # or a socket with another device. Secret rotations apply on the next poll.
                    async with RouterOSAPI(self.settings) as api:
                        snapshot = await collect_device(api, self.mapping)
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
                self._snapshot = snapshot
                self._observed_at = time.time()
                self.status = "degraded" if snapshot.warnings else "active"
                self._error, self._failures = None, 0
                self._next_poll = now + self.settings.routeros_poll_interval
            except (RouterOSError, OSError, TimeoutError, asyncio.IncompleteReadError) as exc:
                code = (
                    exc.code
                    if isinstance(exc, RouterOSError)
                    else "tls-verification-failed"
                    if isinstance(exc, ssl.SSLCertVerificationError)
                    else "timeout"
                    if isinstance(exc, TimeoutError)
                    else "connection-failed"
                )
                self._error = RouterOSError(code)
                self._failures = min(5, self._failures + 1)
                self._next_poll = now + min(
                    60, self.settings.routeros_poll_interval * 2 ** (self._failures - 1)
                )
                self.status = "unavailable"
        snapshot = self._snapshot
        self.last_summary = {
            "source": "routeros",
            "status": self.status,
            "observed_at": self._observed_at,
            "age_seconds": max(0, time.time() - self._observed_at)
            if self._observed_at is not None
            else None,
            "active_clients": None,
            "neighbors": None,
            "mapped_physical_ports": len(self.mapping.ports),
            "source_error": self._error.code if self._error else None,
            "observations": snapshot.public() if snapshot else None,
        }
        if self._error is not None:
            # Retain the previous snapshot only for diagnosis, never send stale observations
            # as fresh telemetry or silently fall back to synthetic ports/clients.
            raise self._error
        return unifi_snapshot(snapshot)
