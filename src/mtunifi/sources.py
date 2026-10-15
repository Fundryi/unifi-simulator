from typing import Protocol

from mtunifi.config import Settings
from mtunifi.routeros import RouterOSSource
from mtunifi.scenario import SyntheticSource


class TelemetrySource(Protocol):
    status: str
    last_summary: dict | None

    async def collect(self, now: float, device_mac: str) -> dict: ...


def create_source(settings: Settings) -> SyntheticSource | RouterOSSource:
    if settings.telemetry_source == "routeros":
        return RouterOSSource(settings)
    return SyntheticSource(settings.synthetic_file)
