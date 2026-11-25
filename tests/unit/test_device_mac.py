import pytest

from mtunifi.config import Settings
from mtunifi.storage import State, Store


def test_explicit_device_mac_is_used_for_a_new_identity_only(tmp_path):
    store = Store(tmp_path)
    first = store.load_or_create("02:00:00:00:00:29".lower())
    assert first.mac == "02:00:00:00:00:29"
    assert store.load_or_create("02:00:00:00:00:02").mac == "02:00:00:00:00:29"


def test_generated_identity_stays_locally_administered(tmp_path):
    mac = Store(tmp_path).load_or_create().mac
    assert int(mac[:2], 16) & 3 == 2


def test_multicast_identity_rejected():
    with pytest.raises(ValueError):
        State(mac="01:00:5e:00:00:01")
    with pytest.raises(ValueError):
        Settings(device_mac="not-a-mac")
    assert Settings(device_mac="02:00:00:00:00:29").device_mac == "02:00:00:00:00:29"
