import json

import httpx
from pydantic import SecretStr

from mtunifi.config import Settings
from mtunifi.emulator import Emulator
from mtunifi.protocol import BOOTSTRAP_KEY, decode, encode
from mtunifi.storage import Store

KEY = bytes.fromhex("1234567890abcdef1234567890abcdef")


async def test_http_400_never_clears_key_and_explicit_reset_resumes_discovery(tmp_path, caplog):
    store = Store(tmp_path)
    state = store.load_or_create()
    state.authkey = SecretStr(KEY.hex())
    state.phase = "exchanging"
    state.encryption = "gcm"
    state.controller_url = Settings().inform_url
    store.save(state)
    app = Emulator(Settings(), store, state)
    received = []

    def rejecting_controller(request):
        assert request.method == "POST" and request.url.path == "/inform"
        packet = decode(request.content, KEY)
        assert packet.mode == "gcm" and packet.body["default"] is False
        received.append(packet.mac)
        return httpx.Response(400)

    with caplog.at_level("INFO"):
        async with httpx.AsyncClient(transport=httpx.MockTransport(rejecting_controller)) as client:
            assert not await app.exchange(client)
            assert not await app.exchange(client)
    assert store.load().authkey.get_secret_value() == KEY.hex()
    assert caplog.text.count('"event":"adoption_recovery_required"') == 1
    requests = [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "mtunifi" and json.loads(record.message)["event"] == "inform_request"
    ]
    assert requests[0]["controller_url"] == Settings().inform_url
    assert requests[0]["has_private_key"] is True and requests[0]["default"] is False
    assert KEY.hex() not in caplog.text
    mac = state.mac
    state.reset_adoption()
    store.save(state)
    app = Emulator(Settings(), store, store.load())

    def discovery_controller(request):
        packet = decode(request.content, BOOTSTRAP_KEY)
        assert packet.mac == mac
        assert packet.mode == "cbc" and packet.body["default"] is True
        assert packet.body["state"] == 0 and packet.body["cfgversion"] == ""
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(discovery_controller)) as client:
        assert not await app.exchange(client)  # 404 can accompany successful pending discovery.
    assert store.load().phase == "factory" and store.load().mac == mac


async def test_valid_private_reset_is_persisted_but_wrong_key_cannot_reset(tmp_path):
    store = Store(tmp_path)
    state = store.load_or_create()
    state.authkey = SecretStr(KEY.hex())
    state.encryption = "gcm"
    app = Emulator(Settings(), store, state)
    for response_key, expected in ((BOOTSTRAP_KEY, False), (KEY, True)):
        response = encode({"_type": "setdefault"}, state.mac, response_key, mode="gcm")
        transport = httpx.MockTransport(
            lambda _, payload=response: httpx.Response(200, stream=httpx.ByteStream(payload))
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await app.exchange(client) is expected
        assert (store.load().authkey is None) is expected


def test_reset_command_preserves_identity_and_telemetry_and_needs_exclusive_lock(
    tmp_path, monkeypatch, capsys
):
    from mtunifi.main import main

    store = Store(tmp_path)
    state = store.load_or_create()
    state.authkey = SecretStr(KEY.hex())
    state.phase = "exchanging"
    state.last_inform = 1234
    store.save(state)
    store.save_telemetry({"active_clients": 3, "source": "synthetic"})
    monkeypatch.setenv("MTUNIFI_STATE_DIR", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["mtunifi", "reset-adoption"])
    with store.lock():
        assert main() == 2
    assert store.load().authkey is not None
    assert main() == 0
    output = capsys.readouterr()
    assert KEY.hex() not in output.out + output.err
    assert json.loads(output.out)["mac"] == state.mac
    restored = store.load()
    assert restored.mac == state.mac and restored.authkey is None and restored.last_inform is None
    assert store.load_telemetry()["active_clients"] == 3
    assert main() == 0  # Repeating an explicit reset is safe and preserves the identity.


def test_reset_missing_state_does_not_create_identity(tmp_path, monkeypatch):
    from mtunifi.main import main

    monkeypatch.setenv("MTUNIFI_STATE_DIR", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["mtunifi", "reset-adoption"])
    assert main() == 2
    assert not (tmp_path / "identity.json").exists()


def test_reset_refuses_state_bound_to_another_controller(tmp_path, monkeypatch):
    from mtunifi.main import main

    store = Store(tmp_path)
    state = store.load_or_create()
    state.authkey = SecretStr(KEY.hex())
    state.controller_url = "http://another-controller:8080/inform"
    store.save(state)
    before = store.path.read_bytes()
    monkeypatch.setenv("MTUNIFI_STATE_DIR", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["mtunifi", "reset-adoption"])
    assert main() == 2
    assert store.path.read_bytes() == before
