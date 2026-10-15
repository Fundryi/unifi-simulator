import json
from pathlib import Path

import pytest

from mtunifi.scenario import MAX_COUNTER, MAX_SCENARIO_BYTES, ScenarioError, SyntheticSource

MAC = "02:11:22:33:44:55"
DEMO = Path(__file__).resolve().parents[2] / "examples/synthetic-switch.json"


def example():
    return json.loads(DEMO.read_text())


def write(path, body):
    # Atomic rename exercises the file-replacement hot reload used by editors.
    replacement = path.with_suffix(".tmp")
    replacement.write_text(json.dumps(body))
    replacement.replace(path)


def test_demo_schema_and_source_supported_wire_fields():
    source = SyntheticSource(DEMO)
    wire = source.snapshot(100, MAC)
    assert len(wire["port_table"]) == 8
    assert len(wire["port_table"][2]["mac_table"]) == 2
    unknown = wire["port_table"][2]["mac_table"][1]
    assert unknown["mac"] == "02:00:00:01:00:03"
    assert unknown["vlan"] == 20
    assert "ip" not in unknown
    assert set(wire) == {"port_table", "lldp_table"}
    assert len(wire["lldp_table"]) == 2
    assert wire["lldp_table"][0]["is_wired"] is True
    assert source.last_summary["active_clients"] == 3


def test_counters_follow_elapsed_time_not_inform_count():
    source = SyntheticSource(DEMO)
    assert source.snapshot(100, MAC)["port_table"][0]["rx_bytes"] == 10_000_000
    assert source.snapshot(100.5, MAC)["port_table"][0]["rx_bytes"] == 10_062_500
    assert source.snapshot(112, MAC)["port_table"][0]["rx_bytes"] == 11_500_000
    assert source.snapshot(112, MAC)["port_table"][0]["rx_bytes"] == 11_500_000
    assert source.snapshot(111, MAC)["port_table"][0]["rx_bytes"] == 11_500_000
    assert source.snapshot(113, MAC)["port_table"][0]["rx_bytes"] == 11_625_000


def test_rate_and_link_reload_preserve_counts_and_pause_traffic(tmp_path):
    path = tmp_path / "scene.json"
    data = example()
    write(path, data)
    source = SyntheticSource(path)
    source.snapshot(0, MAC)
    data["ports"][0]["rx_bytes_per_second"] = 250000
    write(path, data)
    assert source.snapshot(10, MAC)["port_table"][0]["rx_bytes"] == 11_250_000
    assert source.snapshot(20, MAC)["port_table"][0]["rx_bytes"] == 13_750_000
    data["ports"][0]["up"] = False
    write(path, data)
    frame = source.snapshot(20, MAC)
    assert frame["port_table"][0]["speed"] == 0
    assert not frame["port_table"][0]["up"]
    assert len(frame["lldp_table"]) == 1
    assert source.snapshot(50, MAC)["port_table"][0]["rx_bytes"] == 13_750_000


def test_link_down_hides_clients_without_inventing_unknown_ip(tmp_path):
    path = tmp_path / "scene.json"
    data = example()
    write(path, data)
    source = SyntheticSource(path)
    source.snapshot(0, MAC)
    data["ports"][2]["up"] = False
    write(path, data)
    frame = source.snapshot(10, MAC)
    assert frame["port_table"][2]["mac_table"] == []
    assert source.last_summary["active_clients"] == 1
    data["ports"][2]["up"] = True
    write(path, data)
    assert source.snapshot(20, MAC)["port_table"][2]["mac_table"][0]["uptime"] == 0


def test_explicit_counter_reset_and_process_restart(tmp_path):
    path = tmp_path / "scene.json"
    data = example()
    write(path, data)
    source = SyntheticSource(path)
    source.snapshot(0, MAC)
    assert source.snapshot(20, MAC)["port_table"][0]["rx_bytes"] > 10_000_000
    data["ports"][0]["counter_epoch"] = 1
    write(path, data)
    assert source.snapshot(30, MAC)["port_table"][0]["rx_bytes"] == 10_000_000
    assert source.snapshot(40, MAC)["port_table"][0]["rx_bytes"] == 11_250_000
    assert SyntheticSource(path).snapshot(999, MAC)["port_table"][0]["rx_bytes"] == 10_000_000


def test_client_moves_without_resetting_port_counters(tmp_path):
    path = tmp_path / "scene.json"
    data = example()
    write(path, data)
    source = SyntheticSource(path)
    source.snapshot(0, MAC)
    source.snapshot(10, MAC)
    data["ports"][4]["up"] = True
    data["clients"][2]["port_idx"] = 5
    write(path, data)
    frame = source.snapshot(20, MAC)
    assert len(frame["port_table"][2]["mac_table"]) == 1
    assert frame["port_table"][4]["mac_table"][0]["uptime"] == 0
    assert frame["port_table"][2]["rx_bytes"] == 400_000


