import asyncio
import json
import os
import signal
import sys
from pathlib import Path

import pytest
from conftest import configure_crs326

from mtunifi.fleet import environment
from mtunifi.protocol import BOOTSTRAP_KEY, decode, encode
from mtunifi.routeros import RouterOSSource
from mtunifi.storage import Store
from mtunifi.telemetry import inform

MAP = Path(__file__).resolve().parents[2] / "examples/routeros-crs326.json"


@pytest.mark.parametrize("tls", [False, True])
async def test_crs326_profile_preserves_26_ports_clients_lldp_and_10g(routeros_peer, tmp_path, tls):
    peer = configure_crs326(await routeros_peer(tls=tls))
    settings = peer.settings(profile="usw-pro24-experimental")
    settings.routeros_map_file = MAP
    source = RouterOSSource(settings)
    snapshot = await source.collect(100, "02:00:00:00:00:99")
    store = Store(tmp_path)
    state = store.load_or_create()
    body = inform(settings, state, 0, snapshot=snapshot)
    assert body["model"] == "US24PRO2" and len(body["port_table"]) == 26
    p24, p25, p26 = body["port_table"][23:]
    assert p24["speed"] == 1000 and p25["speed"] == p26["speed"] == 10000
    assert p26["rx_bytes"] == peer.rx + 26 and p26["tx_bytes"] == 2026
    assert p24["mac_table"][0]["mac"] == "00:11:22:33:44:55"
    assert p26["mac_table"][0]["mac"] == "02:ab:00:00:00:02"
    assert body["lldp_table"][0]["local_port_idx"] == 26
    assert not any("exceeds-profile" in w for w in source.last_summary["observations"]["warnings"])


