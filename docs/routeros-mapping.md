# RouterOS mapping — M2 implementation and later work

Synthetic adoption was demonstrated in M0/M1. M2 now implements read-only native
RouterOS 7.24+ API collection and vendor-neutral normalization; see [deployment](routeros.md).
No RouterOS writes exist. The real RB760iGS still needs local validation.

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
| VLAN | bridge/vlan and bridge/port | Later: full tagged/untagged/dynamic membership and client VLAN correlation |
| WiredClient | bridge/host plus ARP and DHCP lease enrichment | M3: FDB physical-port authority; MAC+VLAN key, local/router MAC exclusion |
| Neighbor | ip/neighbor (LLDP/MNDP) | M4: protocol provenance, local physical port, age and unknown remote port |

An ARP or DHCP entry does not prove physical attachment. A missing IP does not make an
FDB client offline. Age FDB entries based on observed expiry, not ARP absence. Several
MACs behind one port may indicate an unmanaged switch; preserve uncertainty rather
than inventing intermediate topology. Do not assume MikroTik is DHCP server or gateway.
Treat failed optional API tables as unavailable with a warning, not as empty authoritative
data. Missing required interface data suppresses M2 informs; FDB failures must be
handled explicitly when M3 introduces that source.

M2 tests cover wire/TLS/API errors, permissions, physical/logical separation, configured
vs active hardware offload, VLAN parents, bonds, counters/resets and reconnect.
Later tests must cover VLAN-aware FDB collection, duplicate MAC across VLANs,
unknown IP, MAC movement/aging, local MAC exclusion, incomplete neighbors, counter reset,
MAC correlation and neighbor age. Those behaviors remain deferred.
