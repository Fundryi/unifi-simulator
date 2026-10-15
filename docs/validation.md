# Executed validation — latest 2026-10-16

## Milestone 3: FDB clients, VLAN and IPv4 enrichment — 2026-10-16

The user confirmed M2 live port activity and correct statistics from their RB760iGS
in UniFi before requesting M3. Physical-device M3 client collection has not yet been
reported. M3 was exercised with independent native RouterOS API wire fixtures and
the actual isolated UniFi 10.6.106 controller, using the production Docker image.
Every client entered through encrypted device-side informs; no controller client
creation/purge, direct database writes or real-device identity/key imports were used.

| Experiment | Observed outcome |
| --- | --- |
| Unfiltered bridge | Two FDB clients on port 2; unknown VID omitted rather than defaulted to VLAN 1 |
| ARP/DHCP enrichment | Known client IP/name accepted; external hardware-learned client retained with unknown IP/name |
| Local identities | Interface, bridge/admin, local-FDB and emulator MACs excluded in tests |
| Move and loss of enrichment | Source moved client 2→3; controller recorded port 3 and IP=null in that test |
| FDB permission denied | Port informs continued, controller stayed Connected, source client count=null/degraded |
| Complete empty FDB | Source emitted empty MAC tables/count=0; controller retained two cached client records |
| VLAN-aware bridge | New clients in VID 20/30 appeared on controller ports 2/3 with their scoped IPv4 values |
| Same MAC across VLANs | Native API/normalizer tests retained independent VID/IP records; controller's single-MAC view is not guaranteed to show both |
| Invalid/oversized FDB | Invalid identity rows yielded unknown inventory; native row limit aborted the poll without silently presenting a partial FDB as complete |
| Ambiguity | Bond/unmapped/static-only/contradictory attachments and duplicate bridge namespaces were not guessed into live physical ports |

The first real-controller run accepted ordinary global client MACs, an unknown-IP
hardware-learned client and live moves. A repeated run reusing those test MACs did
not establish the expected fresh associations within its test window; cached prior
port associations were retained. A full rerun with fresh, unique fixture MACs/IPs
passed initial attachment, movement, unavailable/empty FDB and VLAN 20/30 checks.
No cached client purge was used. This confirms the existing controller cache caveat
also matters when repeatedly forgetting/recreating test switches with reused clients.
No immediate offline timing or rapid reassociation guarantee is claimed.

Automated result with Docker opt-in: **188 passed, 1 skipped** (opt-in real-controller
pytest). Checks cover FDB authority vs ARP/DHCP-only rows, MAC movement/expiry/gaps,
unicast/global/local MACs, unknown/conflicting IP, bound/active DHCP identity and
server scope, VLAN-aware/unaware bridges, VID ranges/current membership, invalid
hostnames, down/disabled ports, optional permission/stream failures, packet age,
same GCM identity/key, and bounded response rejection. Both API/TLS container cases
verified actual emitted client MAC/IP data and unknown IP/VID omission as UID 10001.
Ruff lint/format and the Docker build passed; dependencies/lockfile were unchanged.
The same final image passed the existing synthetic Docker smoke check, including
GCM identity/key persistence, M1 clients/LLDP, read-only mounts and health.
Browser rendering and actual-hardware M3 client behavior remain unverified.

## RouterOS connection diagnostics — 2026-10-16

The user reported generic `RouterOS connection-failed` during the local probe.
They confirmed TCP connectivity from the Docker host and, using the PowerShell
stdin test, from the simulator Compose container to 192.168.0.249:8728 with TLS=false.
This establishes TCP reachability, not successful API authentication/collection.
The original message does not identify the failing API stage or exact socket error.

The client now classifies refusal, DNS/routing failures, reset/EOF, timeout and TLS
errors separately and reports a safe stage, numeric errno and configured target.
Raw exception/server text, partial responses and credentials are never included.
Tests cover real loopback refusal through the CLI, EOF during `/interface/print`,
authentication stage and sanitized OS error classification. Both production-image
TCP/TLS container checks also close the fixture listener and verify the resulting
`startup_error` contains `connection-refused`, target port and connect stage.
Result: **162 passed, 1 skipped** with Docker opt-in; regular suite **160 passed,
3 skipped**. Docker build and Ruff lint/format passed.

The updated user-side probe reported `connection-closed` at `/login`. The user then
identified the API service's allowed source IP range as the cause and confirmed the
probe succeeded after correcting that restriction. This is user-reported successful
read-only collection on the RB760iGS / RouterOS 7.24.4, following both host/container
TCP checks. The normalized result and actual-hardware UniFi display were not supplied,
so exact field values, optional warnings and UI behavior remain unreviewed.

## Milestone 2: RouterOS API collector — 2026-10-16

