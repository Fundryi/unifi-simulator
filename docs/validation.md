# Executed validation — 2026-10-15

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
| Newer UniFi Network versions | Not yet tested | Not yet tested | Not yet tested |
| RouterOS 7 | Not implemented | Not applicable yet | Not yet tested |

## Remaining M0 gate

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
