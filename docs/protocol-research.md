# Device-side protocol research

Research date: 2026-10-15. This is an independent implementation from protocol facts.
No upstream source, captures, physical identity, or private adoption keys are included.
“Source-confirmed” below means present in the inspected source, **not tested against
a Ubiquiti controller by this project**. See `validation.md` for executed experiments.

## Sources inspected

Git checkouts were used because GitHub's REST API returned HTTP 403 in this environment.
Links pin the inspected revisions so that later upstream changes do not alter this evidence.

| Project / revision | Evidence | License / use |
| --- | --- | --- |
| [JohnKiller/TNBU](https://github.com/JohnKiller/TNBU/tree/ba2c22f4bdd46868ca621e51fb6165684769c555) | `TNBU.Core/Models/InformPacket.cs`, `Inform/BaseInformBody.cs`, `Inform/InformResponse.cs`, `TNBU.Simulator/DeviceSimulator.cs`, README | AGPL-3.0; studied wire facts and field names, no code copied. README reports controller-side adoption of USW-Pro-48-PoE, not validation of our US8 emulator. |
| [amd989/unifi-gateway](https://github.com/amd989/unifi-gateway/tree/d0f9308da8c38c8f83315030fc96dad4997ea681) | `unifi_protocol.py`, `unifi_gateway.py`, README | MIT, copyright Alejandro Mora; studied gateway handshake/framing, no source copied. Gateway telemetry is not evidence of switch acceptance. |
| [jeffreykog/unifi-inform-protocol](https://github.com/jeffreykog/unifi-inform-protocol/tree/63d75a085c816ad5126a77523878e4e4ab3a2a39) | README (repository contains protocol notes only) | No license file found; treated as reference, no text/code copied. Historical AP documentation. |
| [fxkr/unifi-protocol-reverse-engineering](https://github.com/fxkr/unifi-protocol-reverse-engineering/tree/0566e8e1ddc25717f51d5fba4ca03dbf53a585ad) | README, CBC/PKCS7 and example AP payload | No license file found; reference only. Notes explicitly caution about assumptions. |
| [qvr/unifi-gateway](https://github.com/qvr/unifi-gateway/tree/bf33a50ef4221f6457b3e5f0376892bb67d61419) | README and protocol module; predecessor cited by amd989 | No root license file found; no code copied. Another gateway implementation, not independent switch compatibility evidence. |

The additional sources were discovered through upstream references. This was a focused
source review, not an exhaustive search of GitHub (search API access was unavailable).

## 1. Inform packet and encoding

Source-confirmed in TNBU and gateway; locally exercised using both cryptography and
PyCryptodome, plus HTTP socket tests. All header integers are big endian:

| Offset | Length | Meaning |
| --- | --- | --- |
| 0 | 4 | ASCII `TNBU` |
| 4 | 4 | Packet version: TNBU simulator uses 0, gateway uses 1 |
| 8 | 6 | Device MAC, including in controller replies |
| 14 | 2 | Flags: 1 encrypted, 2 zlib, 4 snappy, 8 GCM |
| 16 | 16 | Random IV / nonce |
| 32 | 4 | Data version, JSON is 1 |
| 36 | 4 | Wire payload length, including GCM tag |
| 40 | variable | Compressed then encrypted JSON |

POST to `/inform`, normally TCP 8080, `application/x-binary`.
CBC uses AES-128 and PKCS#7 padding. Historical protocol notes call it unpadded,
which conflicts with both current code and fxkr's observations; we implement PKCS#7.
GCM uses a 16-byte nonce, full 40-byte header as additional authenticated data,
and a 16-byte tag appended to ciphertext. Replies are decoded with the key used
for the outgoing packet; a new key from the reply applies to the next exchange.

Snappy flag semantics conflict: prose calls it a modifier of flag 2, gateway source
sets flag 4 independently. The MVP rejects snappy explicitly rather than guessing.
Only encrypted responses and payload version 1 are accepted. JSON and inflated payloads
are bounded to 1 MiB. GCM is authenticated; CBC has **no message authentication**.

## 2. Discovery and adoption

Historical AP sources describe UDP 10001 broadcast to 255.255.255.255 and multicast
233.89.188.1 using TLV discovery. Header/type details disagree between sources;
we do not implement UDP discovery. Configuring an explicit inform URL is the
outbound layer 3 path; initial informs may cause the controller to list a pending device.

AP notes describe SSH `set-adopt` delivering a per-device key. TNBU simulator and
gateway instead consume a `setparam` response containing multiline `mgmt_cfg` and
`authkey=...`. This MVP supports that outbound response path only. A controller
requiring inbound SSH adoption is a feasibility blocker: no SSH server/default
password is provided. HTTP 404 after first inform is not proof of discovery or adoption.

## 3. Authentication and identity

Source-confirmed public bootstrap key: MD5 of the protocol string `ubnt`.
It is a public interoperability constant, not a private credential. It cannot protect
initial adoption from an active attacker. Subsequent keys must be newly issued for
this synthetic device; never import physical device secrets.

Identity is a persisted random locally administered unicast MAC and derived serial.
The MAC must not match real equipment. Local OUI acceptance by UniFi is unresolved.
State binds to one controller URL to prevent accidentally sending an adopted identity
to a different controller. No fallback to the bootstrap key after private-key failure.

## 4. Models and minimum device fields

TNBU's base schema includes model, model_display, version, required_version, mac,
serial, hostname, ip, inform_url, default, state, cfgversion, uptime and time.
Gateway uses state 0/default true before adoption and state 2/default false after;
TNBU simulator uses state 4 in its AP example. Exact US8 state semantics are unresolved.

The initial candidate is US8 (eight Ethernet ports), profile `us8-experimental`.
Reported firmware `4.3.28.11361` and required_version `5.4.0` are **candidate profile
values**, not locally observed hardware facts or proof of compatibility. Neither model
acceptance nor completeness of required switch fields is established. There is no
generic model-string override; add a separate validated profile for another model.

## 5. Switch telemetry

TNBU's `ExtendedInformBody` contains `port_table`; `Port_Table` gives field names/types:
port_idx, up, enable, speed, full_duplex, autoneg, rx_bytes, tx_bytes and many others.
The MVP emits that small source-supported subset with eight synthetic ports, port 1
up at 1000 Mb/s, other ports down, zero counters. It does not invent utilization,
PoE, temperature, client addresses or physical topology. Configurable telemetry is M1.

The TNBU schema is evidence of representation, not an actual US8 capture. Which
fields affect controller UI and whether extra capability/if_table fields are required
must be established with an authorized lab. No rejected field has yet been identified
against a real controller; no UI effect is claimed.

## 6. Provisioning, heartbeat, commands

Source-confirmed response `_type` values include `noop`, `setparam`, `cmd`, `reboot`,
`upgrade`, `setdefault`. `noop` can carry interval/immediate; setparam can carry
mgmt_cfg/system_cfg/cfgversion. We accept only authkey, cfgversion and use_aes_gcm
as local emulator protocol metadata. We deliberately ignore controller-selected
URLs, raw system configuration, SSH settings and executable commands.

`cfgversion` is echoed as protocol metadata; that is **not a claim that switch
configuration was applied**. If the controller requires real provisioning effects,
adoption may stall. Reboot, firmware upgrade and reset are ignored, not faked.
No RouterOS client exists in M0, so controller commands have no path to MikroTik.

Requests repeat after the bounded interval (1–300 seconds), after setparam in one
second, and with capped backoff on failures. A received private key means
`key-received`; a later valid noop under that key means `exchanging`. These labels
do not establish the controller's Adopted/Connected UI state.

## 7. Clients, VLANs and topology

TNBU `Port_Table.mac_table` entries expose mac, ip, hostname, vlan, age, static, uptime.
Its `lldp_table` describes chassis_id/chassis_descr, local_port_idx/local_port_name,
port_id/port_descr, is_wired. These are useful research leads, not implemented M0 fields.
Gateway ipv4_active_leases is **not** evidence of a switch wired-client schema.
How controller topology merges LLDP, FDB and other UniFi devices is unresolved.
Client visibility, VLAN UI and topology all require separate experiments after adoption.

## 8. Compatibility and next experiment

No source establishes this emulator's compatibility with 8.0.24 or newer releases.
Packet version, crypto mode, model profile and controller release must be recorded
independently. A different tag/model string is never evidence of compatibility.

Run the isolated 8.0.24 procedure in `adoption.md`. Record initial inform HTTP status,
whether a pending switch appears, setparam fields (names only), key transition,
controller Adopted/Connected state, actual eight-port rendering and reconnect after
restart. Leave it running at least five minutes and record timestamps/version/digest.

If adoption stalls, supply the exact controller version, safe CLI diagnostics and
sanitized controller errors. If needed, capture initial inform through the first
post-adoption exchanges for **a lab switch and controller you own**, recording model,
firmware, packet flags and field names/types. Keep raw captures/private keys offline;
they may contain credentials. Use fresh synthetic identities in public fixtures.
Never modify a production controller database or impersonate a physical device.
