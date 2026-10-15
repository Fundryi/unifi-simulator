import asyncio
import errno
import socket
import ssl

import pytest
from pydantic import ValidationError

from mtunifi.config import Settings
from mtunifi.routeros import (
    RouterOSMap,
    check_version,
    link_speed,
    load_mapping,
    uptime_seconds,
)
from mtunifi.routeros_api import (
    MAX_WORD,
    RouterOSAPI,
    RouterOSError,
    api_error,
    length_prefix,
    read_password,
    read_word,
)


@pytest.mark.parametrize(
    "value, expected",
    [
        (0, b"\0"),
        (127, b"\x7f"),
        (128, b"\x80\x80"),
        (16383, b"\xbf\xff"),
        (16384, b"\xc0\x40\x00"),
        (65536, b"\xc1\x00\x00"),
    ],
)
async def test_framing_known_vectors_and_fragmented_input(value, expected):
    assert length_prefix(value) == expected
    reader = asyncio.StreamReader()

    async def fragment():
        for chunk in (expected[:1], expected[1:], b"a" * value):
            reader.feed_data(chunk)
            await asyncio.sleep(0)

    sender = asyncio.create_task(fragment())
    assert await read_word(reader) == b"a" * value
    await sender


@pytest.mark.parametrize("prefix", [b"\xf1", b"\xe0\x01\x00\x01", b"\xf0\x00\x01\x00\x01"])
async def test_large_or_reserved_lengths_rejected_before_reading_body(prefix):
    reader = asyncio.StreamReader()
    reader.feed_data(prefix)
    with pytest.raises(RouterOSError):
        await read_word(reader)
    with pytest.raises(RouterOSError):
        length_prefix(MAX_WORD + 1)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("1w2d3h4m5s", 788645),
        ("0s", 0),
        ("1d02:03:04", 93784),
        ("02:03:04", 7384),
    ],
)
def test_uptime(value, expected):
    assert uptime_seconds(value) == expected


@pytest.mark.parametrize("value", [None, "", "1week", "00:61:01", "secret", "1sJUNK"])
def test_unknown_uptime_rejected(value):
    with pytest.raises(RouterOSError):
        uptime_seconds(value)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("1Gbps", 1000),
        ("100Mbps", 100),
        ("2.5Gbps", 2500),
        ("10Gbps", 10000),
        (None, None),
        ("unknown", None),
        ("0Mbps", None),
    ],
)
def test_link_speed(value, expected):
    assert link_speed(value) == expected


@pytest.mark.parametrize("version", ["7.24", "7.24.4", "7.24.4 (stable)", "7.25.1"])
def test_supported_version(version):
    assert check_version(version) == version


@pytest.mark.parametrize("version", [None, "7.23.9", "6.49.10", "8.0", "7.24beta1", "bad"])
def test_unsupported_version(version):
    with pytest.raises(RouterOSError):
        check_version(version)


def test_strict_port_mapping_and_no_unknown_interface_substitution(tmp_path):
    config = {"ports": [{"port_idx": 1, "interface": "renamed uplink"}]}
    assert RouterOSMap.model_validate(config).ports[0].interface == "renamed uplink"
    for ports in (
        [{"port_idx": 1, "interface": "ether1"}] * 2,
        [{"port_idx": 9, "interface": "ether1"}],
        [{"port_idx": 1, "interface": "ether1\n/run"}],
        [],
    ):
        with pytest.raises(ValidationError):
            RouterOSMap.model_validate({"ports": ports})
    path = tmp_path / "map.json"
    path.write_text('{"private-unknown-field":"do-not-log"}')
    with pytest.raises(RouterOSError, match="port-map-invalid") as exc:
        load_mapping(path)
    assert "do-not-log" not in str(exc.value)
    path.write_bytes(b"x" * (16 * 1024 + 1))
    with pytest.raises(RouterOSError):
        load_mapping(path)


def test_source_configuration_and_no_password_in_settings(tmp_path):
    with pytest.raises(ValidationError):
        Settings(telemetry_source="routeros")
    settings = Settings(
        telemetry_source="routeros",
        routeros_host="192.168.0.249",
        routeros_port=8728,
        routeros_tls=False,
        routeros_user="observer",
        routeros_password_file=tmp_path / "secret",
        routeros_map_file=tmp_path / "map",
    )
    with pytest.raises(ValidationError):
        Settings(**(settings.model_dump() | {"synthetic_file": "scene.json"}))
    for host in ("http://user:secret@host", "host:8728", "bad\nhost"):
        with pytest.raises(ValidationError):
            Settings(routeros_host=host)
    assert Settings().routeros_tls and Settings().routeros_port == 8729


def test_password_file_preserves_spaces_and_rejects_missing_or_malformed(tmp_path):
    path = tmp_path / "password"
    with pytest.raises(RouterOSError):
        read_password(path)
    path.write_text(" leading and trailing \n")
    assert read_password(path) == " leading and trailing "
    for contents in (b"", b"bad\0secret", b"bad\nsecret", b"x" * 4097):
        path.write_bytes(contents)
        with pytest.raises(RouterOSError) as exc:
            read_password(path)
        assert "secret" not in str(exc.value)


async def test_mutating_commands_cannot_be_sent_even_through_internal_executor():
    api = RouterOSAPI(Settings())
    for command in (
        "/interface/set",
        "/system/script/run",
        "/system/reboot",
        "/ip/firewall/filter/print",
    ):
        with pytest.raises(RouterOSError, match="command-not-allowed"):
            await api._execute(command, ())
    with pytest.raises(RouterOSError, match="command-not-allowed"):
        await api.table("/interface/print")


@pytest.mark.parametrize(
    "exc, code",
    [
        (OSError(errno.EHOSTUNREACH, "PRIVATE"), "network-unreachable"),
        (OSError(errno.ENETUNREACH, "PRIVATE"), "network-unreachable"),
        (ConnectionResetError(errno.ECONNRESET, "PRIVATE"), "connection-reset"),
        (BrokenPipeError(errno.EPIPE, "PRIVATE"), "connection-closed"),
        (socket.gaierror(socket.EAI_NONAME, "PRIVATE"), "dns-resolution-failed"),
        (TimeoutError("PRIVATE"), "timeout"),
        (ssl.SSLError("PRIVATE"), "tls-handshake-failed"),
        (asyncio.IncompleteReadError(b"PRIVATE", 10), "connection-closed"),
    ],
)
def test_safe_network_failure_classification_and_phase(exc, code):
    result = api_error(exc, "/login")
    assert result.code == code and result.public()["stage"] == "/login"
    assert "PRIVATE" not in str(result.public())
