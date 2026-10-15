# Current limitations and milestone gate

This revision is an experimental M0 protocol probe, not a working MikroTik integration.
The M0 success criterion is an actually adopted switch remaining Connected in a real
test UniFi controller; codec tests and a synthetic HTTP peer cannot satisfy that criterion.
See `validation.md` for the latest observed result. Further milestones remain gated.

- US8 profile and candidate firmware values need controller/hardware validation.
- Only one eight-port profile, fixed synthetic link states and zero counters.
- No wired clients, LLDP, VLANs, FDB, health sensors or RouterOS collector.
- No UDP 10001 discovery, inbound SSH adoption, STUN or remote shell.
- Snappy and packet/data versions outside the documented subset are rejected.
- Controller system configuration, upgrades, reset and commands are ignored.
  The controller can keep provisioning/retrying if it requires those effects.
- Echoed cfgversion indicates receipt only; it cannot enforce a real switch configuration.
- Persistent state stores a private protocol key in plaintext in a mode-0600 file.
  Protect volume backups and filesystem access. Initial bootstrap is publicly decryptable;
  CBC is unauthenticated. Use an isolated trusted lab, not an untrusted network.
- Inform defaults to HTTP because that is the device protocol path; HTTPS works only
  if the lab controller explicitly supports it with a trusted certificate.
- Diagnostic `exchanging` and container health do not prove Adopted/Connected UI status.
- No compatibility claim for UniFi 8.0.24 or newer until documented live experiments pass.
- Controller image v8.0.24 is old, third-party software, intended only for an isolated
  disposable experiment; image availability alone does not prove it starts on every host.

Do not import real device MACs, serial numbers or keys. Do not change production
controller databases. Lab packet captures may reveal management secrets; keep them
private and build only synthetic public fixtures. No production deployment is recommended.
