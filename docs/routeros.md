# Milestones 2–5: read-only RouterOS API collection

This collector targets stable **RouterOS 7.24+ (7.x)**. It reads physical Ethernet/SFP
ports, link state/speed/duplex, absolute RX/TX byte counters, uptime, CPU and memory.
Bridge/VLAN/bond inventory and bridge membership are retained as diagnostic context.
They are never counted as extra physical ports. Milestone 3 adds observed FDB clients
with scoped ARP/DHCP enrichment and VLAN context; see [wired clients](wired-clients.md)
for upgrade instructions, aging and ambiguity handling. No synthetic clients are added
in RouterOS mode. Milestone 4 adds [observed neighbors and topology provenance](topology.md).
Milestone 5 adds [independent CRS326 collection, a 26-port profile and inventory CLI](multi-device.md).
The optional [UGW3 gateway profile](gateway-wan.md) reads a selected WAN interface
(default `ether1`) with a separate adoption identity instead of a switch port map.

The requested deployment is **RB760iGS, 192.168.0.249:8728, RouterOS 7.24.4**, using
the native API without TLS. The lab override explicitly selects that transport.
It sends API credentials and observations in plaintext on that connection. API-SSL
with certificate and hostname verification is also supported and is the Python
client's default; there is no automatic fallback between transports.

## Dedicated read-only account

On the MikroTik, an administrator should prepare a dedicated group with only
`read,api` policies and a separate user in that group. Do not use the built-in `read`
group blindly: it can grant additional policies. The collector does not require
`write`, `policy`, `sensitive`, `test`, SSH, REST, DHCP service or routing roles.
Restrict the account and API service/firewall to the collector's actual source IP.
With normal Docker bridge NAT this is usually the Docker host's LAN IP.

Enable/restrict the selected API service through your existing management channel.
The emulator never creates users, enables services or changes firewall/device config.
Endpoint/permission behavior must still be checked on the actual 7.24.4 device.

The only wire commands allowed by the client are:

| Command | Purpose |
| --- | --- |
| `/login` | API session authentication |
| `/ip/neighbor/print` | Existing LLDP/MNDP/CDP observations; no discovery configuration changes |
| `/system/resource/print` | Version, uptime, CPU and memory |
| `/system/identity/print` | Optional identity metadata |
| `/interface/print` with `stats` | Interface kind, flags, absolute byte counters |
| `/interface/ethernet/print` | Physical interface names, IDs, MACs |
| `/interface/ethernet/monitor` with `once` | Link status, rate, duplex, autonegotiation |
| `/interface/bridge/port/print` | Optional bridge membership, PVID, offload flags |
| `/interface/vlan/print` | Optional VLAN interface parent/ID |
| `/interface/bonding/print` | Optional bond membership |
| `/interface/bridge/print`, `/interface/bridge/vlan/print` | Bridge/VLAN context and local MACs |
| `/interface/bridge/host/print` | Learned MAC/VID/port attachment |
| `/ip/arp/print` | Optional scoped IPv4 enrichment |
| `/ip/address/print` | Optional selected-interface IPv4/prefix, gateway mode only |
| `/ip/dhcp-server/lease/print`, `/ip/dhcp-server/print` | Optional bound lease/name enrichment and server scope |

No arbitrary command string is configurable. Read requests use bounded `.proplist`
fields; interface comments, credentials, scripts, exports and raw config are not fetched.
Controller provisioning still cannot write to RouterOS.

## Start with the existing two-project lab

From the repository root, update the source and create the password secret locally:

```sh
git pull
python3 scripts/init-routeros-secret.py
```

The helper prompts without echoing the password and preserves an existing secret.
It creates `.secrets/routeros-password` in a mode-0700 directory excluded from Git
and Docker builds. The file is mode 0444 so container UID 10001 can read the Compose
file-secret bind mount; other host users cannot traverse the private parent directory.
For rotation, replace that file locally, keeping these permissions, then recreate
the emulator so Docker mounts the replacement. Never place the password in `.env`.

Add the following non-secret settings to **the existing** `deploy/lab/.env`, preserving
its MongoDB passwords and all existing lab settings:

```dotenv
MTUNIFI_ROUTEROS_USER=unifi-reader
MTUNIFI_ROUTEROS_HOST=192.168.0.249
MTUNIFI_ROUTEROS_PORT=8728
MTUNIFI_ROUTEROS_TLS=false
MTUNIFI_ROUTEROS_MAP=routeros-rb760igs.json
```

The shipped [RB760iGS map](../examples/routeros-rb760igs.json) uses `ether1`–`ether5`
and `sfp1` in UniFi slots 1–6; slots 7/8 are disabled placeholders for the existing
US8 profile. Port 1 is explicitly marked uplink; change this if your wiring differs.
If interfaces were renamed, copy the JSON into `examples/local/`, adjust the names,
make the file readable by UID 10001, and set e.g.
`MTUNIFI_ROUTEROS_MAP=local/my-routeros.json`. Match current interface names exactly.

