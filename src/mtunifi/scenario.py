"""Validated lab data and a monotonic synthetic traffic source; never reads RouterOS."""

import hashlib
import ipaddress
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

MAX_COUNTER = 2**63 - 1
MAX_SCENARIO_BYTES = 256 * 1024


class ScenarioError(ValueError):
    """Safe messages only: do not expose file contents or Pydantic input values."""

    def __init__(self, message: str, issues: list | None = None):
        super().__init__(message)
        self.issues = issues or []


class LabModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def synthetic_mac(value: str) -> str:
    try:
        if len(value) != 17 or any(value[i] != ":" for i in (2, 5, 8, 11, 14)):
            raise ValueError
        raw = bytes.fromhex(value.replace(":", ""))
        if len(raw) != 6 or raw[0] & 3 != 2:
            raise ValueError
    except ValueError:
        raise ValueError("Expected a locally administered unicast MAC") from None
    return ":".join(f"{byte:02x}" for byte in raw)


class Port(LabModel):
    port_idx: int = Field(ge=1, le=8)
    enabled: bool = True
    up: bool = False
    speed_mbps: Literal[10, 100, 1000] = 1000
    full_duplex: bool = True
    is_uplink: bool = False
    rx_bytes: int = Field(default=0, ge=0, le=MAX_COUNTER)
    tx_bytes: int = Field(default=0, ge=0, le=MAX_COUNTER)
    rx_bytes_per_second: int = Field(default=0, ge=0, le=125_000_000)
    tx_bytes_per_second: int = Field(default=0, ge=0, le=125_000_000)
    counter_epoch: int = Field(default=0, ge=0, le=MAX_COUNTER)

    @model_validator(mode="after")
    def consistent_link(self):
        if self.up and not self.enabled:
            raise ValueError("A disabled port cannot have an active link")
        if max(self.rx_bytes_per_second, self.tx_bytes_per_second) > self.speed_mbps * 125_000:
            raise ValueError("Synthetic traffic exceeds configured line speed")
        return self


class Client(LabModel):
    mac: str
    port_idx: int = Field(ge=1, le=8)
    vlan: int = Field(default=1, ge=1, le=4094)
    ip: str | None = None
    hostname: str | None = Field(default=None, min_length=1, max_length=63)

    _mac = field_validator("mac")(synthetic_mac)

    @field_validator("ip")
    @classmethod
    def ipv4(cls, value):
        return str(ipaddress.IPv4Address(value)) if value is not None else None


class Neighbor(LabModel):
    local_port_idx: int = Field(ge=1, le=8)
    chassis_id: str
    chassis_descr: str = Field(min_length=1, max_length=128)
    port_id: str = Field(min_length=1, max_length=64)
    port_descr: str | None = Field(default=None, min_length=1, max_length=128)
    local_port_name: str = Field(min_length=1, max_length=32)

    _mac = field_validator("chassis_id")(synthetic_mac)


class Scenario(LabModel):
    schema_version: Literal[1] = 1
    ports: list[Port] = Field(min_length=8, max_length=8)
    clients: list[Client] = Field(default_factory=list, max_length=128)
    neighbors: list[Neighbor] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def unique_entities(self):
        if {p.port_idx for p in self.ports} != set(range(1, 9)):
            raise ValueError("US8 requires exactly ports 1 through 8")
        identities = [(c.mac, c.vlan) for c in self.clients]
        if len(identities) != len(set(identities)):
            raise ValueError("A MAC/VLAN pair must have exactly one physical port")
        neighbor_ports = [n.local_port_idx for n in self.neighbors]
        if len(neighbor_ports) != len(set(neighbor_ports)):
            raise ValueError("Only one direct synthetic LLDP neighbor per port is supported")
        return self


def default_scenario() -> Scenario:
    """Preserve the original M0 fixture unless a scenario is explicitly selected."""
    return Scenario(ports=[Port(port_idx=i, up=i == 1) for i in range(1, 9)])


def read_scenario(path: Path) -> tuple[Scenario, str]:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_SCENARIO_BYTES + 1)
        if len(raw) > MAX_SCENARIO_BYTES:
            raise ScenarioError("Synthetic scenario exceeds size limit")
        return Scenario.model_validate_json(raw), hashlib.sha256(raw).hexdigest()
    except OSError:
        raise ScenarioError("Cannot read synthetic scenario") from None
    except ValidationError as exc:
        known_fields = set().union(
            *(model.model_fields for model in (Scenario, Port, Client, Neighbor))
        )
        issues = [
            {
                "path": ".".join(
                    str(part) if isinstance(part, int) or part in known_fields else "<unknown>"
                    for part in error["loc"]
                )
                or "<root>",
                "error": error["type"],
            }
            for error in exc.errors(include_input=False, include_context=False, include_url=False)[
                :10
            ]
        ]
        raise ScenarioError("Invalid synthetic scenario", issues) from None


