# Milestone 3: observed wired clients, VLAN and IPv4 enrichment

RouterOS mode now reads `/interface/bridge/host` as attachment authority. Only a
currently learned FDB MAC on an explicitly mapped, enabled, live physical interface
is emitted through that port's UniFi `mac_table`. Several MACs can share a port.
They are observations **learned through that port**, including downstream switches
and uplinks; this does not prove direct cabling or create inferred topology nodes.

ARP and DHCP add optional IPv4/name metadata. They never create clients or override
the FDB port. MikroTik need not provide DHCP or routing; without enrichment tables,
FDB clients remain visible with unknown IP/name. Native API collection is read-only
and uses the existing dedicated `read,api` account and secret.

## Upgrade the existing RouterOS lab

From `deploy/lab` in PowerShell or a POSIX shell:

```sh
git pull
docker compose -f compose.simulator.yml -f compose.routeros.yml build emulator
docker compose -f compose.simulator.yml -f compose.routeros.yml up -d --force-recreate emulator
docker compose -f compose.simulator.yml -f compose.routeros.yml logs -f emulator
```

The same `.env`, API IP restrictions, map, password secret and adoption volume apply.
No new environment variables, account permissions or re-adoption are needed. The
synthetic M1 source remains separate and keeps its existing scenario behavior.
For an API probe without UniFi communication, stop the emulator first to release the fixed lab
IP, then use the existing `probe-routeros` command in [RouterOS setup](routeros.md).

## Source commands and provenance

The API allowlist adds only these print operations to M2:

| API path | Use |
| --- | --- |
| `/interface/bridge/print` | VLAN filtering, bridge PVID/ageing time and local MAC exclusion |
| `/interface/bridge/vlan/print` | Configured and current tagged/untagged membership, dynamic rows and VID ranges |
| `/interface/bridge/host/print` | MAC, bridge, VID, on-interface and local/dynamic/external flags |
| `/ip/arp/print` | Completed IPv4/MAC observations scoped to the L3 interface |
| `/ip/dhcp-server/lease/print` | Bound leases, active address/MAC, optional hostname |
| `/ip/dhcp-server/print` | Scope each local lease server to its interface |

No client IDs, lease scripts, comments, exports or passwords are fetched. Missing
optional endpoints/permissions produce safe warning codes. A broken connection
aborts the poll as in M2, preserving the adoption identity/key.
Native API limits remain 4096 rows and 4 MiB per response. Hitting a limit fails
the poll; a truncated FDB is never presented as complete.

## Attachment and exclusions

- The normalized identity is **bridge + MAC + VID**. Multiple rows for that identity
  on different interfaces are ambiguous and suppressed, with the alternatives in
  diagnostics. Identical duplicate rows are deduplicated.
- Local FDB flags, all physical/logical interface MACs, bridge/admin MACs and the
  emulator's own MAC are excluded. Ordinary globally administered client MACs are
  accepted. Multicast/zero MACs and invalid/disabled entries are not clients.
- Dynamic and externally learned hardware-offload FDB entries are supported. Both
  emit learned (`static=false`) UniFi records. Static-only FDB configuration has no
  proof of a live endpoint and remains diagnostic inventory, not an online client.
- A bond or unmapped interface remains unresolved. Even a single active bond member
  does not justify guessing which physical port learned a MAC. Disabled/down links
  and contradictory bridge membership suppress client emission.
- UniFi's MAC table has no bridge namespace. If the same MAC/VID occurs in multiple
  bridges, those records are suppressed instead of merging isolated L2 domains.

## VLAN and IPv4 rules

With VLAN filtering enabled, FDB VID 1–4094 is required for an emitted client.
Missing/zero VID is unresolved. A bridge with VLAN filtering disabled does not
distinguish client VLANs; normalized VLAN is unknown and the wire field is omitted.
No default VLAN 1 is invented. Configuration is observed only; no VLAN is created.

Tagged/untagged and **current** membership remain separate in diagnostics. VLAN
ranges are stored compactly. Hardware offload requested/active flags remain distinct.