The user selected an RB760iGS at 192.168.0.249:8728 running RouterOS 7.24.4 and
explicitly requested native API without TLS. The cloud TCP connection was refused;
no RouterOS credentials were present. The initial checks below used fixtures; the
subsequent successful user-side hardware probe is recorded above. Full field/UI
validation remains separate. The local probe procedure is in `routeros.md`.

The implementation was tested against independent loopback RouterOS sentence peers,
both plaintext TCP and certificate-verified API-SSL. The fixtures represent six physical
ports, bridges, VLANs, a bond, very large counters, uptime and CPU/memory. No actual
MikroTik state was read or modified by those tests.

| Check | Observed outcome |
| --- | --- |
| Stable explicit RB760iGS mapping | Six named Ethernet/SFP ports despite reversed API ordering; two disabled US8 slots |
| Data fidelity | RX above 2^53 retained exactly, observed 1 Gb/s / 100 Mb/s links, down/disabled ports, device uptime |
| Logical interfaces/offload | Bridge, VLAN parent/ID and bond members retained separately; configured hw=yes did not fabricate active offload |
| Optional failure | Permission/endpoint rejection marked unavailable/degraded, unknown speed/duplex/health omitted |
| Required failure | No inform or synthetic replacement; old observation retained only for diagnosis, adoption key preserved |
| Recovery/reset | Bounded reconnect/backoff, counter/uptime decrease warning, no identity reset |
| Protocol bounds/auth | Reserved/oversized lengths, malformed/missing counters, unsafe command paths and authentication failures rejected/redacted |
| API-SSL | Trusted fixture accepted; untrusted and wrong-host certificates rejected before credentials |
| Container, TCP and TLS | UID 10001, read-only root, mounted secret/map/CA, CBC-to-GCM exchange, same identity/key across recreation, probe and health passed |
| Compose override | Same simulator project/state volume, dedicated lab network/static IP, explicit plaintext 8728, no synthetic file |
| Actual UniFi 10.6.106, RouterOS fixture source | Adopted/Connected via controller API, eight slots, source port 1 speed=1000 and RX=9007199254740994, reserved slot 7 down, CPU=23 / memory=50.0 accepted |

The last check used the production container and a real isolated UniFi controller,
but RouterOS observations came from the wire fixture, **not the physical RB760iGS**.
Browser rendering, real switch-chip counter accuracy and actual hardware reconnect
remain unverified. Only this experiment's synthetic device was adopted/deleted through
the lab API; no database edits or user adoption volumes were used.

Final automated result with Docker opt-in enabled: **153 passed, 1 skipped** (real
controller pytest opt-in). The two Docker cases are skipped without their explicit
opt-in; the regular suite is 151 passed / 3 skipped. Ruff lint/format, Docker build
with TLS verification retained, and merged Compose validation passed. Existing M0/M1
and re-adoption tests remain green. No dependency or lockfile changes were required.
The rebuilt image also passed the existing synthetic `scripts/docker-smoke.py`
check, including GCM/key persistence, scenario mounts, clients/LLDP and health.

## Re-adoption after deleting a device in 10.6.106

A fresh M1 container was adopted and reached Connected. It was stopped, then its
record was deleted through the controller's device deletion API. Restarting the
same container and volume reproduced the user's result: GCM `exchanging`, private
key retained, HTTP 400, and no matching device offered by the controller.

The rebuilt image ran `reset-adoption` as UID 10001 against that same named volume.
Its output retained the exact synthetic MAC and reported factory/no private key.
The same scenario then rediscovered that MAC as pending (`state=2`, `adopted=false`).
After Adopt, the controller reported Connected (`state=1`, `adopted=true`); diagnostic
output showed GCM/exchanging, a private key, no error and all three synthetic clients.
The container health command passed. No identity-file editing, manual key import,
database writes or deletion of the state volume was used. Browser UI was not inspected.

Final automated results: **95 passed, 1 skipped** (opt-in controller pytest), Ruff
lint/format and Docker build passed. New tests check the POST `/inform` request,
private-key HTTP 400 retention, one-time recovery hint, explicit bootstrap recovery,
valid private GCM `setdefault` versus wrong-key rejection, exclusive reset ownership,
MAC/telemetry preservation, missing state and controller-binding mismatch rejection.
The `setdefault` response path was tested with an encrypted synthetic peer; reception
of that command from a real controller was not observed in the offline deletion test.

## Milestone 1: synthetic telemetry on 10.6.106

The user confirmed the M0 adoption fix in their controller. M1 was then tested against
the same isolated LinuxServer 10.6.106 image used below, with a fresh synthetic identity.
An administrator lab session was established through the existing controller setup API.
All client/LLDP data below entered through device-side encrypted informs, not controller
client creation or database edits. Browser UI rendering was not inspected.

