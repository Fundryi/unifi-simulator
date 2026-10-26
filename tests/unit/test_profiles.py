from mtunifi.profiles import profile


def test_aggregation_profile_is_eight_sfp_plus_ports():
    agg = profile("usw-aggregation-experimental")
    assert agg.model == "USAGG"
    assert agg.kind == "switch"
    assert agg.port_count == 8
    assert all(speed == 10000 for speed in agg.max_speeds)
