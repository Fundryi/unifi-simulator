"""Explicit switch profiles; a device's class is never inferred from its address."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SwitchProfile:
    name: str
    model: str
    display: str
    version: str
    required_version: str
    max_speeds: tuple[int, ...]

    @property
    def port_count(self) -> int:
        return len(self.max_speeds)


PROFILES = {
    "us8-experimental": SwitchProfile(
        "us8-experimental", "US8", "UniFi Switch 8", "4.3.28.11361", "5.4.0", (1000,) * 8
    ),
    "usw-pro24-experimental": SwitchProfile(
        "usw-pro24-experimental",
        "US24PRO2",
        "UniFi Switch Pro 24",
        "6.6.61.15153",
        "5.13.10",
        (1000,) * 24 + (10000,) * 2,
    ),
}


def profile(name: str) -> SwitchProfile:
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError("Unsupported device profile") from None
