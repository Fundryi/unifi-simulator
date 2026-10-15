import asyncio
import json
import logging
import time

import httpx
from pydantic import SecretStr

from mtunifi.config import Settings
from mtunifi.protocol import BOOTSTRAP_KEY, MAX_PAYLOAD, ProtocolError, decode, encode
from mtunifi.storage import State, Store
from mtunifi.telemetry import inform

logger = logging.getLogger("mtunifi")


def event(name: str, **fields):
    logger.info(json.dumps({"event": name, **fields}, separators=(",", ":")))


def apply_response(state: State, response: dict) -> float | None:
    """Allowlisted emulator metadata only. Never executes commands or RouterOS writes."""
    kind = response.get("_type")
    known = (
        kind
        if kind in ("noop", "setparam", "cmd", "reboot", "upgrade", "setdefault")
        else "unknown"
    )
    event("controller_response", type=known)
    if kind == "setparam":
        mgmt = response.get("mgmt_cfg", "")
        if not isinstance(mgmt, str):
            raise ProtocolError("Invalid management configuration")
        values = dict(line.split("=", 1) for line in mgmt.splitlines() if "=" in line)
        # Validate all accepted fields before mutating state.
        new_key = values.get("authkey")
        if new_key is not None:
            try:
                if len(new_key) != 32 or len(bytes.fromhex(new_key)) != 16:
                    raise ValueError
            except ValueError:
                raise ProtocolError("Invalid adoption key in response") from None
            if bytes.fromhex(new_key) == BOOTSTRAP_KEY:
                raise ProtocolError("Controller supplied bootstrap key as private key")
        cfg = response.get("cfgversion", values.get("cfgversion", state.cfgversion))
        if not isinstance(cfg, str) or len(cfg) > 256:
            raise ProtocolError("Invalid configuration version")
        mode = values.get("use_aes_gcm")
        if mode not in (None, "true", "false", "1", "0"):
            raise ProtocolError("Invalid encryption negotiation")
        if new_key is not None:
            state.authkey = SecretStr(new_key)
            state.phase = "key-received"
        state.cfgversion = cfg
        if mode is not None:
            state.encryption = "gcm" if mode in ("true", "1") else "cbc"
        # system_cfg is deliberately not persisted, applied or logged; it can contain credentials.
        event(
            "provisioning_metadata",
            private_key_received=new_key is not None,
            system_configuration_ignored="system_cfg" in response,
        )
        return 1
    if kind == "noop":
        if state.authkey is not None:
            state.phase = "exchanging"  # Wire exchange only; controller UI adoption unproven.
        delay = response.get("interval")
        if response.get("immediate") == 1:
            return 1
        if isinstance(delay, (float, int)) and not isinstance(delay, bool):
            if 1 <= delay <= 300:
                return float(delay)
        return None
    event("unsupported_message", action="ignored")
    return None


class Emulator:
    def __init__(self, settings: Settings, store: Store, state: State):
        self.settings, self.store, self.state = settings, store, state
        self.started = time.monotonic()
        self.delay = settings.interval

    async def exchange(self, client: httpx.AsyncClient) -> bool:
        state = self.state
        if state.controller_url not in (None, self.settings.inform_url):
            raise RuntimeError(
                "State belongs to another controller; use a separate state directory"
            )
        state.controller_url = self.settings.inform_url
        key = bytes.fromhex(state.authkey.get_secret_value()) if state.authkey else BOOTSTRAP_KEY
        mode = state.encryption if state.authkey else self.settings.encryption
        body = inform(self.settings, state, int(time.monotonic() - self.started))
        state.last_attempt = state.last_telemetry = time.time()
        event("telemetry_generated", ports=len(body["port_table"]), source="synthetic")
        packet = encode(body, state.mac, key, mode=mode, version=self.settings.packet_version)
        event("inform_request", bytes=len(packet), phase=state.phase, encryption=mode)
        previous_phase = state.phase
        try:
            async with client.stream(
                "POST",
                self.settings.inform_url,
                content=packet,
                headers={
                    "Content-Type": "application/x-binary",
                    "User-Agent": "AirControl Agent v1.0",
                    "Accept-Encoding": "identity",
                },
            ) as response:
                if response.status_code != 200:
                    raise ProtocolError(f"Controller HTTP {response.status_code}")
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise ProtocolError("Unsupported HTTP content encoding")
                raw = bytearray()
                async for chunk in response.aiter_raw():
                    raw.extend(chunk)
                    if len(raw) > MAX_PAYLOAD + 40:
                        raise ProtocolError("Controller response too large")
            decoded = decode(bytes(raw), key)
            if decoded.mac != state.mac:
                raise ProtocolError("Response identity mismatch")
            if mode == "gcm" and decoded.mode != "gcm":
                raise ProtocolError("Encryption downgrade rejected")
            if self.settings.trace:
                # Trace metadata only; never untrusted strings, bodies, keys or configuration.
                event(
                    "protocol_trace",
                    version=decoded.version,
                    encryption=decoded.mode,
                    bytes=len(raw),
                    field_count=len(decoded.body),
                )
            delay = apply_response(state, decoded.body)
            self.delay = delay if delay is not None else self.settings.interval
            state.last_inform = time.time()
            state.last_error = None
            event("inform_success", phase=state.phase)
        except (httpx.HTTPError, ProtocolError) as exc:
            # Exception strings from HTTP libraries can include URLs or credentials: never log them.
            state.last_error = str(exc) if isinstance(exc, ProtocolError) else type(exc).__name__
            self.delay = min(60, max(self.settings.interval, self.delay * 2))
            event("inform_failure", reason=state.last_error, retry_seconds=self.delay)
            self.store.save(state)
            return False
        if state.phase != previous_phase:
            event("state_transition", previous=previous_phase, current=state.phase)
        self.store.save(state)
        return True

    async def run(self, once: bool = False) -> bool:
        async with httpx.AsyncClient(
            timeout=self.settings.timeout, follow_redirects=False, trust_env=False
        ) as client:
            while True:
                success = await self.exchange(client)
                if once:
                    return success
                await asyncio.sleep(self.delay)
