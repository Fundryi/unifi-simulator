# RB760iGS as UGW3: read-only WAN telemetry

The opt-in `ugw3-experimental` profile presents a **UniFi Security Gateway / UGW3**
and reads WAN observations from RouterOS 7.24+. The default WAN interface is
**`ether1`**, including the requested RB760iGS at **192.168.0.249:8728**. It reuses
the existing read-only API account and password secret. Nothing is written to RouterOS.

This is a different controller device class. The gateway override uses a **new
state volume, synthetic MAC and adoption key**; the old US8 volume stays intact.
The CRS326 remains a separate Switch Pro 24 with its existing identity and clients.

## Switch the existing M5 lab

Run `git pull` in the existing checkout. Then, from `deploy/lab`, use these commands
(single lines also work in Windows PowerShell):

```sh
docker compose -f compose.simulator.yml -f compose.routeros.yml -f compose.multidevice.yml -f compose.gateway.yml up -d --build --no-deps --force-recreate emulator
docker compose -f compose.simulator.yml -f compose.routeros.yml -f compose.multidevice.yml -f compose.gateway.yml logs -f emulator
```

Only `emulator` is recreated. Controller, MongoDB and `emulator-crs326` keep running.
The last override, [compose.gateway.yml](../deploy/lab/compose.gateway.yml), replaces
the RB's `/state` mount with `rb760-gateway-state`. The previous `simulator-state`
volume is retained. No password, port-map edit or adoption reset is needed.

In UniFi Devices, adopt the new **`mtunifi-rb760-ugw3`** gateway. Confirm Connected
and WAN traffic across several informs. UniFi allows one gateway per site; an
already-adopted gateway must be handled before adopting this one. The previous US8
record can remain offline until verification; removing it is an operator decision, never an automatic action.

```sh
docker compose -f compose.simulator.yml -f compose.routeros.yml -f compose.multidevice.yml -f compose.gateway.yml exec emulator mtunifi diagnose
docker compose -f compose.simulator.yml -f compose.routeros.yml -f compose.multidevice.yml -f compose.gateway.yml exec emulator mtunifi probe-routeros
docker compose -f compose.simulator.yml -f compose.routeros.yml -f compose.multidevice.yml -f compose.gateway.yml exec emulator mtunifi health
```

Diagnostics should report `selected_profile=ugw3-experimental`, `model=UGW3`,
`wan_interface=ether1` and actual WAN counters in `wan`. Zero mapped physical switch
ports and unknown client/neighbor counts are expected for this WAN-only profile.
`health` checks source/inform freshness, not controller adoption. `exec` probes the
running service without creating another container competing for its fixed lab IP.

Keep **all four files in this order** for subsequent gateway recreations. Without
the gateway override Compose selects the original US8 profile and volume again.
If there is no CRS service, omit `compose.multidevice.yml` from these commands.
Never use `down -v` as part of this migration.

## Choose the WAN interface

No `.env` change is needed for `ether1`. To make the selection explicit, or to use
another current RouterOS name, add these non-secret values to the **existing**
`deploy/lab/.env`, preserving all its other settings:

```dotenv
MTUNIFI_ROUTEROS_WAN_INTERFACE=ether1
# Optional synthetic controller hostname:
MTUNIFI_GATEWAY_HOSTNAME=mtunifi-rb760-ugw3
```

Recreate `emulator` with the same four-file command after changing the selection.
The selected interface may be Ethernet, PPPoE or VLAN, for example `pppoe-out1` or
`vlan-wan`. Its **own** counters are read; the collector does not sum parent and
encapsulated interface traffic. A missing/renamed interface fails explicitly.
Changing WAN selection keeps the gateway's MAC/key. It does not reclassify an US8.

Outside this Compose lab, select `MTUNIFI_PROFILE=ugw3-experimental`,
`MTUNIFI_TELEMETRY_SOURCE=routeros`, `MTUNIFI_ROUTEROS_WAN_INTERFACE=ether1`, the usual
RouterOS credentials/transport and a **fresh** `MTUNIFI_STATE_DIR`. A switch map is
not required. Inventory settings use the same fields without the `MTUNIFI_` prefix;
each device still needs its own state directory and synthetic identity.

## Observation contract

- `/interface/print` supplies selected-interface flags and absolute RX/TX bytes.
  **WAN RX = downloaded bytes, WAN TX = uploaded bytes.** Counters remain integers;
  UniFi computes rates between informs. Actual RouterOS uptime is reported and a
  counter/uptime decrease produces a reset warning without clearing adoption.
  UniFi may omit rates on the first reset sample and calculate them on a later inform.
- Physical Ethernet optionally uses `/interface/ethernet/print` and `monitor once`
  for link speed/duplex. PPPoE/VLAN do not inherit an invented parent speed or duplex.
  Down/disabled links retain observed counters; unknown details are omitted.
- Optional `/ip/address/print` supplies a scoped IPv4 address and prefix. It prefers
  `actual-interface` scope; disabled/invalid addresses are excluded. Denied, malformed
  or multiple distinct candidates do not produce a guessed primary address. No
  prefix, public address, default route or internet connectivity is assumed.
- Gateway informs map the selected RouterOS interface to the UGW3 logical WAN
  alias **`eth0`** in `config_port_table`, `if_table` and `uplink`. UniFi 10.6.106
  accepts arbitrary names as uplinks but requires this model alias for `wan1`.
  Diagnostics retain the actual source name, such as `ether1` or `pppoe-out1`.
  They do not send switch `port_table`/LLDP or claim the RB's six
  physical ports are a USG's three. Interface MAC is the synthetic gateway identity.
- Uptime/CPU/memory use the existing read-only resource collector. Gateway mode
  does not query switch FDB, ARP, DHCP leases or neighbor cache. LAN clients/ports,
  routing, NAT/firewall/DHCP configuration, DPI, latency, speed tests and WAN health
  probes are outside this profile. CRS326 retains its switch/client observations.
- Optional permission/endpoint failures degrade diagnostics while valid WAN
  counters continue. A broken API stream or missing/malformed required WAN data
  prevents that inform; no stale or synthetic replacement is sent. Keys are retained.
  UniFi may retain earlier values when optional fields disappear.

The existing `read,api` account is sufficient for the allowed commands; no new
write permission is needed. API/8728 remains the user's explicit plaintext LAN
transport. API-SSL continues to verify certificates without fallback.

## Return to US8

Recreate only the RB service using the original three M5 files:

```sh
docker compose -f compose.simulator.yml -f compose.routeros.yml -f compose.multidevice.yml up -d --no-deps --force-recreate emulator
```

This mounts the retained `simulator-state` and selects US8 again. If its controller
record was not deleted, its previous adoption can resume. If you forgot it in UniFi,
follow [explicit re-adoption](adoption.md#re-adopt-a-device-deleted-from-the-controller)
with these three files. The gateway volume remains available for another switch
back. Do not copy keys between volumes or bypass the profile guard.

## Validation

See [executed validation](validation.md) for automated native API/container checks
and UniFi 10.6.106 observations. RouterOS observations in the cloud come from
independent API fixtures. Actual RB WAN traffic, browser graphs and long-term
history still need verification on the user's deployment. The earlier M5 synthetic
feasibility experiment is retained in that report as historical evidence.