Keep the controller running. Stop the emulator first to release its fixed lab IP,
then build and probe from `deploy/lab`:

```sh
cd deploy/lab
docker compose -f compose.simulator.yml stop emulator
docker compose -f compose.simulator.yml -f compose.routeros.yml build emulator
docker compose -f compose.simulator.yml -f compose.routeros.yml run --rm --no-deps emulator probe-routeros
```

`probe-routeros` makes only read-only MikroTik API requests. It prints normalized
observations and warnings without the password, contacts no UniFi controller, and
does not create or alter adoption state. A successful probe reports six mapped
physical ports, version 7.24.4, actual link state/counters, and `source=routeros`.
Missing optional data produces `status=degraded` with safe warning codes.

After the probe succeeds:

```sh
docker compose -f compose.simulator.yml -f compose.routeros.yml up -d --force-recreate emulator
docker compose -f compose.simulator.yml -f compose.routeros.yml logs -f emulator
docker compose -f compose.simulator.yml -f compose.routeros.yml exec emulator mtunifi diagnose
```

This uses the same Compose project, named state volume, synthetic UniFi MAC and
adoption key. **Do not reset adoption or delete volumes just to change data source.**
The physical MikroTik's MACs remain observations, never the emulator's UniFi identity.
The advertised UniFi device IP remains the emulator's address, not the MikroTik API IP.
UniFi model/firmware fields still use the experimental US8 profile; the actual
RouterOS version and board name are retained in normalized diagnostics.

To return to M1, recreate using only `compose.simulator.yml`; the same identity is retained.

## Optional verified API-SSL

Use a server certificate with the RouterOS DNS name or IP in its SAN. Configure the
API-SSL service with that certificate. In `.env` set `MTUNIFI_ROUTEROS_TLS=true`,
`MTUNIFI_ROUTEROS_PORT=8729`, and use that matching hostname/IP as the host.
For a private CA, copy only its public PEM certificate to
`examples/local/routeros-ca.pem`, readable by UID 10001, and set
`MTUNIFI_ROUTEROS_CA_FILE=/scenarios/local/routeros-ca.pem`. Otherwise the system
trust store is used. Expired, untrusted and hostname-mismatched certificates fail
before sending credentials. There is no skip-verification option.

## Python CLI without Docker

The CLI reads exported environment variables, not `.env` files. From the repo root:

```sh
export MTUNIFI_TELEMETRY_SOURCE=routeros
export MTUNIFI_SYNTHETIC_FILE=
export MTUNIFI_ROUTEROS_HOST=192.168.0.249
export MTUNIFI_ROUTEROS_PORT=8728
export MTUNIFI_ROUTEROS_TLS=false
export MTUNIFI_ROUTEROS_USER=unifi-reader
export MTUNIFI_ROUTEROS_PASSWORD_FILE="$PWD/.secrets/routeros-password"
export MTUNIFI_ROUTEROS_MAP_FILE="$PWD/examples/routeros-rb760igs.json"
.venv/bin/mtunifi probe-routeros
```

Set the existing controller/state/IP variables before `mtunifi run`. The offline
`sample` command remains synthetic-only, preventing accidental fake RouterOS data.

## Polling, missing data and recovery

- `MTUNIFI_ROUTEROS_POLL_INTERVAL`: 10 seconds by default, bounded to 5–300.
  Provisioning informs between polls reuse the snapshot without querying the router again.
- `MTUNIFI_ROUTEROS_TIMEOUT`: 10 seconds by default, bounded to 1–120 for the entire
  connection/login/collection. Each poll creates one session and closes it afterwards.
- Required resource/physical-interface/counter failure stops that inform. No stale
  snapshot or synthetic replacement is sent. The controller may eventually mark the
  emulator offline; adoption credentials remain intact for recovery.
- Failed polls retry with backoff capped at 60 seconds. Recovery reconnects using the
  same UniFi identity; successful polling clears the source error.
- Optional permission/endpoint rejection marks data unavailable and warns. A failed
  bridge table is `null`, not an authoritative empty table. A broken connection aborts
  the entire poll, including when it happens during an optional request.
- Unknown link speed/duplex is omitted, never guessed. UniFi can retain an earlier
  cached value; display behavior for missing fields requires real-controller inspection.
- Counters are actual ingress RX / egress TX bytes from the MikroTik. No local rate
  extrapolation or counter persistence is used. The MikroTik's uptime is sent with them;
  decreasing uptime/counters produces a diagnostic reset warning. CPU/memory use
  observed values only; missing/invalid values are omitted.
  Hardware-offloaded traffic accuracy depends on the counters exposed by that device;
  the collector neither sums logical interfaces nor invents unreported switch-chip traffic.
