"""Opt-in Linux container check against local RouterOS and UniFi wire fixtures."""

import asyncio
import json
import os
import uuid
from pathlib import Path

import pytest
from conftest import configure_crs326

from mtunifi.protocol import BOOTSTRAP_KEY, decode, encode

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


@pytest.mark.skipif(
    os.environ.get("MTUNIFI_ALLOW_DOCKER_TEST") != "yes",
    reason="Explicit Docker smoke opt-in and a built image required",
)
@pytest.mark.parametrize(
    "tls,wide,gateway",
    [
        (False, False, False),
        (True, False, False),
        (True, True, False),
        (True, False, True),
    ],
)
async def test_rootless_container_routeros_source_and_gcm_restart(
    routeros_peer, tls, wide, gateway
):
    image = os.environ.get("MTUNIFI_DOCKER_IMAGE", "mtunifi:dev")
    peer = await routeros_peer(tls=tls)
    if wide:
        configure_crs326(peer)
    port_count, observed_count = (26, 26) if wide else (8, 6)
    if gateway:
        observed_count = 0
    neighbor_port = 26 if wide else 2
    original = peer.tables

    def tables():
        result = original()
        result["/ip/neighbor/print"] = [
            {
                "interface": "sfp-sfpplus2" if wide else "ether2",
                "mac-address": "00:11:22:33:44:99",
                "interface-name": "Gi1/0/8",
                "discovered-by": "lldp",
                "identity": "upstream",
            }
        ]
        return result

    peer.tables = tables
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
        "MTUNIFI_PROFILE": "usw-pro24-experimental" if wide else "us8-experimental",
        "MTUNIFI_TELEMETRY_SOURCE": "routeros",
        "MTUNIFI_ROUTEROS_HOST": "127.0.0.1",
        "MTUNIFI_ROUTEROS_PORT": str(settings.routeros_port),
        "MTUNIFI_ROUTEROS_TLS": str(tls),
        "MTUNIFI_ROUTEROS_USER": "observer",
        "MTUNIFI_ROUTEROS_PASSWORD_FILE": "/run/password",
        "MTUNIFI_ROUTEROS_MAP_FILE": "/scenarios/routeros-crs326.json"
        if wide
        else "/scenarios/routeros-rb760igs.json",
        "MTUNIFI_INFORM_URL": f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/inform",
    }
    if gateway:
        variables["MTUNIFI_PROFILE"] = "ugw3-experimental"
        variables["MTUNIFI_ROUTEROS_MAP_FILE"] = ""
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
        assert report["discovered_ports"] == observed_count and report["synthetic_ports"] == 0
        assert packets[1].mode == "gcm" and packets[0].mac == packets[1].mac
        if gateway:
            assert report["model"] == "UGW3" and report["wan_interface"] == "ether1"
            assert report["wired_clients"] is None and report["neighbors"] is None
            assert "port_table" not in packets[1].body
            assert packets[1].body["if_table"][0]["rx_bytes"] == peer.rx + 1
            assert packets[1].body["if_table"][0]["ip"] == "198.51.100.2"
            assert packets[1].body["if_table"][0]["mac"] == packets[1].mac
        else:
            assert report["wired_clients"] == 2
            assert report["routeros_telemetry"]["neighbors"] == 1
            assert (
                report["neighbors"] == 1 and report["neighbors_source"] == "routeros-neighbor-cache"
            )
            (neighbor,) = packets[1].body["lldp_table"]
            assert neighbor["chassis_id"] == "00:11:22:33:44:99"
            assert neighbor["local_port_idx"] == neighbor_port and neighbor["port_id"] == "Gi1/0/8"
            assert packets[1].body["port_table"][0]["rx_bytes"] == peer.rx + 1
            assert len(packets[1].body["port_table"]) == port_count
            if wide:
                assert packets[1].body["model"] == "US24PRO2"
                assert packets[1].body["port_table"][25]["speed"] == 10000
            wired = {c["mac"]: c for p in packets[1].body["port_table"] for c in p["mac_table"]}
            assert wired["00:11:22:33:44:55"]["ip"] == "192.0.2.50"
            assert "ip" not in wired["02:ab:00:00:00:02"]
            assert "vlan" not in wired["02:ab:00:00:00:02"]
        await docker(*args, image, "health")
        assert (
            json.loads(await docker(*args, image, "probe-routeros"))["mapped_physical_ports"]
            == observed_count
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