@pytest.mark.skipif(os.name != "posix", reason="POSIX signal/lock supervision integration")
async def test_real_processes_isolate_failed_start_crash_credentials_state_and_shutdown(
    routeros_peer, tmp_path
):
    rb = await routeros_peer()
    crs = configure_crs326(await routeros_peer(tls=True))
    crs.password = "second-independent-fixture-password"
    packets, keys, events = [], {}, []
    changed = asyncio.Event()
    failures = []
    key_by_model = {"US8": bytes.fromhex("11" * 16), "US24PRO2": bytes.fromhex("22" * 16)}

    async def wait_until(predicate):
        async with asyncio.timeout(20):
            while True:
                changed.clear()
                if predicate():
                    return
                await changed.wait()

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
            raw = await reader.readexactly(size)
            mac = ":".join(f"{b:02x}" for b in raw[8:14])
            key = keys.get(mac, BOOTSTRAP_KEY)
            packet = decode(raw, key)
            packets.append(packet)
            changed.set()
            if mac not in keys:
                keys[mac] = key_by_model[packet.body["model"]]
                body = {
                    "_type": "setparam",
                    "mgmt_cfg": f"authkey={keys[mac].hex()}\nuse_aes_gcm=1",
                }
            else:
                body = {"_type": "noop", "interval": 1}
            response = encode(body, mac, key, mode=packet.mode)
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Length: "
                + str(len(response)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + response
            )
            await writer.drain()
        except Exception as exc:
            failures.append(type(exc).__name__)
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(controller, "127.0.0.1", 0)
    url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/inform"
    missing_map = tmp_path / "late-map.json"
    devices = []
    for i, (name, peer) in enumerate((("rb", rb), ("crs", crs)), 10):
        settings = peer.settings(
            hostname=name,
            state_dir=tmp_path / name,
            device_ip=f"192.0.2.{i}",
            inform_url=url,
            interval=1,
        )
        if name == "crs":
            settings.profile = "usw-pro24-experimental"
            settings.routeros_map_file = missing_map
        devices.append({"id": name, "settings": settings.model_dump(mode="json")})
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps({"schema_version": 1, "devices": devices}))
    # Unrelated inherited MTUNIFI configuration must not leak to either child.
    env = environment(rb.settings())
    env["MTUNIFI_STATE_DIR"] = str(tmp_path / "must-not-exist")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "mtunifi.main",
        "run",
        "--inventory",
        str(path),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def read_events():
        while line := await process.stderr.readline():
            events.append(json.loads(line))
            changed.set()

    reader = asyncio.create_task(read_events())
    try:
        await wait_until(
            lambda: (
                len(packets) >= 3
                and any(
                    e.get("event") == "worker_retry" and e.get("device_id") == "crs" for e in events
                )
            )
        )
        assert {p.body["model"] for p in packets} == {"US8"}
        assert not (tmp_path / "crs/identity.json").exists()
        missing_map.write_bytes(MAP.read_bytes())
        await wait_until(
            lambda: any(p.mode == "gcm" and p.body["model"] == "US24PRO2" for p in packets)
        )
        rb_state, crs_state = (Store(tmp_path / name).load() for name in ("rb", "crs"))
        assert rb_state.mac != crs_state.mac
        assert rb_state.authkey.get_secret_value() != crs_state.authkey.get_secret_value()
        crs_pid = next(
            e["pid"]
            for e in reversed(events)
            if e.get("event") == "worker_started" and e["device_id"] == "crs"
        )
        count_before = sum(p.body["model"] == "US8" for p in packets)
        os.kill(crs_pid, signal.SIGKILL)
        await wait_until(
            lambda: any(
                e.get("event") == "worker_started"
                and e.get("device_id") == "crs"
                and e["pid"] != crs_pid
                for e in events
            )
        )
        await wait_until(
            lambda: len([p for p in packets if p.mac == crs_state.mac and p.mode == "gcm"]) >= 2
        )
        assert sum(p.body["model"] == "US8" for p in packets) > count_before + 2
        after = Store(tmp_path / "crs").load()
        assert after.mac == crs_state.mac and after.authkey == crs_state.authkey
        assert rb.logins >= 1 and crs.logins >= 1
        assert not (tmp_path / "must-not-exist").exists()
        assert not failures
        for forbidden in (rb.password, crs.password, *[key.hex() for key in key_by_model.values()]):
            assert forbidden not in json.dumps(events)
        process.send_signal(signal.SIGTERM)
        await asyncio.wait_for(process.wait(), 10)
        await reader
        one_shot = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "mtunifi.main",
            "run",
            "--inventory",
            str(path),
            "--once",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(one_shot.communicate(), 10)
        assert one_shot.returncode == 0 and not out
        assert Store(tmp_path / "rb").load().mac == rb_state.mac
        assert Store(tmp_path / "crs").load().authkey == crs_state.authkey
        assert all(p.mode == "gcm" for p in packets[-2:])
    finally:
        if process.returncode is None:
            process.send_signal(signal.SIGTERM)
        await asyncio.wait_for(process.wait(), 10)
        await reader
        server.close()
        await server.wait_closed()
    assert process.returncode == 0
    for name in ("rb", "crs"):
        with Store(tmp_path / name).lock():
            pass
    assert not await process.stdout.read()


async def test_fleet_diagnose_health_and_selected_init_are_read_only(tmp_path):
    devices = [
        {
            "id": name,
            "settings": {
                "hostname": name,
                "device_ip": f"192.0.2.{i}",
                "state_dir": str(tmp_path / name),
            },
        }
        for i, name in enumerate(("one", "two"), 10)
    ]
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps({"devices": devices}))

    async def call(command, *args):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "mtunifi.main",
            command,
            "--inventory",
            str(path),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(process.communicate(), 10)
        return process.returncode, json.loads(out) if out else None, err

    code, data, _ = await call("init", "--device", "two")
    assert code == 0 and data["devices"][0]["device_id"] == "two"
    assert not (tmp_path / "one").exists()
    assert (tmp_path / "two/identity.json").exists()
    code, data, _ = await call("diagnose")
    assert code == 1 and [r["exit_code"] for r in data["devices"]] == [2, 0]
    assert not (tmp_path / "one").exists()
    before = (tmp_path / "two/identity.json").read_bytes()
    code, data, _ = await call("health")
    assert code == 1 and [r["exit_code"] for r in data["devices"]] == [2, 1]
    assert (tmp_path / "two/identity.json").read_bytes() == before
