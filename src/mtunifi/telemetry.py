"""Minimal synthetic profile. No field is a claim about MikroTik observations."""

import time
from dataclasses import dataclass

from mtunifi.config import Settings
from mtunifi.storage import State


@dataclass(frozen=True)
class PhysicalPort:
    index: int
    up: bool = False
    speed_mbps: int = 0
    rx_bytes: int = 0
    tx_bytes: int = 0


def inform(settings: Settings, state: State, uptime: int, *, inform_url: str | None = None) -> dict:
    # Names and types are supported by TNBU BaseInformBody/Port_Table; US8 acceptance untested.
    ports = [
        PhysicalPort(index=i, up=i == 1, speed_mbps=1000 if i == 1 else 0) for i in range(1, 9)
    ]
    return {
        "model": "US8",
        "model_display": "UniFi Switch 8",
        "version": "4.3.28.11361",
        "required_version": "5.4.0",
        "mac": state.mac,
        "serial": state.mac.replace(":", "").upper(),
        "hostname": settings.hostname,
        "ip": settings.device_ip,
        "inform_url": inform_url if inform_url is not None else settings.inform_url,
        "default": state.authkey is None,
        "state": 0 if state.authkey is None else 2,
        "cfgversion": state.cfgversion,
        "uptime": uptime,
        "time": int(time.time()),
        "discovery_response": False,
        "isolated": False,
        "locating": False,
        "port_table": [
            {
                "port_idx": p.index,
                "up": p.up,
                "enable": True,
                "speed": p.speed_mbps,
                "full_duplex": p.up,
                "autoneg": True,
                "rx_bytes": p.rx_bytes,
                "tx_bytes": p.tx_bytes,
            }
            for p in ports
        ],
    }
