import json
from copy import deepcopy
from pathlib import Path

import pytest

from mtunifi.config import Settings
from mtunifi.fleet import (
    InventoryError,
    check_existing_identities,
    environment,
    load_inventory,
)
from mtunifi.main import main
from mtunifi.storage import StateBindingError, Store


def manifest(tmp_path):
    return {
        "schema_version": 1,
        "devices": [
            {
                "id": name,
                "settings": {
                    "state_dir": str(tmp_path / name),
                    "hostname": name,
                    "device_ip": f"192.0.2.{i}",
                },
            }
            for i, name in enumerate(("one", "two"), 10)
        ],
    }


def write(tmp_path, data):
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(data))
    return path


def test_relative_paths_and_no_secret_reads_or_identity_creation(tmp_path, monkeypatch, capsys):
    data = manifest(tmp_path)
    data["devices"][0]["settings"]["state_dir"] = "state-one"
    data["devices"][0]["settings"]["synthetic_file"] = "not-read.json"
    path = write(tmp_path, data)
    inventory = load_inventory(path)
    assert inventory.devices[0].settings.state_dir == tmp_path / "state-one"
    assert inventory.devices[0].settings.synthetic_file == tmp_path / "not-read.json"
    monkeypatch.setattr("sys.argv", ["mtunifi", "validate-inventory", "--inventory", str(path)])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["valid"]
    assert not (tmp_path / "state-one").exists()


@pytest.mark.parametrize(
    "change",
    [
        "same-id",
        "same-ip",
        "same-hostname",
        "same-state",
        "nested-state",
        "symlink-state",
        "missing-state",
        "unknown-field",
        "password-inline",
        "same-target",
        "too-many-devices",
    ],
)
def test_reject_unsafe_or_ambiguous_inventories_without_echoing_input(tmp_path, change):
    data = manifest(tmp_path)
    first, second = data["devices"]
    a, b = first["settings"], second["settings"]
    if change == "same-id":
        second["id"] = first["id"]
    elif change == "same-ip":
        b["device_ip"] = a["device_ip"]
    elif change == "same-hostname":
        b["hostname"] = a["hostname"]
    elif change == "same-state":
        b["state_dir"] = a["state_dir"]
    elif change == "nested-state":
        b["state_dir"] = str(Path(a["state_dir"]) / "nested")
    elif change == "symlink-state":
        Path(a["state_dir"]).mkdir()
        alias = tmp_path / "alias"
        alias.symlink_to(a["state_dir"], target_is_directory=True)
        b["state_dir"] = str(alias)
    elif change == "missing-state":
        del b["state_dir"]
    elif change == "unknown-field":
        b["DO-NOT-ECHO"] = "DO-NOT-ECHO"
    elif change == "password-inline":
        b["routeros_password"] = "DO-NOT-ECHO"
    elif change == "same-target":
        for settings in (a, b):
            settings.update(
                {
                    "telemetry_source": "routeros",
                    "routeros_host": "192.0.2.249",
                    "routeros_user": "reader",
                    "routeros_map_file": "map.json",
                    "routeros_password_file": "secret",
                }
            )
    elif change == "too-many-devices":
        data["devices"] *= 17
    with pytest.raises(InventoryError) as exc:
        load_inventory(write(tmp_path, data))
    assert "DO-NOT-ECHO" not in str(exc.value)


def test_bounded_inventory_and_environment_isolation(tmp_path, monkeypatch):
    path = write(tmp_path, manifest(tmp_path))
    path.write_bytes(b"x" * (64 * 1024 + 1))
    with pytest.raises(InventoryError, match="size limit"):
        load_inventory(path)
    monkeypatch.setenv("MTUNIFI_ROUTEROS_PASSWORD_FILE", "DO-NOT-INHERIT")
    monkeypatch.setenv("MTUNIFI_UNKNOWN_SECRET", "DO-NOT-INHERIT")
    monkeypatch.setenv("MTUNIFI_PROFILE", "DO-NOT-INHERIT")
    env = environment(Settings())
    assert "MTUNIFI_UNKNOWN_SECRET" not in env
    assert "MTUNIFI_ROUTEROS_PASSWORD_FILE" not in env
    assert env["MTUNIFI_PROFILE"] == "us8-experimental"
    assert env["PATH"]


def test_copied_mac_detected_but_unrelated_corrupt_worker_is_isolated(tmp_path):
    inventory = load_inventory(write(tmp_path, manifest(tmp_path)))
    stores = [Store(d.settings.state_dir) for d in inventory.devices]
    for store in stores:
        with store.lock():
            store.load_or_create()
    stores[1].path.write_bytes(stores[0].path.read_bytes())
    with pytest.raises(InventoryError, match="Copied device identity"):
        check_existing_identities(inventory.devices)
    stores[1].path.write_text("DO-NOT-ECHO")
    check_existing_identities(inventory.devices)


def test_identity_profile_target_binding_and_legacy_us8_upgrade(tmp_path):
    store = Store(tmp_path)
    state = store.load_or_create()
    state.controller_url = "http://127.0.0.1:8080/inform"
    before = deepcopy(state)
    with pytest.raises(StateBindingError):
        state.bind("usw-pro24-experimental", "192.168.0.251")
    assert state == before
    state.bind("us8-experimental", "192.168.0.249")
    store.save(state)
    loaded = store.load()
    assert loaded.mac == state.mac and loaded.profile == "us8-experimental"
    loaded.bind("us8-experimental", "192.168.0.249")
    for name, target in (
        ("usw-pro24-experimental", "192.168.0.249"),
        ("us8-experimental", "192.168.0.251"),
        ("us8-experimental", None),
    ):
        with pytest.raises(StateBindingError):
            loaded.bind(name, target)
    loaded.reset_adoption()
    assert loaded.profile == "us8-experimental" and loaded.routeros_host == "192.168.0.249"


def test_reset_requires_one_target_and_never_creates_state(tmp_path, monkeypatch, capsys):
    path = write(tmp_path, manifest(tmp_path))
    monkeypatch.setattr("sys.argv", ["mtunifi", "reset-adoption", "--inventory", str(path)])
    assert main() == 2
    assert "Select exactly one device" in capsys.readouterr().err
    assert not (tmp_path / "one").exists()
