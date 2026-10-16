# RouterOS mapping — M2–M4 implementation and later work

Synthetic adoption was demonstrated in M0/M1. M2 now implements read-only native
RouterOS 7.24+ API collection and vendor-neutral normalization; M3 adds
[FDB/ARP/DHCP client correlation](wired-clients.md); M4 adds
[neighbor evidence and LLDP export](topology.md). See [deployment](routeros.md).
No RouterOS writes exist. The user confirmed a successful RB760iGS 7.24.4 probe;
live port activity/statistics and M3 wired clients in UniFi are also user-confirmed.
M4 hardware neighbor collection remains unverified.

Use verified API-SSL or the user's explicitly requested LAN API on 8728 with a dedicated read-only account,
restrict it to the collector source IP and grant only required read/API permissions.
Do not grant write/policy/sensitive rights. Plaintext API is an explicit transport
selection and never a fallback after failed TLS verification.
Exact permissions and endpoint support must be verified on the target RouterOS build.

| Normalized entity | Proposed source | Required handling |
| --- | --- | --- |
| NetworkDevice / DeviceHealth | system identity/resource | M2: version, uptime, CPU/memory; unknown health stays unknown |
| PhysicalPort | interface/ethernet and monitor | M2: explicit slot/name mapping; missing speed/duplex stays unknown |
| LogicalInterface | interface kinds, bridge/port, vlan, bonding | M2: parent/member inventory; configured offload separated from observed active flag |
| TrafficCounters | interface stats | M2: absolute RX/TX bytes, device uptime, reset warning; no guessed rates |
| VLAN | bridge/vlan and bridge/port | M3: compact VID ranges, configured/current tagged/untagged and dynamic membership, scoped enrichment |
| WiredClient | bridge/host plus ARP and DHCP lease enrichment | M3: FDB physical-port authority; MAC+VLAN key, local/router MAC exclusion |
| Neighbor | ip/neighbor (LLDP/MNDP/CDP) | M4: reported provenance/age; only unambiguous LLDP-only physical links exported; unknown ports retained diagnostically |

An ARP or DHCP entry does not prove physical attachment. A missing IP does not make an
FDB client offline. Age FDB entries based on observed expiry, not ARP absence. Several
MACs behind one port may indicate an unmanaged switch; preserve uncertainty rather
than inventing intermediate topology. Do not assume MikroTik is DHCP server or gateway.
Treat failed optional API tables as unavailable with a warning, not as empty authoritative
data. Missing required interface data suppresses informs. Failed/invalid FDB omits
MAC tables and reports unknown clients while port informs continue; a successful
empty FDB emits empty MAC tables. Static-only or ambiguous attachment stays diagnostic.

M2 tests cover wire/TLS/API errors, permissions, physical/logical separation, configured
vs active hardware offload, VLAN parents, bonds, counters/resets and reconnect.
M3 tests cover VLAN-aware FDB, duplicate MAC across VLANs/bridges, unknown/conflicting
IP, moves/removal/reappearance, local MAC exclusion, external hardware learning,
ambiguous bonds and optional-source failures. M4 tests cover neighbor protocols,
unknown fields, ambiguous ports, aliases, age and optional failures. No inferred
switches or direct links are created from FDB counts.
