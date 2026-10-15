# Milestone 1: synthetic switch telemetry

M1 adds eight configurable ports, traffic counters, wired clients and LLDP neighbors.
Everything is **fictional lab data**. No RouterOS connection, traffic on virtual ports
or configuration change to real equipment takes place. Adoption remains persistent.

## Upgrade and try the demo

From the repository root:

```sh
git pull
cd deploy/lab
docker compose -f compose.simulator.yml up -d --build --force-recreate emulator
docker compose -f compose.simulator.yml logs -f emulator
docker compose -f compose.simulator.yml exec emulator mtunifi diagnose
```

Preserve `.env`, controller data and simulator state volume. No re-adoption or `down -v`.
The lab Compose selects `examples/synthetic-switch.json`, including with an older `.env`:

- Port 1: 1 Gb/s uplink, 125000 RX / 64000 TX **bytes/second**.
- Port 2: 1 Gb/s, a workstation with an LLDP neighbor using the same client/chassis MAC.
- Port 3: 100 Mb/s, two clients sharing a port; one has VLAN 20 and **no known IP**.
- Ports 4–8: down. Another LLDP record describes the synthetic upstream switch on port 1.

## Local scenario and live edits

From the repository root:

```sh
mkdir -p examples/local
cp examples/synthetic-switch.json examples/local/my-switch.json
chmod 755 examples examples/local
chmod 644 examples/local/my-switch.json
```

Set `MTUNIFI_SCENARIO=local/my-switch.json` in `deploy/lab/.env`, then recreate the
emulator as above. `examples/local/` is ignored by Git and the Docker build context.
The directory is mounted read-only at `/scenarios`; files must be readable by UID 10001.
They contain synthetic data, never secrets. Keep the database `.env` private.

Edit file contents **on the host**; each inform reloads the file. Atomic editor saves
work because the parent directory is mounted. No rebuild/restart is needed for content
changes. Selecting a different filename is an environment change requiring recreation.

The root `docker-compose.yml` uses MTUNIFI_SYNTHETIC_FILE, e.g.
`/scenarios/synthetic-switch.json`. For the Python CLI, use a **host filesystem path**:

```sh
MTUNIFI_SYNTHETIC_FILE=examples/local/my-switch.json .venv/bin/mtunifi validate-scenario
MTUNIFI_SYNTHETIC_FILE=examples/local/my-switch.json .venv/bin/mtunifi sample
```

Validation is offline and creates no identity. Errors report safe field paths/types
without echoing values or unknown keys. `sample` initializes/reuses a local identity
and prints an initial payload; running instances own the advancing counters.
Without a selected file the CLI/standalone image keeps the minimal M0 fixture.

## JSON fields

Root: `schema_version: 1`, `ports`, optional `clients` and `neighbors`. Types are strict,
unknown fields rejected, maximum 256 KiB, 128 clients and eight direct neighbors.
US8 requires **exactly eight unique ports**, numbered 1–8:

| Port field | Meaning / default |
| --- | --- |
| `port_idx` | Required integer 1–8 |
| `enabled` / `up` | Enabled (true) / virtual link (false); disabled ports cannot be up |
| `speed_mbps` | 10, 100 or 1000; default 1000 |
| `full_duplex` / `is_uplink` | Default true / false |
| `rx_bytes` / `tx_bytes` | Initial nonnegative signed 64-bit values, default 0 |
| `rx_bytes_per_second` / `tx_bytes_per_second` | Integer rates, default 0; bounded by line speed in each direction |
| `counter_epoch` | Nonnegative counter reset generation, default 0 |

Counters grow with monotonic elapsed time, not inform count. Fractional-byte carry
avoids polling drift; large baselines stay integers. Down links report speed 0 and
pause growth while preserving counts. Rate/link edits preserve counts and take effect
at the next observed reload. Saturation clamps at 2^63−1; no packets are transmitted.

Increment `counter_epoch` to reset a port to its initial rx_bytes/tx_bytes. Editing
either baseline also resets both counters. Process restart resets counters to baselines
and device uptime, preserving identity/key. Counters are not restored from diagnostics.
Controller graph handling of manual resets has not been inspected.

Clients: `mac`, `port_idx`, optional `vlan` (1–4094, default 1), `ip`, `hostname`.
Use locally administered unicast MACs, e.g. `02:00:00:01:00:01`. Omit unknown IPs.
Multiple clients can share one port; a MAC/VLAN pair has exactly one port. A move resets
its synthetic observation uptime. Down ports emit an empty MAC table.

Neighbors: `local_port_idx`, `local_port_name`, `chassis_id` (synthetic MAC),
`chassis_descr`, `port_id`, optional `port_descr`. One direct neighbor per port;
down ports emit none. Relationships are synthetic, not inferred real topology.
The emulator's own MAC cannot be reported as a client or neighbor.

## Failure handling and diagnostics

Invalid/missing initial files prevent startup before a new identity is created. Invalid
runtime edits retain the **in-memory** last good scenario and log one
`synthetic_config_rejected` event per rejection period. Valid recovery logs
`synthetic_config_loaded`; file contents are never logged. A restart with the file
still invalid fails rather than rebuilding a scenario from stale data.

`diagnose` includes `synthetic_telemetry`: current generated counters, MAC tables,
LLDP records, active/configured client counts, revision, timestamp and source status.
`using-last-good` flags a rejected edit. This atomic private `telemetry.json` snapshot
is separate from adoption secrets. It reports **emitted data**, not verified UI state.
`last_inform`, `last_error` and health continue to describe protocol communication.

## Observed on UniFi Network 10.6.106

Real device-side encrypted informs produced eight ports with correct link/speed/byte
values, nonzero controller-computed `rx_bytes-r`/`tx_bytes-r`, three wired clients with
correct initial `sw_mac`/`sw_port`, the unknown-IP client without a fabricated address,
VLAN 20 metadata and two LLDP records. See [validation](validation.md) for evidence.
The device API consumes/strips nested `mac_table`; inspect `stat/sta` for client records.

**Controller caching limitation:** after port down, the removed client stayed cached
with an old last_seen for at least 132 seconds. Moving a client from port 3 to 5 updated
its association, but a rapid return to port 3 retained the previous controller-side
port even while the emitted MAC table was correct. Exact aging and immediate reassociation
are not guaranteed. No controller-side delete/purge commands are used to hide this.

The browser UI, traffic charts and topology graph have **not** been inspected here.
Check Ports, Client Devices and Topology manually. An unknown LLDP MAC does not guarantee
a managed UniFi graph node. Client VLAN metadata does not configure VLAN membership,
a gateway or DHCP. RouterOS collection and real topology remain later milestones.
