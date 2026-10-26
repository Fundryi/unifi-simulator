import pytest

from mtunifi.config import Settings
from mtunifi.storage import State
from mtunifi.telemetry import inform


def test_firmware_version_overrides_profile_version():
    settings = Settings(firmware_version="7.5.15.17146")
    body = inform(settings, State(mac="02:00:00:00:00:01"), 1)
    assert body["version"] == "7.5.15.17146"
    assert body["required_version"] == "5.4.0"


def test_firmware_version_defaults_to_profile():
    body = inform(Settings(), State(mac="02:00:00:00:00:01"), 1)
    assert body["version"] == "4.3.28.11361"


def test_firmware_version_rejects_garbage():
    with pytest.raises(ValueError):
        Settings(firmware_version="7.5.15; rm -rf /")
