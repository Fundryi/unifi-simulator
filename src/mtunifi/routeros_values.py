"""Bounded parsers for RouterOS API string values."""

import re

from mtunifi.routeros_api import RouterOSError


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


def resource_health(resource: dict):
    from mtunifi.snapshot import DeviceHealth

    warnings = []
    total, free = number(resource.get("total-memory")), number(resource.get("free-memory"))
    if total is None or total == 0 or free is None or free > total:
        total, free = None, None
        warnings.append("memory:unavailable")
    cpu = number(resource.get("cpu-load"), 100)
    if cpu is None:
        warnings.append("cpu:unavailable")
    return DeviceHealth(
        version=check_version(resource.get("version")),
        uptime_seconds=uptime_seconds(resource.get("uptime")),
        cpu_percent=cpu,
        memory_total_bytes=total,
        memory_free_bytes=free,
        board=text(resource.get("board-name")),
    ), warnings
