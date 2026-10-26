"""Explicit device profiles; a device's class is never inferred from its address."""

from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceProfile:
    name: str
    model: str
    display: str
    version: str
    required_version: str
    max_speeds: tuple[int, ...]
    kind: str = "switch"

    @property
    def port_count(self) -> int:
        return len(self.max_speeds)


PROFILES = {
    "us8-experimental": DeviceProfile(
        "us8-experimental", "US8", "UniFi Switch 8", "4.3.28.11361", "5.4.0", (1000,) * 8
    ),
    "usw-pro24-experimental": DeviceProfile(
        "usw-pro24-experimental",
        "US24PRO2",
        "UniFi Switch Pro 24",
        "6.6.61.15153",
        "5.13.10",
        (1000,) * 24 + (10000,) * 2,
    ),
    # USW-Aggregation: 8x SFP+ 10G, no copper. Fits an 8-port SFP+ RouterOS switch.
    # Model string per https://static.ui.com/fingerprint/ui/public.json (sysid ed2d).
    "usw-aggregation-experimental": DeviceProfile(
        "usw-aggregation-experimental",
        "USL8A",
        "UniFi Switch Aggregation",
        "7.5.15.17146",
        "5.13.10",
        (10000,) * 8,
    ),
    "ugw3-experimental": DeviceProfile(
        "ugw3-experimental",
        "UGW3",
        "UniFi Security Gateway",
        "4.4.57.5578372",
        "5.4.0",
        (),
        "gateway",
    ),
}


def profile(name: str) -> DeviceProfile:
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError("Unsupported device profile") from None
