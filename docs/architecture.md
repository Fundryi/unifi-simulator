# Architecture and milestone boundary

M0 is a single synthetic device, using outbound UniFi device protocol. It provides
no dashboard, controller API facade, RouterOS integration or automatic network discovery.

```mermaid
flowchart LR
    Env[Environment configuration] --> Emulator[Inform loop / response state machine]
    Synthetic[US8 candidate synthetic profile] --> Emulator
    Store[Per-device atomic state file] <--> Emulator
    Emulator <--> Codec[TNBU codec / AES / zlib]
    Codec <-->|HTTP POST /inform| Controller[Isolated UniFi lab controller]
    Emulator --> Diagnostics[Structured metadata logs / diagnostic CLI]
```

`config.py`: validated settings, no credential-bearing URLs. `protocol.py`: bounded
binary codec, independent of networking. `telemetry.py`: small normalized PhysicalPort
dataclass and source-supported switch mapping. `emulator.py`: serialized state transitions,
bounded HTTP transport, retry loop and allowlisted management metadata. `storage.py`:
exclusive Linux file lock, atomic mode-0600 state replacement and fsync. `main.py`:
CLI, safe diagnostics and health command. A state volume survives process/container restarts.

The private adoption key is issued by the controller and persisted. No TLS/SSH device
keypair is generated because the implemented outbound protocol does not use one.
The public bootstrap constant is used only before receiving a private key. There is
no key downgrade fallback. HTTP redirects and proxy-environment routing are disabled
for inform traffic; HTTPS uses normal certificate validation. A trusted private CA can
be installed in the runtime trust store; never disable certificate verification.

Future RouterOS collector -> vendor-neutral snapshot -> switch mapper will replace
the synthetic source. Separate logical/physical interfaces and confidence/provenance
are required; see `routeros-mapping.md`. One Emulator/Store per device isolates keys,
controller state, collectors and reconnect handling. Only one process/device is exposed
by the M0 CLI; multi-device orchestration is deliberately deferred.

State does not persist raw controller configuration. CLI health checks recent valid
protocol exchange and last-error status, not controller UI state. Health does not probe
the controller or start/change identity. No inbound application endpoint is exposed.