@dataclass
class Counter:
    config: Port
    rx: int
    tx: int
    rx_fraction: float = 0
    tx_fraction: float = 0

    def advance(self, seconds: float):
        if not self.config.up or not self.config.enabled:
            return
        for direction in ("rx", "tx"):
            increment = (
                getattr(self, direction + "_fraction")
                + getattr(self.config, direction + "_bytes_per_second") * seconds
            )
            whole = int(increment)
            setattr(self, direction, min(MAX_COUNTER, getattr(self, direction) + whole))
            setattr(self, direction + "_fraction", increment - whole)


class SyntheticSource:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.scenario, self.revision = (
            read_scenario(path) if path else (default_scenario(), "builtin")
        )
        self.status = "active"
        self._rejected = False
        self._counters: dict[int, Counter] = {}
        self._seen: dict[tuple, float] = {}
        self._last: float | None = None
        self.last_summary: dict | None = None

    async def collect(self, now: float, device_mac: str) -> dict:
        return self.snapshot(now, device_mac)

    @staticmethod
    def _identity_check(scenario: Scenario, device_mac: str):
        if any(c.mac == device_mac for c in scenario.clients) or any(
            n.chassis_id == device_mac for n in scenario.neighbors
        ):
            raise ScenarioError("Synthetic scenario reuses the emulator identity")

    def _reload(self, device_mac: str):
        if self.path is None:
            return
        try:
            candidate, revision = read_scenario(self.path)
            self._identity_check(candidate, device_mac)
        except ScenarioError:
            self.status = "using-last-good"
            if not self._rejected:
                logging.getLogger("mtunifi").warning(
                    json.dumps(
                        {
                            "event": "synthetic_config_rejected",
                            "retained_last_good": True,
                        }
                    )
                )
            self._rejected = True
            return
        changed = revision != self.revision or self._rejected
        self.scenario, self.revision, self.status = candidate, revision, "active"
        self._rejected = False
        if changed:
            logging.getLogger("mtunifi").info(
                json.dumps(
                    {
                        "event": "synthetic_config_loaded",
                        "revision": revision[:12],
                    }
                )
            )

    def snapshot(self, now: float, device_mac: str) -> dict:
        # Attribute elapsed traffic to the previously active settings before a hot reload.
        elapsed = max(0, now - self._last) if self._last is not None else 0
        for counter in self._counters.values():
            counter.advance(elapsed)
        self._last = max(now, self._last) if self._last is not None else now
        self._reload(device_mac)
        self._identity_check(self.scenario, device_mac)
        scenario = self.scenario
        active_ports = {p.port_idx for p in scenario.ports if p.enabled and p.up}
        active_clients = [c for c in scenario.clients if c.port_idx in active_ports]
        active_neighbors = [n for n in scenario.neighbors if n.local_port_idx in active_ports]
        clients_by_port: dict[int, list] = {i: [] for i in range(1, 9)}
        seen = {}
        for client in active_clients:
            identity = (client.mac, client.vlan, client.port_idx)
            seen[identity] = self._seen.get(identity, now)
            entry = {
                "mac": client.mac,
                "vlan": client.vlan,
                "age": 0,
                "static": False,
                "uptime": max(0, int(now - seen[identity])),
            }
            entry.update(client.model_dump(include={"ip", "hostname"}, exclude_none=True))
            clients_by_port[client.port_idx].append(entry)
        self._seen = seen
        ports = []
        for port in sorted(scenario.ports, key=lambda p: p.port_idx):
            previous = self._counters.get(port.port_idx)
            if previous is None or (
                previous.config.counter_epoch,
                previous.config.rx_bytes,
                previous.config.tx_bytes,
            ) != (port.counter_epoch, port.rx_bytes, port.tx_bytes):
                previous = Counter(port, port.rx_bytes, port.tx_bytes)
            previous.config = port
            self._counters[port.port_idx] = previous
            up = port.enabled and port.up
            ports.append(
                {
                    "port_idx": port.port_idx,
                    "enable": port.enabled,
                    "up": up,
                    "speed": port.speed_mbps if up else 0,
                    "full_duplex": port.full_duplex if up else False,
                    "autoneg": True,
                    "is_uplink": port.is_uplink,
                    "rx_bytes": previous.rx,
                    "tx_bytes": previous.tx,
                    "mac_table": clients_by_port[port.port_idx],
                }
            )
        neighbors = [n.model_dump(exclude_none=True) | {"is_wired": True} for n in active_neighbors]
        self.last_summary = {
            "source": "synthetic",
            "status": self.status,
            "revision": self.revision[:12],
            "ports": ports,
            "active_clients": len(active_clients),
            "configured_clients": len(scenario.clients),
            "neighbors": neighbors,
        }
        return {"port_table": ports, "lldp_table": neighbors}