ARP/lease scope must match the FDB bridge and VID:

- A VLAN interface directly over a known bridge provides an explicit VID scope.
- An unfiltered bridge scopes to that bridge with unknown VLAN.
- An IP on a filtered bridge itself is scoped to its bridge PVID only when current
  untagged VLAN membership includes that bridge CPU interface.
- Other/stacked logical parents or unresolved server/interface scopes are left
  unknown. ARP on an individual enslaved physical interface is not guessed into a
  bridge/VLAN scope. No MAC-only cross-VLAN fallback is used.

ARP failed/incomplete entries are ignored. Only bound, unblocked/active DHCP leases
are considered; active address/MAC fields take precedence over configured values.
The DHCP server must have an unambiguous interface scope. A lease for an absent FDB
MAC never appears as a client.

A single consistent IPv4 candidate is emitted, with `arp`/`dhcp` provenance in
diagnostics. Conflicting or multiple addresses remain unknown; up to eight candidate
IPs are retained for review. A unique matching bound-lease hostname is optional
(ASCII hostname characters, at most 63 characters). No DNS lookup, fabricated IP,
IPv6-to-IPv4 substitution or ARP/DHCP-derived attachment is used.

## Aging, moves and outages

RouterOS owns dynamic FDB expiration. On the next complete successful poll, a missing
row is removed from the emitted table, even if its DHCP lease remains bound. Missing
ARP or DHCP removes enrichment only, not attachment. Bridge ageing time is diagnostic
context; the collector does not guess last-packet time or apply an extra client TTL.

A move updates the physical port on the next poll and resets continuous observation
uptime. Reappearance, process restart, detected router/counter reset, a failed poll
or unavailable FDB breaks that observation window. This is not actual host uptime.
Informs between polls carry the age of the last FDB observation rather than claiming
a newly sampled MAC table on every provisioning request.

An unavailable or malformed FDB is different from a successful empty FDB:

- **Unavailable/invalid:** omit every MAC table, report client count `null`, keep port
  telemetry/informs running with degraded status. Previous client data is not sent as
  fresh data, and no authoritative empty table is fabricated.
- **Successful empty:** emit empty MAC tables and report count 0.
- **ARP/DHCP denial:** keep valid FDB attachment; only the missing enrichment is unknown.
- **Connection/required-port failure:** suppress the whole inform as in M2.

## Diagnostics and controller behavior

```sh
docker compose -f compose.simulator.yml -f compose.routeros.yml exec emulator mtunifi diagnose
```

Inspect `wired_clients_source=routeros-fdb`, count, `clients_status`, unresolved count,
and `routeros_telemetry.observations.wired`. Normalized clients expose source interface,
bridge/VID, attachment status, IP provenance/candidates and observed uptime. The
inventory records source availability, total FDB rows and excluded/skipped rows.
Bridge/VLAN configuration is under `observations.bridges` / `bridge_vlans`. Logs contain
counts and safe error codes; the private diagnostic snapshot contains observed MAC/IP/name metadata.
Counts represent emitted MAC/VID entries; a MAC learned in two VLANs contributes twice.

UniFi 10.6.106 accepted two fixture-sourced clients on port 2, including an unknown-IP
client without a fabricated address/VLAN. It also accepted a port 2→3 move, cleared
that client's IP when enrichment disappeared in this test, and stayed Connected while
FDB access was denied. A successful empty FDB still left old controller records cached.
VLAN-aware clients with VID 20/30 also appeared on ports 2/3 with their scoped IPs.
No controller-side client purge is used. Exact offline timing and rapid reassociation
remain controller behavior; multiple VLANs for one MAC may collapse in controller
client views despite distinct VID records in the inform.

See [validation](validation.md) for executed tests. M3 FDB/enrichment on the physical
RB760iGS still needs local confirmation; the user has confirmed M2 live ports/statistics.
LLDP/MNDP and real topology remain milestone 4.
