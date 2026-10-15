# RouterOS mapping plan — not implemented in M0

Do not begin full RouterOS integration until synthetic switch adoption is demonstrated
or the blocker is documented. No RouterOS connections or writes occur in this revision.
No RouterOS credentials are required for development of M0.

For M2 use RouterOS 7 HTTPS REST or verified API-SSL with a dedicated read-only account,
restrict it to the collector source IP and grant only required read/API permissions.
Do not enable write/policy/sensitive rights or plaintext management for the emulator.
Exact permissions and endpoint support must be verified on the target RouterOS build;
no configuration command is prescribed before that experiment.

| Normalized entity | Proposed source | Required handling |
| --- | --- | --- |
| NetworkDevice / DeviceHealth | system identity/resource | RouterOS version, uptime; unknown health stays unknown |
| PhysicalPort | interface/ethernet and monitor | Separate Ethernet from bridges/bonds/VLANs; missing speed/duplex stays unknown |
| LogicalInterface | interface/bridge, bridge/port, vlan, bonding | Preserve membership and hardware offload |
| TrafficCounters | interface stats | RX/TX direction explicit; uptime/counter reset invalidates rate baseline |
| VLAN | bridge/vlan and bridge/port | PVID, tagged/untagged ports, dynamic membership; preserve VLAN context |
| WiredClient | bridge/host plus ARP and DHCP lease enrichment | FDB physical-port authority; MAC+VLAN key, local/router MAC exclusion |
| Neighbor | ip/neighbor (LLDP/MNDP) | Protocol provenance, local physical port, age and unknown remote port |

An ARP or DHCP entry does not prove physical attachment. A missing IP does not make an
FDB client offline. Age FDB entries based on observed expiry, not ARP absence. Several
MACs behind one port may indicate an unmanaged switch; preserve uncertainty rather
than inventing intermediate topology. Do not assume MikroTik is DHCP server or gateway.
Treat failed optional API tables as unavailable with a warning, not as empty authoritative
data. Missing required interface/FDB data must degrade the snapshot explicitly.

Later tests must cover hardware offload, VLAN-aware bridges, duplicate MAC across VLANs,
unknown IP, MAC movement/aging, local MAC exclusion, incomplete neighbors, counter reset,
permission denial, endpoint absence and per-device reconnect. These tests are deferred,
not silently represented by protocol tests in M0.