| Experiment | Observed outcome |
| --- | --- |
| Fresh M1 device adoption | Adopted, Connected (`state=1`), GCM setparam/noop |
| Demo port table | Eight ports, 1/2 at 1000 Mb/s, 3 at 100 Mb/s, others down |
| Traffic counters | Increasing RX/TX; controller rate fields about 125008 RX / 64004 TX bytes/s vs configured 125000 / 64000 (sampling/rounding) |
| Wired clients | All three MACs appeared in stat/sta with correct initial switch and ports 2/3/3 |
| Unknown IP / VLAN | Unknown-IP client had no fabricated IP; VLAN 20 retained |
| LLDP | Both source-supported neighbor records retained in device API |
| Live port edits | Port 2 down, port 3 changed to 10 Mb/s, port 5 up; state stayed Connected |
| LLDP removal | Port 2 down removed that neighbor from device LLDP table |
| Down-link counters | Port 2 bytes stayed fixed while active port 1 increased |
| Client move 3 -> 5 | Correct association appeared through stat/sta without new adoption |
| Malformed runtime JSON | Last good scenario retained, same key/identity exchanging, no input content logged |
| Valid file recovery | Source returned to active with three clients; controller restored port states and two LLDP records |
| Fast reverse client move 5 -> 3 | **Partial:** source emitted port 3 but controller kept cached sw_port=5; exact aging unresolved |
| Client removed by link down | **Partial:** controller retained cached record with old last_seen for at least 132 seconds |

The device API removes nested mac_table after processing it; separate wired client
records demonstrate it was consumed, not rejected. No fake station fields, fabricated
unknown IP, forced controller client purge, RouterOS connection or management writes
were used. The validation scenario was a Git-ignored local copy, preserving the shipped demo.

Automated coverage includes configuration validation, bounds, MAC/VLAN uniqueness,
large/fractional/reset/paused counters, live moves, hot reload/failure recovery,
diagnostics redaction, identity isolation and actual encrypted fixture exchanges.
Final results: **89 passed, 1 skipped** (explicit real-controller pytest opt-in),
Ruff lint/format passed, Compose configuration validated, final Docker image built
with TLS verification retained. The M1 container smoke check passed as UID 10001
with a read-only scenario mount, GCM key/identity persistence, three clients, two
LLDP records, diagnostic snapshot and protocol health.
See `synthetic-telemetry.md` for usage and the distinction between controller API
acceptance, cache behavior and unverified topology/chart rendering.

## Subsequent fix: real UniFi Network 10.6.106 adoption

A user reported Ready to Adopt -> Adopting and HTTP 404 -> 400 on 10.6.106.
The same failure was reproduced in the isolated LinuxServer lab (same image digest
as `deploy/lab/README.md`). Controller debug logging identified
`invalid inform_ip unifi-network-application`: the device's JSON inform_url contained
a Docker DNS name. CBC/GCM and packet version changes alone did not resolve it.

The fix advertises resolved IPv4 in JSON while retaining the configured DNS HTTP/TLS
destination and saved controller binding. A first corrected bootstrap request received
a new private key. The rebuilt production container then completed encrypted GCM
provisioning and noop exchanges. The actual controller API reported `adopted=true`,
`state=1` (Connected), eight ports, port 1 up at 1000 Mb/s. Emulator health passed.
No real-device key, manual key injection, SSH server or direct database modification
was used. An isolated lab administrator session was established through the setup API;
the browser wizard and port rendering were not inspected. Do not describe this as UI
validation or proof of supported configuration effects.

Current automated results: **60 passed, 1 skipped** (the default opt-in controller
test remains disabled). New checks cover DNS vs advertised address, unchanged HTTP Host
and saved binding, HTTPS scheme/port preservation, literal IPs, DNS failures/timeouts
and explicit IPv6 rejection. Ruff and Docker build passed. The rebuilt image also
passed `scripts/docker-smoke.py` with the synthetic peer and retained UID 10001/health.

A second, fresh production-image container with a new random synthetic identity and
empty state volume also completed adoption end to end: factory -> key-received ->
exchanging, with the actual controller reporting adopted/Connected. That test used the
normal image entrypoint, configured DNS URL and default CBC bootstrap; no packet
patching, preloaded key or state edits were used.

The first device was sampled through the real controller API for **320 seconds**,
including an emulator container restart with its existing state volume. All samples
reported `adopted=true`, `state=1`, eight ports and last_seen age at most 10 seconds.
The final sample was 320 seconds after the baseline with last_seen age 2 seconds.
The emulator retained its MAC/private key and continued successful GCM noop exchanges;
its health command passed. Controller restart and browser UI rendering were not tested.
Temporary debug settings were removed and only test containers started for this
experiment were stopped afterwards; persistent lab data and the primary identity were
retained. No raw traces or credentials were added to Git.

Earlier baseline results follow for historical context.

## Original onboarding baseline

