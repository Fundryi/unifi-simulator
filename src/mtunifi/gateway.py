"""Read-only WAN observations and the explicit UGW3 inform adapter."""

import ipaddress
from dataclasses import replace

from mtunifi.clients import ipv4
from mtunifi.config import Settings
from mtunifi.routeros_api import RouterOSAPI, RouterOSError, Table
from mtunifi.routeros_values import boolean, indexed, link_speed, number, resource_health, text
from mtunifi.snapshot import DeviceSnapshot, WANObservation


def wan_addresses(rows: list[dict] | None, interface: str) -> tuple[tuple[str, ...], str]:
    if rows is None:
        return (), "unavailable"
    addresses = set()
    malformed = False
    for row in rows:
        if (row.get("actual-interface") or row.get("interface")) != interface:
            continue
        if boolean(row.get("disabled")) is True or boolean(row.get("invalid")) is True:
            continue
        if any(key in row and boolean(row[key]) is None for key in ("disabled", "invalid")):
            malformed = True
            continue
        try:
            raw = text(row.get("address"), 64)
            if raw is None or "/" not in raw:
                raise ValueError
            address = ipaddress.IPv4Interface(raw)
            if ipv4(str(address.ip)) is None:
                raise ValueError
        except (ValueError, TypeError):
            malformed = True
            continue
        addresses.add(str(address))
    if malformed:
        return tuple(sorted(addresses))[:8], "invalid"
    return tuple(sorted(addresses))[:8], "ambiguous" if len(addresses) > 1 else "observed"


async def collect_gateway(api: RouterOSAPI, settings: Settings) -> DeviceSnapshot:
    resources = await api.table(Table.RESOURCE)
    if len(resources) != 1:
        raise RouterOSError("invalid-resource-table")
    health, warnings = resource_health(resources[0])

    async def optional(table):
        try:
            return await api.table(table)
        except RouterOSError as exc:
            if exc.code not in ("permission-denied", "endpoint-unavailable", "request-rejected"):
                raise
            warnings.append(table.name.lower() + ":" + exc.code)
            return None

    interfaces = indexed(await api.table(Table.INTERFACES))
    name = settings.routeros_wan_interface
    row = interfaces.get(name)
    if row is None:
        raise RouterOSError("wan-interface-missing", stage=Table.INTERFACES.value)
    disabled, running = boolean(row.get("disabled")), boolean(row.get("running"))
    rx, tx = number(row.get("rx-byte")), number(row.get("tx-byte"))
    if disabled is None or running is None or rx is None or tx is None:
        raise RouterOSError("required-wan-data-missing", stage=Table.INTERFACES.value)
    up = not disabled and running
    speed, duplex = None, None
    kind = text(row.get("type"))
    if kind == "ether":
        ethernet = await optional(Table.ETHERNET)
        matches = [r for r in ethernet or () if r.get("name") == name]
        monitor = None
        if len(matches) == 1:
            try:
                records = await api.monitor(matches[0].get(".id", name))
                if len(records) == 1:
                    monitor = records[0]
            except RouterOSError as exc:
                if exc.code not in (
                    "permission-denied",
                    "endpoint-unavailable",
                    "request-rejected",
                ):
                    raise
        if monitor is None:
            warnings.append("wan:monitor-unavailable")
        else:
            if monitor.get("status") in ("link-ok", "no-link"):
                up = not disabled and monitor["status"] == "link-ok"
            if up:
                speed = link_speed(monitor.get("rate"))
                duplex = boolean(monitor.get("full-duplex"))
                if speed is None or duplex is None:
                    warnings.append("wan:link-details-unavailable")
    identity = await optional(Table.IDENTITY)
    health = replace(
        health, identity=text(identity[0].get("name")) if identity and len(identity) == 1 else None
    )
    addresses, address_status = wan_addresses(await optional(Table.IP_ADDRESSES), name)
    ip, netmask = None, None
    if address_status == "observed" and len(addresses) == 1:
        selected = ipaddress.IPv4Interface(addresses[0])
        ip, netmask = str(selected.ip), str(selected.netmask)
    if address_status != "observed":
        warnings.append("wan:ipv4-" + address_status)
    return DeviceSnapshot(
        health,
        (),
        warnings=tuple(sorted(set(warnings))),
        wan=WANObservation(
            name,
            kind,
            not disabled,
            up,
            rx,
            tx,
            speed,
            duplex,
            ip,
            netmask,
            addresses,
            address_status,
        ),
    )


def gateway_snapshot(snapshot: DeviceSnapshot, device_mac: str) -> dict:
    wan = snapshot.wan
    if wan is None:
        raise RouterOSError("required-wan-data-missing")
    # UGW3 firmware names WAN eth0; 10.6.106 does not derive wan1 from arbitrary
    # RouterOS names. Keep the source name in WANObservation and export this alias.
    entry = {
        "name": "eth0",
        "enable": wan.enabled,
        "up": wan.up,
        "rx_bytes": wan.rx_bytes,
        "tx_bytes": wan.tx_bytes,
    }
    # The interface belongs to the synthetic gateway, never borrow a physical device MAC.
    if device_mac:
        entry["mac"] = device_mac
    if not wan.up:
        entry["speed"] = 0
    elif wan.speed_mbps is not None:
        entry["speed"] = wan.speed_mbps
    if wan.full_duplex is not None and wan.up:
        entry["full_duplex"] = wan.full_duplex
    if wan.ip is not None:
        entry.update(ip=wan.ip, netmask=wan.netmask)
    body = {
        "uptime": snapshot.health.uptime_seconds,
        "uplink": "eth0",
        "config_port_table": [{"ifname": "eth0", "name": "wan", "type": "wan"}],
        "if_table": [entry],
    }
    stats = {}
    health = snapshot.health
    if health.cpu_percent is not None:
        stats["cpu"] = str(health.cpu_percent)
    if health.memory_total_bytes and health.memory_free_bytes is not None:
        stats["mem"] = f"{100 * (1 - health.memory_free_bytes / health.memory_total_bytes):.1f}"
    if stats:
        body["system-stats"] = stats
    return body
