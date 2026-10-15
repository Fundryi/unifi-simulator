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