- Map changes require recreation; API ordering changes do not change slot assignments.
  A missing/renamed mapped physical interface fails explicitly; bridges, bonds and
  VLANs cannot silently replace it. US8 still limits representation to eight slots.
  Faster-than-gigabit observations are preserved with a profile warning; no UI support
  for those rates is claimed.
- `diagnose` reports source, freshness, normalized observations and warnings. Client
  count now reflects emitted FDB clients, or `null` when FDB is unavailable. `health` also requires
  a recent successful RouterOS snapshot and valid inform; it never probes the router.

## Diagnosing connection failures

Older images collapse several socket failures into `RouterOS connection-failed`.
That message alone cannot distinguish refusal, DNS/routing, a reset, or EOF during
login/collection. Update/rebuild the simulator to obtain the specific reason, numeric
`errno`, stage and configured target (`host`, `port`, `tls`). Neither the username,
password, raw server error nor partial response is logged.

Stages are `connect` / `connect-tls`, `credentials`, or an allowlisted API command
such as `/login`, `/system/resource/print` or `/interface/print`. TLS CA setup failures
use `tls-configuration`. Runtime failures also emit a safe `routeros_failure` event
and retain these details in `diagnose`.

| Reason | What to check |
| --- | --- |
| `connection-refused` | API service/port or a rejecting firewall rule |
| `network-unreachable` / `dns-resolution-failed` | Container routes, LAN/VPN access, subnet collisions, DNS |
| `connection-closed` / `connection-reset` | Stage of peer closure, selected API transport/port, service/account source restrictions, RouterOS logs |
| `tls-handshake-failed` / `tls-verification-failed` | API-SSL port/certificate settings; do not disable certificate checks |
| `authentication-failed` | Dedicated account with `read,api` and the local secret |
| `timeout` | Reachability or a stalled API command; inspect the stage |

A successful TCP check from the Docker host does not establish container reachability.
Even successful container TCP can be followed by `connection-closed` at `/login` if
the API service restricts the source IP range. This was confirmed on the user's
RB760iGS / 7.24.4: correcting the API allowed-address range made the probe succeed.
Match the actual source address seen by RouterOS after Docker/host NAT.
From `deploy/lab`, with the emulator stopped as for the normal probe, test the same
Compose network and effective host/port without reading credentials:

```sh
docker compose -f compose.simulator.yml -f compose.routeros.yml run --rm --no-deps \
  --entrypoint python emulator -c 'import os,socket; h=os.environ["MTUNIFI_ROUTEROS_HOST"]; p=int(os.environ["MTUNIFI_ROUTEROS_PORT"]); print({"host":h,"port":p,"tls":os.environ["MTUNIFI_ROUTEROS_TLS"]},flush=True); s=socket.create_connection((h,p),3); print("TCP OK"); s.close()'
```

This checks only TCP, not API login. If it fails while host TCP succeeds, inspect
Docker routing/firewall/NAT before changing credentials. If it succeeds, run the
updated `probe-routeros` and use its stage/reason to check the API session. Share only
the safe diagnostic output, never password files or a full rendered Compose config.

In Windows PowerShell, pass the Python code on standard input to avoid native
argument quoting removing the inner quotes from `python -c`:

```powershell
@'
import os
import socket
host = os.environ["MTUNIFI_ROUTEROS_HOST"]
port = int(os.environ["MTUNIFI_ROUTEROS_PORT"])
print({"host": host, "port": port, "tls": os.environ["MTUNIFI_ROUTEROS_TLS"]}, flush=True)
with socket.create_connection((host, port), timeout=3):
    print("TCP OK")
'@ | docker compose -f compose.simulator.yml -f compose.routeros.yml run --rm --no-deps -T --entrypoint python emulator -
```

## Validation status

The automated suite exercises actual loopback TCP API sentences, optional TLS with
certificate/hostname verification, protocol bounds, read-only command restrictions,
six physical ports vs logical interfaces, unknown fields, large counters, version
gating, failures/reconnect, resets and encrypted UniFi informs. See
[executed validation](validation.md) for results and container checks.
To repeat the Linux container checks after building the lab image, run from the repo root:

```sh
MTUNIFI_ALLOW_DOCKER_TEST=yes MTUNIFI_DOCKER_IMAGE=unifi-simulator-lab-emulator \
  .venv/bin/pytest -q tests/integration/test_routeros_docker.py
```

These use host networking only for loopback wire fixtures, temporary test state
volumes, mounted fixture secrets, UID 10001 and a read-only root filesystem.
The cloud connection to `192.168.0.249:8728` was refused and no RouterOS credentials
were available there. The user subsequently confirmed a successful local probe on
RB760iGS / 7.24.4 after correcting the API source-IP restriction and later confirmed
live port activity/statistics in UniFi. Detailed counter accuracy and optional warnings
remain unreviewed. M3 client visibility on that hardware is now user-confirmed;
M4 deployment success is now user-reported; exact neighbor values/browser topology
remain unverified. M5 deployment was subsequently confirmed working by the user;
the new gateway WAN profile still needs local hardware validation.
