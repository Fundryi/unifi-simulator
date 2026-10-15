# Authorized local adoption experiment

Use only a controller and equipment you administer. The emulator creates its own
locally administered random MAC; never replace it with a physical UniFi identity.
No UniFi login credentials are needed by the emulator. Initial adoption uses the
public protocol bootstrap constant; the controller must issue a new private key.

## Disposable UniFi Network 8.0.24 lab

Requires Linux, Docker Compose, roughly 2 GiB RAM and disk for the controller image.
This starts an independent controller with its own volume, not an existing database.
On your local machine (the cloud onboarding interface does not provide web previews):

```sh
cp .env.example .env  # only if you do not already have .env
# Edit .env: MTUNIFI_INFORM_URL=http://controller:8080/inform
docker compose -f docker-compose.yml -f compose.lab.yml up -d controller
docker compose -f docker-compose.yml -f compose.lab.yml logs -f controller
```

Open the controller UI on your machine using HTTPS, host 127.0.0.1, port 18443.
Complete the local setup wizard with a new lab account. The UI uses a self-signed
certificate; trust the lab certificate explicitly. Inform communication does not
disable TLS checks. Do not expose that UI or Docker socket publicly. The controller
image includes its own MongoDB; no production database is contacted.

Then start the emulator on the same Compose network:

```sh
docker compose -f docker-compose.yml -f compose.lab.yml up -d --build emulator
docker compose -f docker-compose.yml -f compose.lab.yml logs -f emulator
docker compose -f docker-compose.yml -f compose.lab.yml exec emulator mtunifi diagnose
```

In the controller Devices view, look for the new synthetic MAC/hostname. If listed,
choose Adopt. The emulator repeats informs; no repeated manual set-inform is needed.
Observe `factory` -> `key-received` -> `exchanging` in logs. These are protocol phases,
not proof of UI adoption. Confirm Adopted/Connected in the UI for at least five minutes,
eight physical ports, port 1 at 1 Gb/s, others down, and no fabricated clients.
Restart only the emulator, then only this lab controller, and check reconnection:

```sh
docker compose -f docker-compose.yml -f compose.lab.yml restart emulator
docker compose -f docker-compose.yml -f compose.lab.yml restart controller
```

Record controller version, image digest, profile, packet version, encryption, safe
diagnostics, observed UI state and duration in `docs/validation.md`. Do not record keys,
passwords, system_cfg or raw capture data. Keep acceptance and rejection results.

Stop the experiment with `docker compose -f docker-compose.yml -f compose.lab.yml down`.
Volumes remain for restart. Do not use `down -v` unless you deliberately want to erase
only this disposable lab's controller data and emulator identity.

## Existing authorized isolated controller

Set MTUNIFI_INFORM_URL to its reachable HTTP(S) `/inform` endpoint. Port 8080 is normally
the device inform service; 8443 is normally controller UI, not the inform endpoint.
Use a separate state volume for each controller and each synthetic device.
MTUNIFI_DEVICE_IP must be a controller-reachable address of the emulator host, not
the documentation address 192.0.2.10. Outbound-only adoption may not require this
address to accept connections, but inbound SSH adoption is not implemented.

```sh
export MTUNIFI_INFORM_URL=http://YOUR-LAB-CONTROLLER:8080/inform
export MTUNIFI_STATE_DIR=/path/to/dedicated-private-lab-state
export MTUNIFI_DEVICE_IP=YOUR-EMULATOR-IP
uv run --frozen mtunifi run
```

Use a writable directory owned by the runtime user; Docker named volumes initialize
with UID 10001. Avoid using the same state directory for concurrent processes.

Optional real-controller test (explicitly creates/uses a synthetic device in the lab):

```sh
MTUNIFI_ALLOW_CONTROLLER_TEST=yes uv run --frozen --extra dev pytest -q -s -m controller
```

It requires the exported URL and state directory above. A pass means only a valid
encrypted response was received. UI adoption and sustained connection are manual checks.

## Failure diagnosis and unresolved handshake

- Connection errors: verify controller service, DNS/routing and TCP 8080. Container
  loopback is not the host or another container; use the Compose service name.
- HTTP 404: may precede discovery; check the UI. It is not a successful inform.
- **UniFi 10.6.106, Ready to Adopt -> Adopting, then HTTP 400:** the original emulator
  copied a Docker DNS hostname into the payload's `inform_url`. Controller logs show
  `invalid inform_ip unifi-network-application`. The fixed emulator resolves an IPv4
  address for this JSON field, while keeping the configured hostname for the actual
  HTTP request, Host header, TLS verification and persisted controller binding. No
  identity reset, key import, SSH service or database edit is needed. Update/rebuild
  **the simulator only**, preserving its existing volume and `.env`:

  ```sh
  git pull
  cd deploy/lab
  docker compose -f compose.simulator.yml up -d --build --force-recreate emulator
  docker compose -f compose.simulator.yml logs -f emulator
  ```

  If the device is still pending, choose Adopt once in the controller. Watch for
  `key-received`, successful `setparam`/`noop`, and `exchanging`; verify Connected in
  the controller. Do not run `down -v`, change the configured controller URL or delete
  the identity to apply this fix. Leave Inform Host Override unset for the initial
  experiment; if needed, use the controller's reachable **IPv4 address**, not its
  Docker DNS hostname. The emulator does not apply controller-selected URL changes.
- Other HTTP 400 / decrypt failures: record packet version and encryption. Try a fresh
  disposable identity separately with packet version 0, or initial GCM, one variable
  at a time. Never force bootstrap fallback for an adopted identity or borrow real keys.
- Pending adoption indefinitely: controller might require inbound SSH or additional
  switch fields/capabilities. This implementation provides neither an SSH service nor
  forged firmware upgrades. Record the blocker and obtain an authorized switch trace.
- State belongs to another URL: stop and select a separate directory/volume; do not
  bypass this check by editing the stored key or MAC.
- Unknown messages: metadata is logged, payloads remain redacted even with MTUNIFI_TRACE=true.
  Collect safe field names/types manually from private lab traces when needed.

Network: emulator needs outbound TCP to the configured inform endpoint and normal
DNS. It exposes no inbound ports. UDP discovery, STUN and SSH are intentionally not
required or opened. Downloading development dependencies uses PyPI; images use Docker
Hub (and its registry/CDN destinations). No RouterOS network access is needed in M0.
