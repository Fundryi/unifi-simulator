# RB760iGS as UGW3: read-only WAN telemetry

The opt-in `ugw3-experimental` profile presents a **UniFi Security Gateway / UGW3**
and reads WAN observations from RouterOS 7.24+. The default WAN interface is
**`ether1`**, including the requested RB760iGS at **192.168.0.249:8728**. It reuses
the existing read-only API account and password secret. Nothing is written to RouterOS.

This is a different controller device class. The gateway override uses a **new
state volume, synthetic MAC and adoption key**; the old US8 volume stays intact.
The CRS326 remains a separate Switch Pro 24 with its existing identity and clients.

The original `compose.gateway.yml` **replaces the US8 service with WAN-only UGW3**.
It does not preserve switch/client telemetry inside the gateway. To keep both views
of the RB, use [simultaneous US8 + UGW3](#run-us8-and-ugw3-together) instead.

## Run UGW3 in place of US8

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

## Run US8 and UGW3 together

Use [compose.rb760-dual.yml](../deploy/lab/compose.rb760-dual.yml) **instead of**
`compose.gateway.yml`, last after the original three M5 files. Do not merge both
role overrides. The dual override leaves the original `emulator` service intact
and adds `emulator-gateway`:

| Service | Profile / role | Default lab IP | State volume |
| --- | --- | --- | --- |
| `emulator` | US8, RB ports/clients/neighbors | 172.30.88.10 | original `simulator-state` |
| `emulator-gateway` | UGW3, selected RB WAN | 172.30.88.12 | existing `rb760-gateway-state` |
| `emulator-crs326` | Switch Pro 24, CRS ports/clients | 172.30.88.11 | existing `crs326-state` |

Both RB collectors use `192.168.0.249`, the same dedicated read-only account/secret
and separate API sessions. The gateway selection still defaults to `ether1`.
The two RB views have different synthetic MACs/keys; they are two logical devices
representing one physical router. Do not add their interface counters together when
calculating total network usage. No physical link/topology between these two logical
identities is invented. The inventory CLI's duplicate-target guard is unchanged;
this dual-role deployment uses separate Compose services.

**Existing single gateway migration, with US8 already deleted in UniFi:** run the
following from `deploy/lab` (single lines work in Windows PowerShell). Stop the old
`emulator` before moving its gateway volume to `emulator-gateway`:

```sh
git pull
docker compose -f compose.simulator.yml -f compose.routeros.yml -f compose.multidevice.yml -f compose.rb760-dual.yml stop emulator
docker compose -f compose.simulator.yml -f compose.routeros.yml -f compose.multidevice.yml -f compose.rb760-dual.yml build emulator emulator-gateway
docker compose -f compose.simulator.yml -f compose.routeros.yml -f compose.multidevice.yml -f compose.rb760-dual.yml run --rm --no-deps emulator reset-adoption
docker compose -f compose.simulator.yml -f compose.routeros.yml -f compose.multidevice.yml -f compose.rb760-dual.yml up -d --no-deps --force-recreate emulator emulator-gateway
```

The reset selects the retained **US8** identity and clears only its obsolete key;
its MAC and target stay unchanged. Adopt that US8 in UniFi again. The UGW3 uses its
existing gateway volume/MAC/key and hostname `mtunifi-rb760-ugw3` at the new lab IP;
an already-adopted gateway normally needs no reset or re-adoption. If that UGW3 was
also deleted, reset **`emulator-gateway`** separately after stopping that service,
using the same complete file list. CRS stays running throughout.

If US8 is still adopted, **omit the reset command**. If this is a fresh deployment
with no US8 identity, also omit reset and adopt both new RB views after starting.
Do not reset an undeleted device or delete/copy volumes. If the RouterOS API host
was changed in `.env`, restore the host originally bound to each identity first.

For custom lab subnets, choose a distinct `MTUNIFI_GATEWAY_IP` in that subnet; the
default is `172.30.88.12`. Keep the existing `UNIFI_SIMULATOR_IP` and `MTUNIFI_CRS326_IP`
settings for the other two services. All three addresses must be distinct.

Inspect either role by selecting its service, always with the dual file list:

```sh
docker compose -f compose.simulator.yml -f compose.routeros.yml -f compose.multidevice.yml -f compose.rb760-dual.yml exec emulator mtunifi diagnose
docker compose -f compose.simulator.yml -f compose.routeros.yml -f compose.multidevice.yml -f compose.rb760-dual.yml exec emulator-gateway mtunifi diagnose
```

All future build/up/reset commands for this deployment must keep
`compose.rb760-dual.yml` last. A single-file synthetic startup cannot use the
RouterOS-bound US8 volume and fails safely with the binding error described in
[readoption](adoption.md#re-adopt-a-device-deleted-from-the-controller).

## Choose the WAN interface

No `.env` change is needed for `ether1`. To make the selection explicit, or to use
another current RouterOS name, add these non-secret values to the **existing**
`deploy/lab/.env`, preserving all its other settings:

```dotenv
MTUNIFI_ROUTEROS_WAN_INTERFACE=ether1
# Optional synthetic controller hostname:
MTUNIFI_GATEWAY_HOSTNAME=mtunifi-rb760-ugw3
```

After changing the selection, recreate the gateway service with its full file list:
`emulator` with `compose.gateway.yml` in the single-gateway deployment, or
`emulator-gateway` with `compose.rb760-dual.yml` in the dual deployment. For example:

```sh
docker compose -f compose.simulator.yml -f compose.routeros.yml -f compose.multidevice.yml -f compose.rb760-dual.yml up -d --no-deps --force-recreate emulator-gateway
```

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

For the **single-gateway** deployment, recreate only the RB service using the
original three M5 files:

```sh
docker compose -f compose.simulator.yml -f compose.routeros.yml -f compose.multidevice.yml up -d --no-deps --force-recreate emulator
```

In the dual deployment, US8 already runs: stop `emulator-gateway` with the full
dual file list if WAN collection is no longer wanted, before omitting that override.

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
