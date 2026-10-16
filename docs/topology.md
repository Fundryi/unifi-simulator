# Milestone 4: observed neighbors and topology provenance

RouterOS mode reads the existing `/ip/neighbor/print` cache with the same read-only
API account. It records LLDP, MNDP, CDP and unknown protocol observations separately
from FDB clients. Only eligible LLDP observations enter the device-side UniFi
`lldp_table`. FDB clients remain learned-through-port observations: several clients
on one port never create an invented intermediate switch or a direct cable claim.

## Upgrade the existing lab

From `deploy/lab`, in PowerShell or a POSIX shell:

```sh
git pull
docker compose -f compose.simulator.yml -f compose.routeros.yml build emulator
docker compose -f compose.simulator.yml -f compose.routeros.yml up -d --force-recreate emulator
docker compose -f compose.simulator.yml -f compose.routeros.yml exec emulator mtunifi diagnose
```

Keep the existing `.env`, password secret, port map and adoption volume. No new
variables, write permissions, reset or re-adoption are required. Synthetic mode
continues to use the M1 scenario. The emulator never enables discovery or changes
RouterOS configuration. If the cache is empty, check the switch's existing discovery
settings and advertisements on the neighboring equipment through your normal
management channel; an empty cache is a valid observation.

## Evidence and wire mapping

`probe-routeros` and `diagnose` expose
`observations.neighbors.observations` inside the RouterOS summary. Each record has
`evidence=routeros-neighbor-cache`, `certainty=reported`, the actual protocol list,
local interface list, optional neighbor MAC/identity, remote interface name,
system description, cache age and `export_reason`. There are no inferred edges.
These records describe what RouterOS reported; they do not certify physical cabling.

The sole additional API command is `/ip/neighbor/print`, requesting only
`interface,mac-address,identity,interface-name,discovered-by,age,system-description`.
No discovery settings, scripts, credentials or configuration are fetched/changed.
Missing properties stay unknown. In particular, older/different endpoint output
without `discovered-by` or `interface-name` is diagnostic only.

| RouterOS evidence | Normalized / UniFi representation |
| --- | --- |
| `discovered-by` | Protocol provenance, case-normalized; absent is `unknown` |
| `interface` | Original interface list, resolved only to one mapped physical slot |
| `mac-address` | Reported neighbor MAC; emitted as UniFi `chassis_id`, not imported as an emulator identity |
| `interface-name` | Reported remote interface, emitted as `port_id`; no generated port ID |
| `system-description`, then `identity` | Optional `chassis_descr`; omitted if unavailable |
| `age` | RouterOS cache age at poll time, or null if unparseable/absent |

The summary's outer `age_seconds` is time since the poll; add it to a known neighbor
cache age when interpreting cached diagnostics. Age is not device uptime, a TTL or
proof of a recently received packet. RouterOS owns cache expiration; each successful
poll replaces the previous observations. The emulator does not invent a timeout.

## Conservative export rules

An exported row needs an enabled/up mapped physical port, a unicast neighbor MAC,
a known remote interface and explicit **LLDP-only** provenance. Missing descriptions
are allowed and omitted. Global MACs are accepted; local interface, bridge/admin
and emulator MACs are excluded. Identical rows are deduplicated, using the youngest
reported age. Two differing LLDP identities/ports/descriptions on one local slot
remain ambiguous rather than choosing a direct neighbor.

| `export_reason` | Meaning |
| --- | --- |
| `eligible` | LLDP row emitted |
| `protocol-not-lldp` | MNDP, CDP, unknown or other protocol; preserved in diagnostics |
| `merged-protocols` | LLDP mixed with another protocol; cache fields cannot be attributed to LLDP alone |
| `remote-port-unknown`, `remote-mac-unknown` | Required remote identity is unavailable |
| `unresolved-local-interface` | Bridge/bond alone, unmapped, conflicting or unsupported interface list |
| `unverified-bridge-alias` | Physical+bridge list lacks matching observed bridge membership |
| `bond-member` | Physical member belongs to a bond; no guessed member attachment |
| `bond-membership-unknown` | A bond exists but its member inventory is unavailable |
| `local-port-down` | Disabled/down physical link |
| `ambiguous-local-port` | Differing LLDP advertisements on the same slot |
| `invalid-row` | Required cache identity/interface missing or malformed |

A bridge alias such as `ether2,bridge-lan` is accepted only when the port's bridge
membership is also observed. Bridge/bond/VLAN names are never extra physical ports.
MNDP/CDP and merged-protocol rows are not relabeled as LLDP. This intentionally
means a cache containing only those protocols can have observations but no UniFi
LLDP links. Unknown remote ports are not replaced by names such as `unknown`.

## Availability and diagnostics

`neighbors` counts exported LLDP rows, while `neighbor_observations` counts normalized
cache observations, including records not exported. `neighbors_status=observed`
means a complete cache read; it does not mean every observation is exportable.

- A complete empty cache sends `lldp_table=[]`, with `neighbors=0`.
- A complete cache with only ineligible rows also sends `[]`; observations and reasons
  explain why no LLDP links were emitted.
- Permission/endpoint failure or malformed required rows omits `lldp_table`, sets
  `neighbors=null` / `neighbors_status=unavailable` and marks the source degraded.
  Partial malformed evidence stays diagnostic, never an authoritative partial table.
  Port/client informs continue, with the same adoption key.
- A broken stream, timeout or native response limit aborts the whole poll; old
  observations remain diagnostic only, without synthetic replacement.

UniFi can retain previous values when a field is omitted. A subsequent successful
empty table explicitly reports no exportable neighbors. Controller API acceptance
of LLDP does not establish that its browser topology graph will draw every third-party
switch or resolve it to another managed UniFi device. See [executed validation](validation.md).
