"""Bounded RouterOS 7 API transport. Only the commands below can reach the wire."""

import asyncio
import ssl
from enum import StrEnum
from pathlib import Path

from mtunifi.config import Settings

MAX_WORD = 64 * 1024
MAX_REPLY = 4 * 1024 * 1024
MAX_ROWS = 4096


class RouterOSError(ValueError):
    """Fixed safe codes only; never include server messages or credentials."""

    def __init__(self, code: str):
        self.code = code
        super().__init__("RouterOS " + code)


class Table(StrEnum):
    RESOURCE = "/system/resource/print"
    IDENTITY = "/system/identity/print"
    INTERFACES = "/interface/print"
    ETHERNET = "/interface/ethernet/print"
    BRIDGE_PORTS = "/interface/bridge/port/print"
    VLANS = "/interface/vlan/print"
    BONDS = "/interface/bonding/print"


PROPERTIES = {
    Table.RESOURCE: "version,uptime,cpu-load,total-memory,free-memory,board-name,architecture-name",
    Table.IDENTITY: "name",
    Table.INTERFACES: ".id,name,type,disabled,running,rx-byte,tx-byte",
    Table.ETHERNET: ".id,name,default-name,mac-address,disabled,running",
    Table.BRIDGE_PORTS: "interface,bridge,hw,hw-offload,disabled,pvid",
    Table.VLANS: "name,interface,vlan-id",
    Table.BONDS: "name,slaves,mode",
}
MONITOR = "/interface/ethernet/monitor"


def length_prefix(size: int) -> bytes:
    if not 0 <= size <= MAX_WORD:
        raise RouterOSError("word-too-large")
    if size < 0x80:
        return bytes([size])
    if size < 0x4000:
        return (size | 0x8000).to_bytes(2, "big")
    return (size | 0xC00000).to_bytes(3, "big")


async def read_word(reader: asyncio.StreamReader) -> bytes:
    first = (await reader.readexactly(1))[0]
    if first < 0x80:
        size = first
    elif first < 0xC0:
        size = ((first & 0x3F) << 8) | int.from_bytes(await reader.readexactly(1), "big")
    elif first < 0xE0:
        size = ((first & 0x1F) << 16) | int.from_bytes(await reader.readexactly(2), "big")
    elif first < 0xF0:
        size = ((first & 0x0F) << 24) | int.from_bytes(await reader.readexactly(3), "big")
    elif first == 0xF0:
        size = int.from_bytes(await reader.readexactly(4), "big")
    else:
        raise RouterOSError("invalid-length")
    if size > MAX_WORD:
        raise RouterOSError("word-too-large")
    return await reader.readexactly(size)


def read_password(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            raw = handle.read(4097)
        # Permit one final newline from a secret file, preserving spaces in passwords.
        if raw.endswith(b"\r\n"):
            raw = raw[:-2]
        elif raw.endswith(b"\n"):
            raw = raw[:-1]
        if not raw or len(raw) > 4096 or any(c in raw for c in (b"\n", b"\r", b"\0")):
            raise ValueError
        return raw.decode("utf-8")
    except (OSError, ValueError):
        raise RouterOSError("password-file-invalid") from None


class RouterOSAPI:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None

    async def __aenter__(self):
        try:
            context = None
            if self.settings.routeros_tls:
                context = ssl.create_default_context(cafile=self.settings.routeros_ca_file)
                context.minimum_version = ssl.TLSVersion.TLSv1_2
            self.reader, self.writer = await asyncio.open_connection(
                self.settings.routeros_host,
                self.settings.routeros_port,
                ssl=context,
                server_hostname=self.settings.routeros_host if context else None,
                limit=MAX_WORD + 1,
            )
            password = read_password(self.settings.routeros_password_file)
            await self._execute(
                "/login", ("=name=" + self.settings.routeros_user, "=password=" + password)
            )
            return self
        except BaseException:
            await self.close()
            raise

    async def __aexit__(self, *_):
        await self.close()

    async def close(self):
        if self.writer is not None:
            self.writer.close()
            try:
                async with asyncio.timeout(1):
                    await self.writer.wait_closed()
            except (OSError, TimeoutError):
                pass
            self.writer = None

    async def table(self, table: Table) -> list[dict[str, str]]:
        if not isinstance(table, Table):
            raise RouterOSError("command-not-allowed")
        words = ["=.proplist=" + PROPERTIES[table]]
        if table == Table.INTERFACES:
            words.append("=stats=")
        return await self._execute(table.value, tuple(words))

    async def monitor(self, interface_id: str) -> list[dict[str, str]]:
        if not interface_id or len(interface_id) > 128 or any(ord(c) < 32 for c in interface_id):
            raise RouterOSError("invalid-interface-id")
        return await self._execute(MONITOR, ("=numbers=" + interface_id, "=once="))

    async def _execute(self, command: str, words: tuple[str, ...]) -> list[dict[str, str]]:
        # Defense in depth: even internal callers cannot send writes, scripts or arbitrary paths.
        if command not in {"/login", MONITOR, *(t.value for t in Table)}:
            raise RouterOSError("command-not-allowed")
        if self.writer is None or self.reader is None:
            raise RouterOSError("not-connected")
        encoded = [word.encode("utf-8") for word in (command, *words)]
        self.writer.write(b"".join(length_prefix(len(w)) + w for w in encoded) + b"\0")
        await self.writer.drain()
        rows = []
        total = 0
        error = None
        for _ in range(MAX_ROWS + 32):
            sentence = []
            for _ in range(128):
                raw = await read_word(self.reader)
                total += len(raw) + 5
                if total > MAX_REPLY:
                    raise RouterOSError("reply-too-large")
                if not raw:
                    break
                try:
                    sentence.append(raw.decode("utf-8"))
                except UnicodeDecodeError:
                    raise RouterOSError("invalid-encoding") from None
            else:
                raise RouterOSError("sentence-too-large")
            if not sentence:
                raise RouterOSError("empty-reply")
            fields = {}
            for word in sentence[1:]:
                if not word.startswith("=") or "=" not in word[1:]:
                    raise RouterOSError("invalid-reply")
                key, value = word[1:].split("=", 1)
                if key in fields:
                    raise RouterOSError("duplicate-field")
                fields[key] = value
            kind = sentence[0]
            if kind == "!done":
                if error:
                    raise RouterOSError(error)
                if command == "/login" and "ret" in fields:
                    raise RouterOSError("legacy-login-unsupported")
                return rows
            if kind == "!re":
                rows.append(fields)
                if len(rows) > MAX_ROWS:
                    raise RouterOSError("too-many-rows")
            elif kind in ("!trap", "!fatal"):
                # Classify privately; never put the untrusted server text in exceptions/logs.
                message = fields.get("message", "").lower()
                if command == "/login":
                    error = "authentication-failed"
                elif "permission" in message:
                    error = "permission-denied"
                elif "no such command" in message or "not supported" in message:
                    error = "endpoint-unavailable"
                else:
                    error = "request-rejected"
                if kind == "!fatal":
                    raise RouterOSError(error)
            elif kind != "!empty":  # RouterOS 7.18+ can emit !empty before !done.
                raise RouterOSError("unknown-reply")
        raise RouterOSError("too-many-sentences")
