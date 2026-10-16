# Milestone 5: independent devices and CRS326

The lab now supports the existing **RB760iGS at 192.168.0.249** and a separate
**CRS326-24G-2S+ at 192.168.0.251**. Each has its own synthetic UniFi MAC, private
adoption key, state, RouterOS session/secret, polling cache and failure backoff.
The CRS profile is `usw-pro24-experimental` / UniFi model `US24PRO2` (Switch Pro 24,
non-PoE): 24 gigabit slots plus two 10 Gb SFP+ slots. No PoE readings are fabricated.
The RB keeps its existing US8 profile and adoption volume.

The collector still requires stable RouterOS **7.24+ (7.x)**. Port names in
[the CRS map](../examples/routeros-crs326.json) assume `ether1`–`ether24` and
`sfp-sfpplus1`/`sfp-sfpplus2`; check a probe if these have been renamed. The actual
CRS version/API settings and hardware readings must be verified locally. The lab
defaults to the same explicitly selected LAN API/8728 transport as the RB; verified
API-SSL is available through the CRS-specific variables below.

## Upgrade the existing Compose lab

Run from the repository root, in PowerShell or a POSIX shell:

```sh
git pull
python scripts/init-routeros-secret.py --device crs326
```

The helper prompts without echoing the password, preserves existing files and writes
UTF-8 without a BOM. The new file is `.secrets/routeros-crs326-password`; the RB's
`.secrets/routeros-password` is retained. POSIX directory/file permissions are checked
on Linux; on Windows, keep `.secrets` private using your NTFS access permissions.
Neither passwords nor adoption keys belong in `.env` or an inventory.

Prepare a dedicated `read,api` account on the CRS through your normal MikroTik
management channel, as in [RouterOS setup](routeros.md). Restrict the account/service
source IP to the Docker host's actual LAN source after NAT. No write, policy,
sensitive, SSH or REST permissions are needed. The emulator changes no RouterOS
configuration or discovery settings.

Add this **username only** to the existing `deploy/lab/.env`, preserving its other
values. Use the account you prepared, rather than assuming this example exists:

```dotenv
MTUNIFI_CRS326_USER=unifi-reader
```

Then from `deploy/lab`:

```sh
docker compose -f compose.simulator.yml -f compose.routeros.yml -f compose.multidevice.yml build
docker compose -f compose.simulator.yml -f compose.routeros.yml -f compose.multidevice.yml up -d
docker compose -f compose.simulator.yml -f compose.routeros.yml -f compose.multidevice.yml logs -f emulator emulator-crs326
```

The original service remains `emulator`, using `simulator-state` and 172.30.88.10.
The new service is `emulator-crs326`, using `crs326-state` and 172.30.88.11. Both use
the external `unifi-lab` network; neither joins the database network or publishes
ports. Security settings, read-only scenarios and UID 10001 are inherited by the new
service. The original controller Compose project is unchanged.

In UniFi, adopt **only the new `mtunifi-crs326` / Switch Pro 24**. The existing RB
needs no reset or new adoption. Do not remove its volume or reuse it for the CRS.
Inspect each source independently:

```sh
docker compose -f compose.simulator.yml -f compose.routeros.yml -f compose.multidevice.yml exec emulator mtunifi diagnose
docker compose -f compose.simulator.yml -f compose.routeros.yml -f compose.multidevice.yml exec emulator-crs326 mtunifi diagnose
docker compose -f compose.simulator.yml -f compose.routeros.yml -f compose.multidevice.yml exec emulator-crs326 mtunifi probe-routeros
```

An `exec` probe uses the running service's settings without creating another static
IP/container or modifying adoption state. It only reads RouterOS. A CRS/API failure
cannot stop the RB's process or clear its key. Fix/recreate one service without
restarting the other:

```sh
docker compose -f compose.simulator.yml -f compose.routeros.yml -f compose.multidevice.yml up -d --force-recreate emulator-crs326
```

| CRS variable | Default / meaning |
| --- | --- |
| `MTUNIFI_CRS326_USER` | Required read-only username |
| `MTUNIFI_CRS326_HOST` | 192.168.0.251 |
| `MTUNIFI_CRS326_PORT`, `MTUNIFI_CRS326_TLS` | 8728, false (explicit plaintext LAN API) |
| `MTUNIFI_CRS326_CA_FILE` | Optional CA path **inside** the container; mount the CA for verified API-SSL |
| `MTUNIFI_CRS326_MAP` | routeros-crs326.json, relative to mounted examples |
| `MTUNIFI_CRS326_IP`, `MTUNIFI_CRS326_HOSTNAME` | 172.30.88.11, mtunifi-crs326 |
| `MTUNIFI_CRS326_POLL_INTERVAL`, `MTUNIFI_CRS326_TIMEOUT` | 10 seconds, 20 seconds (26 monitor reads) |

