"""Independent RouterOS wire peer; no RouterOS host or credentials required."""

import asyncio
import ipaddress
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest_asyncio
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from mtunifi.config import Settings

PASSWORD = "fixture-read-only-password"


async def receive(reader):
    words = []
    while True:
        first = (await reader.readexactly(1))[0]
        if first == 0:
            return words
        if first & 0x80 == 0:
            size = first
        elif first & 0xC0 == 0x80:
            size = (first & 0x3F) * 256 + (await reader.readexactly(1))[0]
        elif first & 0xE0 == 0xC0:
            size = (first & 0x1F) * 65536 + int.from_bytes(await reader.readexactly(2), "big")
        else:
            raise AssertionError("Fixture does not expect large requests")
        words.append((await reader.readexactly(size)).decode())


def sentence(*words):
    result = b""
    for word in words:
        raw = word.encode()
        size = len(raw)
        prefix = bytes([size]) if size < 128 else bytes([0x80 + (size >> 8), size & 255])
        result += prefix + raw
    return result + b"\0"


class RouterOSPeer:
    def __init__(self, directory):
        self.directory = directory
        self.server = None
        self.tasks = set()
        self.commands = []
        self.logins = 0
        self.traps = {}
        self.drop = None
        self.stall = None
        self.version = "7.24.4 (stable)"
        self.uptime = "1d2h3m4s"
        self.rx = 9007199254740993
        self.ca = None
        self.monitors = {}

    def tables(self):
        physical = [f"ether{i}" for i in range(1, 6)] + ["sfp1"]
        interfaces = [
            {
                ".id": f"*{i}",
                "name": name,
                "type": "ether",
                "disabled": "true" if name == "ether5" else "false",
                "running": "true" if name in ("ether1", "ether2") else "false",
                "rx-byte": str(self.rx + i),
                "tx-byte": str(1000 + i),
            }
            for i, name in enumerate(physical, 1)
        ]
        interfaces += [
            {"name": "bridge-lan", "type": "bridge"},
            {"name": "vlan20", "type": "vlan"},
            {"name": "bond1", "type": "bonding"},
        ]
        return {
            "/system/resource/print": [
                {
                    "version": self.version,
                    "uptime": self.uptime,
                    "cpu-load": "23",
                    "total-memory": "268435456",
                    "free-memory": "134217728",
                    "board-name": "RB760iGS",
                    "architecture-name": "mmips",
                }
            ],
            "/system/identity/print": [{"name": "read-only-lab"}],
            "/interface/print": list(reversed(interfaces)),
            "/interface/ethernet/print": [
                {
                    ".id": f"*{i}",
                    "name": name,
                    "default-name": name,
                    "mac-address": f"DC:2C:6E:00:00:{i:02X}",
                }
                for i, name in enumerate(physical, 1)
            ],
            "/interface/vlan/print": [
                {"name": "vlan20", "interface": "bridge-lan", "vlan-id": "20"}
            ],
            "/interface/bonding/print": [{"name": "bond1", "slaves": "ether3,ether4"}],
            "/interface/bridge/port/print": [
                {"interface": "ether2", "bridge": "bridge-lan", "hw": "yes", "pvid": "20"}
            ],
            "/interface/bridge/print": [
                {
                    "name": "bridge-lan",
                    "vlan-filtering": "false",
                    "pvid": "1",
                    "mac-address": "DC:2C:6E:00:00:01",
                    "ageing-time": "5m",
                }
            ],
            "/interface/bridge/vlan/print": [],
            "/interface/bridge/host/print": [
                {
                    "mac-address": "00:11:22:33:44:55",
                    "on-interface": "ether2",
                    "bridge": "bridge-lan",
                    "dynamic": "true",
                    "local": "false",
                },
                {
                    "mac-address": "02:AB:00:00:00:02",
                    "on-interface": "ether2",
                    "bridge": "bridge-lan",
                    "dynamic": "false",
                    "external": "true",
                },
                {
                    "mac-address": "DC:2C:6E:00:00:01",
                    "on-interface": "bridge-lan",
                    "bridge": "bridge-lan",
                    "local": "true",
                },
                {
                    "mac-address": "DC:2C:6E:00:00:02",
                    "on-interface": "ether2",
                    "bridge": "bridge-lan",
                    "dynamic": "true",
                    "local": "false",
                },
            ],
            "/ip/arp/print": [
                {
                    "address": "192.0.2.50",
                    "mac-address": "00:11:22:33:44:55",
                    "interface": "bridge-lan",
                    "status": "reachable",
                }
            ],
            "/ip/dhcp-server/lease/print": [
                {
                    "address": "192.0.2.50",
                    "mac-address": "00:11:22:33:44:55",
                    "status": "bound",
                    "server": "lan",
                    "host-name": "fixture-client",
                }
            ],
            "/ip/dhcp-server/print": [
                {"name": "lan", "interface": "bridge-lan", "disabled": "false"}
            ],
            "/ip/neighbor/print": [],
        }

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            words = await receive(reader)
            assert words == ["/login", "=name=observer", "=password=" + PASSWORD]
            self.logins += 1
            self.commands.append(("/login",))  # Never keep a credential-bearing capture.
            if "/login" in self.traps:
                writer.write(sentence("!trap", "=message=" + self.traps["/login"]))
                writer.write(sentence("!done"))
                await writer.drain()
                return
            writer.write(sentence("!done"))
            await writer.drain()
            while True:
                words = await receive(reader)
                command = words[0]
                self.commands.append(tuple(words))
                if command == self.drop:
                    return
                if command == self.stall:
                    await asyncio.Event().wait()
                if command in self.traps:
                    writer.write(sentence("!trap", "=message=" + self.traps[command]))
                    writer.write(sentence("!done"))
                else:
                    if command == "/interface/ethernet/monitor":
                        assert "=once=" in words
                        interface_id = next(
                            w.split("=", 2)[2] for w in words if w.startswith("=numbers=")
                        )
                        rows = [
                            {
                                "status": "link-ok" if interface_id in ("*1", "*2") else "no-link",
                                "rate": "1Gbps" if interface_id == "*1" else "100Mbps",
                                "full-duplex": "yes",
                                "auto-negotiation": "done",
                            }
                        ]
                        rows = self.monitors.get(interface_id, rows)
                    else:
                        assert command in self.tables(), "Unexpected or mutating command"
                        rows = self.tables()[command]
                    if not rows:
                        writer.write(sentence("!empty"))
                    for row in rows:
                        writer.write(sentence("!re", *("=" + k + "=" + v for k, v in row.items())))
                    writer.write(sentence("!done"))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            try:
                async with asyncio.timeout(1):
                    await writer.wait_closed()
            except (ConnectionError, ssl.SSLError, TimeoutError):
                pass
            self.tasks.discard(task)

    async def start(self, tls=False):
        context = None
        if tls:
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fixture.local")])
            now = datetime.now(UTC)
            cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(subject)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=1))
                .not_valid_after(now + timedelta(days=1))
                .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                .add_extension(
                    x509.SubjectAlternativeName(
                        [
                            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                            x509.DNSName("localhost"),
                        ]
                    ),
                    critical=False,
                )
                .add_extension(
                    x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
                )
                .sign(key, hashes.SHA256())
            )
            certfile, keyfile = self.directory / "ca.pem", self.directory / "key.pem"
            certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            keyfile.write_bytes(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certfile, keyfile)
            self.ca = certfile
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0, ssl=context)
        return self

    def settings(self, **overrides):
        password = self.directory / "password"
        password.write_text(PASSWORD + "\n")
        mapping = Path(__file__).resolve().parents[1] / "examples" / ("routeros-rb760igs.json")
        return Settings(
            telemetry_source="routeros",
            routeros_host="127.0.0.1",
            routeros_port=self.server.sockets[0].getsockname()[1],
            routeros_tls=self.ca is not None,
            routeros_ca_file=self.ca,
            routeros_user="observer",
            routeros_password_file=password,
            routeros_map_file=mapping,
            **overrides,
        )

    async def close(self):
        self.server.close()
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.server.wait_closed()


@pytest_asyncio.fixture
async def routeros_peer(tmp_path):
    peers = []

    async def start(tls=False):
        directory = tmp_path / str(len(peers))
        directory.mkdir()
        peer = await RouterOSPeer(directory).start(tls)
        peers.append(peer)
        return peer

    yield start
    for peer in peers:
        await peer.close()
