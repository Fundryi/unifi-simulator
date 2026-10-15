# Current limitations and milestone gate

This revision implements experimental M0/M1 synthetic telemetry and M2/M3 read-only
RouterOS API collection. A successful hardware probe is user-confirmed; detailed
hardware telemetry/UI validation remains pending.
The M0 success criterion is an actually adopted switch remaining Connected in a real
test UniFi controller; codec tests and a synthetic HTTP peer cannot satisfy that criterion.
See `validation.md` for the real 10.6.106 controller API result. Manual UI inspection
remains separate from controller API validation. See `synthetic-telemetry.md` for M1 results.

- US8 profile and candidate firmware were accepted by the 10.6.106 controller API;
  hardware fidelity, other releases and UI rendering still need validation.
- Only one eight-slot profile; RouterOS mode maps at most eight Ethernet/SFP ports.
  Reserved slots are disabled; bridges/bonds/VLANs are inventory, not extra physical ports.
- Three synthetic wired clients and LLDP were accepted by 10.6.106. Removed clients
  and rapid reverse port moves can retain cached associations; immediate offline/
  reassociation and exact aging time are not guaranteed. No controller-side purge is used.
- Read-only FDB clients and scoped IPv4 ARP/DHCP enrichment are implemented. No
  LLDP/MNDP collection, VLAN configuration or hardware health sensors are implemented.
- FDB bond/unmapped/conflicting/static-only entries are not guessed into live physical
  clients. Unknown IP/VID is not fabricated; one MAC/VID in multiple bridges is ambiguous.
- M3 still needs actual-hardware client validation. UniFi can cache removed clients
  and merge one MAC across VLANs; exact offline/reassociation behavior is not guaranteed.
- RouterOS 7.24+ (7.x stable) is version-gated. The user confirmed a successful
  RB760iGS 7.24.4 probe after correcting API allowed source IPs. Detailed API values,
  port activity/statistics in UniFi are user-confirmed. Detailed counters, optional
  warnings and M3 physical-hardware client behavior remain unreviewed.
- Required RouterOS API failure suppresses inform telemetry and may make the emulator
  appear offline. Optional unknown values are omitted and can retain cached UI values.
- API-SSL verifies trust and hostname. The requested API/8728 mode sends credentials
  in plaintext; it is explicit, never an automatic fallback.
- Synthetic counters reset to configured baselines when the process restarts, with
  uptime reset. The diagnostic snapshot does not restore a prior process's counters.
- Last-good scenario retention is in memory. An invalid file still prevents restart.
- Client/LLDP API presence does not prove topology graph or traffic-chart UI rendering.
- No UDP 10001 discovery, inbound SSH adoption, STUN or remote shell.
- Snappy and packet/data versions outside the documented subset are rejected.
- Controller system configuration, upgrades, reboot and executable commands are ignored.
  The controller can keep provisioning/retrying if it requires those effects.
- Private-key `setdefault` resets only local adoption metadata. Offline deletion
  in the controller requires explicit `reset-adoption`; HTTP 400 never clears keys.
- Echoed cfgversion indicates receipt only; it cannot enforce a real switch configuration.
- Persistent state stores a private protocol key in plaintext in a mode-0600 file.
  Protect volume backups and filesystem access. Initial bootstrap is publicly decryptable;
  CBC is unauthenticated. Use an isolated trusted lab, not an untrusted network.
- Inform defaults to HTTP because that is the device protocol path; HTTPS works only
  if the lab controller explicitly supports it with a trusted certificate.
- Diagnostic `exchanging` and container health do not prove Adopted/Connected UI status.
- Adoption/Connected is observed for the synthetic profile with 10.6.106 only;
  no compatibility claim for 8.0.24, other versions or actual RouterOS hardware.
- This profile advertises a resolved IPv4 inform endpoint; IPv6-only controllers
  are unsupported. DNS transport names remain supported with normal TLS verification.
- Controller image v8.0.24 is old, third-party software, intended only for an isolated
  disposable experiment; image availability alone does not prove it starts on every host.

Do not import real device MACs, serial numbers or keys. Do not change production
controller databases. Lab packet captures may reveal management secrets; keep them
private and build only synthetic public fixtures. No production deployment is recommended.
