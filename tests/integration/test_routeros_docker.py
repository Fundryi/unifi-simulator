"""Opt-in Linux container check against local RouterOS and UniFi wire fixtures."""

import asyncio
import json
import os
import uuid
from pathlib import Path

import pytest

from mtunifi.protocol import BOOTSTRAP_KEY, decode, encode

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


@pytest.mark.skipif(
    os.environ.get("MTUNIFI_ALLOW_DOCKER_TEST") != "yes",
    reason="Explicit Docker smoke opt-in and a built image required",
)
@pytest.mark.parametrize("tls", [False, True])
async def test_rootless_container_routeros_source_and_gcm_restart(routeros_peer, tls):
    image = os.environ.get("MTUNIFI_DOCKER_IMAGE", "mtunifi:dev")
    peer = await routeros_peer(tls=tls)
    settings = peer.settings()
    settings.routeros_password_file.chmod(0o444)
    volume = "mtunifi-m2-smoke-" + uuid.uuid4().hex
    key = bytes.fromhex("1234567890abcdef1234567890abcdef")
    packets = []

    async def controller(reader, writer):
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            size = int(
                next(
                    line.split(b":", 1)[1]
                    for line in header.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
            )
            active_key = BOOTSTRAP_KEY if not packets else key
            packet = decode(await reader.readexactly(size), active_key)
            packets.append(packet)
            body = (
                {"_type": "setparam", "mgmt_cfg": f"authkey={key.hex()}\nuse_aes_gcm=1"}
                if len(packets) == 1
                else {"_type": "noop"}
            )
            raw = encode(body, packet.mac, active_key, mode=packet.mode)
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: "
                + str(len(raw)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + raw
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def docker(*args, expected_status=0):
        process = await asyncio.create_subprocess_exec(
            "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await asyncio.wait_for(process.communicate(), 30)
        assert process.returncode == expected_status, err.decode()
        return out.decode() if expected_status == 0 else err.decode()

    server = await asyncio.start_server(controller, "127.0.0.1", 0)
    args = [
        "run",
        "--rm",
        "--network",
        "host",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "-v",
        volume + ":/state",
        "--mount",
        f"type=bind,src={settings.routeros_password_file},dst=/run/password,readonly",
        "-v",
        f"{EXAMPLES}:/scenarios:ro",
    ]
    variables = {
        "MTUNIFI_TELEMETRY_SOURCE": "routeros",
        "MTUNIFI_ROUTEROS_HOST": "127.0.0.1",
        "MTUNIFI_ROUTEROS_PORT": str(settings.routeros_port),
        "MTUNIFI_ROUTEROS_TLS": str(tls),
        "MTUNIFI_ROUTEROS_USER": "observer",
        "MTUNIFI_ROUTEROS_PASSWORD_FILE": "/run/password",
        "MTUNIFI_ROUTEROS_MAP_FILE": "/scenarios/routeros-rb760igs.json",
        "MTUNIFI_INFORM_URL": f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/inform",
    }
    if tls:
        peer.ca.chmod(0o644)
        args += ["--mount", f"type=bind,src={peer.ca},dst=/run/ca.pem,readonly"]
        variables["MTUNIFI_ROUTEROS_CA_FILE"] = "/run/ca.pem"
    for name, value in variables.items():
        args += ["-e", name + "=" + value]
    await docker("volume", "create", volume)
    try:
        await docker(*args, image, "run", "--once")
        await docker(*args, image, "run", "--once")
        report = json.loads(await docker(*args, image, "diagnose"))
        assert report["phase"] == "exchanging" and report["has_private_key"]
        assert report["discovered_ports"] == 6 and report["synthetic_ports"] == 0
        assert report["wired_clients"] is None
        assert packets[1].mode == "gcm" and packets[0].mac == packets[1].mac
        assert packets[1].body["port_table"][0]["rx_bytes"] == peer.rx + 1
        await docker(*args, image, "health")
        assert (
            json.loads(await docker(*args, image, "probe-routeros"))["mapped_physical_ports"] == 6
        )
        assert (await docker(*args, "--entrypoint", "id", image, "-u")).strip() == "10001"
        peer.server.close()
        await peer.server.wait_closed()
        error = json.loads(await docker(*args, image, "probe-routeros", expected_status=2))
        assert error["reason"] == "RouterOS connection-refused"
        assert error["stage"] == ("connect-tls" if tls else "connect")
        assert error["routeros_target"]["port"] == settings.routeros_port
    finally:
        server.close()
        await server.wait_closed()
        await docker("volume", "rm", volume)