def test_fractional_increments_and_large_integer_precision(tmp_path):
    path = tmp_path / "scene.json"
    data = example()
    data["ports"][0].update(rx_bytes=2**60, rx_bytes_per_second=1)
    write(path, data)
    source = SyntheticSource(path)
    source.snapshot(0, MAC)
    for quarter in range(1, 5):
        frame = source.snapshot(quarter / 4, MAC)
    assert frame["port_table"][0]["rx_bytes"] == 2**60 + 1


def test_counter_saturation_does_not_overflow(tmp_path):
    path = tmp_path / "scene.json"
    data = example()
    data["ports"][0]["rx_bytes"] = MAX_COUNTER - 1
    write(path, data)
    source = SyntheticSource(path)
    source.snapshot(0, MAC)
    assert source.snapshot(10, MAC)["port_table"][0]["rx_bytes"] == MAX_COUNTER


def test_bad_reload_retains_good_and_recovers_without_secret_logging(tmp_path, caplog):
    path = tmp_path / "scene.json"
    write(path, example())
    source = SyntheticSource(path)
    source.snapshot(0, MAC)
    path.write_text('{"password":"DO-NOT-LOG-THIS"}')
    with caplog.at_level("INFO"):
        assert source.snapshot(10, MAC)["port_table"][0]["rx_bytes"] == 11_250_000
        source.snapshot(20, MAC)
        assert source.status == "using-last-good"
        assert caplog.text.count('"event": "synthetic_config_rejected"') == 1
        write(path, example())
        source.snapshot(30, MAC)
        assert source.status == "active"
    assert "DO-NOT-LOG-THIS" not in caplog.text


def test_deleted_file_retains_last_good_and_missing_initial_file_fails(tmp_path):
    path = tmp_path / "scene.json"
    with pytest.raises(ScenarioError, match="Cannot read"):
        SyntheticSource(path)
    write(path, example())
    source = SyntheticSource(path)
    path.unlink()
    assert len(source.snapshot(0, MAC)["port_table"]) == 8
    assert source.status == "using-last-good"


@pytest.mark.parametrize(
    "case",
    [
        "ports",
        "duplicate_port",
        "duplicate_client",
        "vlan",
        "mac",
        "neighbor_mac",
        "speed",
        "negative_counter",
        "bool",
        "unknown",
        "line_rate",
        "disabled",
        "neighbor_port",
        "ip",
    ],
)
def test_invalid_scenarios_are_rejected_with_safe_errors(tmp_path, case):
    data = example()
    if case == "ports":
        data["ports"].pop()
    elif case == "duplicate_port":
        data["ports"][7]["port_idx"] = 1
    elif case == "duplicate_client":
        data["clients"].append(data["clients"][0])
    elif case == "vlan":
        data["clients"][0]["vlan"] = 4095
    elif case == "mac":
        data["clients"][0]["mac"] = "ff:ff:ff:ff:ff:ff"
    elif case == "neighbor_mac":
        data["neighbors"][0]["chassis_id"] = "00:11:22:33:44:55"
    elif case == "speed":
        data["ports"][0]["speed_mbps"] = 2500
    elif case == "negative_counter":
        data["ports"][0]["rx_bytes"] = -1
    elif case == "bool":
        data["ports"][0]["up"] = "false"
    elif case == "unknown":
        data["password"] = "DO-NOT-LOG"
    elif case == "line_rate":
        data["ports"][0]["rx_bytes_per_second"] = 125000001
    elif case == "disabled":
        data["ports"][0]["enabled"] = False
    elif case == "neighbor_port":
        data["neighbors"][0]["local_port_idx"] = 9
    elif case == "ip":
        data["clients"][0]["ip"] = "not-an-ip"
    path = tmp_path / "scene.json"
    write(path, data)
    with pytest.raises(ScenarioError, match="^Invalid synthetic scenario$"):
        SyntheticSource(path)


def test_identity_collision_rejected_and_bad_reload_is_not_activated(tmp_path):
    path = tmp_path / "scene.json"
    data = example()
    write(path, data)
    source = SyntheticSource(path)
    source.snapshot(0, MAC)
    data["clients"][0]["mac"] = MAC
    write(path, data)
    assert source.snapshot(10, MAC)["port_table"][1]["mac_table"][0]["mac"] != MAC
    assert source.status == "using-last-good"
    with pytest.raises(ScenarioError, match="reuses"):
        SyntheticSource(path).snapshot(0, MAC)


def test_size_is_bounded(tmp_path):
    path = tmp_path / "scene.json"
    path.write_bytes(b" " * (MAX_SCENARIO_BYTES + 1))
    with pytest.raises(ScenarioError, match="size limit"):
        SyntheticSource(path)