Environment: Linux x86_64, host Python 3.12.14, uv 0.12.23, Docker 28.4.0,
Compose 2.40.3. Container runtime Python 3.12.15. No RouterOS device or user-supplied
controller/credentials were available. User requested a local lab test procedure.

| Check | Observed outcome |
| --- | --- |
| Frozen installation, `sh scripts/install-cloud.sh`, including second execution | Passed; lockfile unchanged |
| `pytest -q` | 50 passed, 1 skipped (opt-in actual-controller test) |
| Ruff lint and format | Passed |
| CBC/GCM independent PyCryptodome wire comparison, packet versions 0/1 | Passed with synthetic data; not a real device capture |
| Real loopback HTTP fixture: key rotation, encrypted noop, process-state restore, outage, controller restart | Passed; peer is synthetic, not UniFi |
| State integrity, per-device isolation, file locking, redaction and unsafe command handling | Passed |
| CLI sample and diagnose | Passed; eight synthetic ports, no network action or exposed key |
| Docker image build | Passed with build-time host CA bundle; dependency TLS/checksum verification retained |
| `scripts/docker-smoke.py` | Passed: real container informs to synthetic HTTP peer, retained identity/key across container recreation, GCM, health, UID 10001, read-only root and dropped capabilities |
| Base + lab Compose configuration validation | Passed with explicit lab inform URL |
| UniFi Network 8.0.24 container startup | Passed; controller logs reported startup completed |
| Initial CBC inform to that uninitialized controller | **Failed: HTTP 404**; no decoded protocol response, no key received |
| Adopted/Connected UI, sustained heartbeats to adopted real controller | **Not tested / not demonstrated**; lab setup wizard not completed |
| Switch port rendering, clients, VLANs, topology, RouterOS | Not tested; latter features outside M0 |
| GitHub Actions workflow | Provided; not executed on GitHub |

Controller image tested: `jacobalberty/unifi:v8.0.24`, pull digest
`sha256:23d3114328fcb21fd7a0ae37a38d7b65b4e21adc653b58aeadc1e3aecc4b30d6`.
The failed inform occurred before local administrator creation (read-only query
confirmed zero administrators and zero device records). The cause of the 404 is not
established; it cannot prove the payload is accepted, invalid, or undiscoverable after
controller initialization. No database writes, bypassed adoption, UI login, real-device
identity import or packet captures were used. Only this disposable controller was
started and stopped; its independent volume and image remain available locally.

## Compatibility matrix

| Target | Transport/startup | Adoption / Connected | Switch telemetry UI |
| --- | --- | --- | --- |
| Synthetic HTTP peer | Confirmed locally, CBC/GCM, versions 0/1 | State-machine simulation only | No UI exists |
| UniFi Network 8.0.24, US8 candidate | Controller started; pre-wizard inform returned 404 | Not yet tested after wizard | Not yet tested |
| UniFi Network 10.6.106, US8 candidate | Live encrypted key/provisioning/noop exchange | Adopted/Connected observed via controller API after DNS payload fix | Eight ports accepted into API; UI rendering not inspected |
| Newer UniFi Network versions | Not yet tested | Not yet tested | Not yet tested |
| RouterOS 7 | Not implemented | Not applicable yet | Not yet tested |

## Original M0 gate (before the 10.6.106 fix)

Complete the isolated controller wizard on a local machine, attempt adoption from
Devices, and observe the switch remain Connected for at least five minutes. See
`adoption.md` for exact commands and evidence to retain. If the switch is absent or
adoption stalls, obtain a private authorized US8 protocol trace to resolve required
identity/capability/state fields and SSH requirements. Do not begin full RouterOS
integration or claim compatibility based on the synthetic peer tests.

## Build trust correction

The first Docker build failed with a certificate verification error because the clean
base image lacked the cloud network proxy CA. No verification bypass was used.
BuildKit secret `build_ca` supplied the existing host trust bundle to pip and uv only
during installation; it is not copied into the runtime image. The successful command:

```sh
BUILDX_CONFIG=/workspace/.cache/buildx docker build \
  --secret id=build_ca,src=/etc/ssl/certs/ca-certificates.crt -t mtunifi:dev .
.venv/bin/python scripts/docker-smoke.py
```

`compose.cloud.yml` provides the same build secret for Compose. Home-cache writes are
redirected into `/workspace/.cache`. On an ordinary host with public trusted TLS, the
base Dockerfile needs no extra build secret. Do not use insecure/trusted-host flags.

## Persistence scope

Source is committed locally. No Git push or publication was performed. Cloud setup
instructions are saved separately as a configuration draft, not executed by saving.
Current-instance success does not prove a new task restores local-only Git commits
or Docker volumes. Before relying on remote `main`, publish the source through your
normal Git review workflow; the remote was empty at the start of this task.
