# RB760iGS as a gateway: WAN feasibility and proposed next profile

**A USG profile is a viable next step for WAN visibility.** A separate laboratory
experiment on UniFi Network 10.6.106 adopted a fresh synthetic `UGW3` / UniFi Security
Gateway, reached Connected with a newly issued private key and GCM, and produced
nonzero `wan1.rx_bytes-r` / `wan1.tx_bytes-r` values from increasing fixture counters.
This is stronger evidence than merely changing a model string. The observed device
API classified it as `type=ugw` and exposed WAN byte counters, IP and link speed.
It was then deleted through the supported API; no database writes were used.

The experiment used synthetic counters and test-net addresses, not the RB's WAN.
The browser traffic graph, long-term history and actual RouterOS WAN collection were
not validated. It is a feasibility experiment, **not an available production profile**.
M5 intentionally keeps the user's already-adopted RB as US8 and adds the CRS as a
separate Switch Pro 24. No existing switch was forgotten or reclassified.

## Recommendation

Implement a separate opt-in `UGW3` gateway adapter rather than turning a US8 into a
router by changing its name. It is the candidate already demonstrated with our
outbound adoption protocol. Other UXG/UDM-class models were not tested; their model
names alone do not establish compatibility with an external Network controller.

The gateway profile needs a distinct telemetry contract:

- Explicit WAN interface selection: physical Ethernet, PPPoE or VLAN, as actually
  configured on the RB. Read its own counters; do not sum an encapsulated interface
  and its physical parent, which would double-count traffic.
- RX on WAN means downloaded bytes; TX means uploaded bytes. Keep large integer
  counters, actual uptime, reset detection and unknown/down state. Controller-side
  rates must be tested over multiple informs and after counter/device resets.
- Device-side `config_port_table` associates logical interface names with WAN/LAN
  roles. `if_table` carries interface counters/link state; `uplink` identifies WAN.
  The controller consumed these into `wan1` in the experiment. The switch's
  `port_table.mac_table` is not a gateway/WAN schema.
- Add bounded read-only IP-address observations for the selected interface if WAN
  IP/prefix display is required. Missing/denied data must remain unknown. Do not
  assume the RB provides DHCP, has a public address, or uses an untagged Ethernet WAN.
- Keep controller provisioning metadata read-only. Do not apply its gateway
  firewall/NAT/DHCP/VLAN configuration to RouterOS, claim internet connectivity from
  link-up alone, invent latency/speed-test/DPI results or run controller commands.

The exact RB WAN interface is still required before collecting real WAN data.
A gateway represents the routed role, not all six RB physical ports as a USG's three
ports. The CRS can retain detailed switching/clients, while a future RB gateway
adapter reports WAN and explicitly mapped LAN observations.

## Migration contract

A gateway is a different controller device class. Use a **new synthetic identity and
state volume**, keep the existing US8 volume intact for rollback, stop its old
emulator, and adopt the gateway in the isolated lab. Do not reuse a switch's private
key or import physical MikroTik MACs. Removing the old UI device is an explicit
operator action after deciding to migrate; M5 does not perform it automatically.

Proposed acceptance checks: real read-only RB counters at the selected WAN, gateway
Connected under a new key, correct RX/TX direction and computed WAN rates, interface
loss/recovery/reset, restart persistence, and browser/history display. The initial
lab experiment has established adoption and controller-computed rates only.