The profile and map must agree. A US8 map still accepts at most eight slots; the new
profile accepts up to 26, with 10 Gb capacity on slots 25/26. Unmapped slots remain
disabled, and unknown actual speed/duplex stays unknown. FDB clients and LLDP retain
the M3/M4 provenance rules even on slots above eight.

## Inventory CLI: alternative to separate containers

`--inventory` supervises up to 32 independent child processes, using the same normal
single-device CLI in each. A child crash/startup error is retried independently
(5, 10, 20, then 30 seconds); normal source/HTTP retry stays inside that child's
existing emulator. Logs gain `device_id`. SIGTERM/SIGINT stops children and releases
state locks. Inventory changes apply on supervisor restart; synthetic scenario hot
reload remains per device.

[examples/fleet-routeros.json](../examples/fleet-routeros.json) is an editable example.
Paths are relative to the inventory file. Every entry needs a unique stable ID,
explicit state directory, hostname and advertised IPv4 address. Supply actual
controller/reachable advertised addresses, read-only usernames and mounted/local
secret/map/CA paths. Each API endpoint and identity is separate. State directories
must not overlap, including through symlinks. Existing duplicate MACs copied between
state directories are rejected at fleet startup.

The example creates **fresh** `.state/fleet-rb760igs` and `.state/fleet-crs326` states;
it does not import the running Compose volumes. Choose one deployment mode for your
targets. Use the Compose upgrade above to preserve the current Docker adoption;
do not also start this fresh inventory against the same devices/controller.

```sh
mtunifi validate-inventory --inventory examples/fleet-routeros.json
mtunifi run --inventory examples/fleet-routeros.json
mtunifi run --inventory examples/fleet-routeros.json --once
mtunifi diagnose --inventory examples/fleet-routeros.json
mtunifi health --inventory examples/fleet-routeros.json
mtunifi probe-routeros --inventory examples/fleet-routeros.json --device crs326
```

`validate-inventory` is offline and creates no state or sessions. It rejects unknown
fields, inline passwords, duplicate IDs/IPs/hostnames/API endpoints and overlapping
state paths. Inherited `MTUNIFI_*` settings are removed from child environments so
one device cannot silently inherit another's target or secret path. Other environment
settings, including the executable path, are preserved. Secrets remain file-based.

Diagnostics/probe/health aggregate each device's `exit_code` and safe `result`; the
aggregate returns 1 if any child fails. `--once` waits for one exchange per device and
returns 1 if any fails, without suppressing successful peers. Invalid inventory exits
2 before starting workers. `--device ID` scopes a command. Reset deliberately requires
one explicit device, and its state owner must first be stopped:

```sh
mtunifi reset-adoption --inventory examples/fleet-routeros.json --device crs326
```

If running the supervisor in one Docker container, mount inventory/maps/secret files
read-only, provide writable private state directories, and override the image's default
single-device health check with `mtunifi health --inventory /path/inventory.json`.
The provided Compose uses one container per device, so its normal health checks work.

## Persisted identity guards and limits

M5 records the selected profile and RouterOS hostname in each identity. Existing
M0–M4 controller-bound states are recognized as US8 and upgraded on normal use with
the same MAC/key. An adopted US8 cannot be silently reclassified as Switch Pro 24;
a bound RouterOS hostname cannot be replaced with the second device's hostname.
Credential/CA rotation or transport/port changes at the same hostname remain allowed.
Reset-adoption preserves these bindings. Use separate state for a different device
or class. M5 adds state fields; keep a volume backup before downgrading to older code
that rejects unknown fields.

LLDP retains reported neighbor MACs; M5 does not rewrite real neighbor identities to
another emulator's random MAC. Controller topology-node merging/browser rendering
is separate from observed links. See [validation](validation.md) for executed results,
[neighbor rules](topology.md), and [the optional RB UGW3 gateway/WAN deployment](gateway-wan.md).

## Two views of the RB: US8 plus UGW3

To retain RB switch/client telemetry alongside the UGW3 WAN, add
`compose.rb760-dual.yml` as the fourth file instead of `compose.gateway.yml`.
See [dual-role migration](gateway-wan.md#run-us8-and-ugw3-together) for service/IP/volume
assignments and readoption of a deleted US8. This does not change the CRS service
or relax inventory endpoint/identity guards.
