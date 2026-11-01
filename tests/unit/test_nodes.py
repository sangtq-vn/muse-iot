"""Unit tests for the /api/nodes node model (core/web/nodes.py)."""

import time

from core.web.nodes import ONLINE_WINDOW_SEC, build_nodes


NOW = 1_700_000_000.0


def _row(value, age_sec=10, unit=""):
    return {"value": value, "ts": NOW - age_sec, "unit": unit, "age_sec": age_sec}


def _multi_config():
    return {
        "box_id": "test_box",
        "nodes": [
            {
                "id": "node_left",
                "adapter": "esp32_mqtt",
                "config": {"broker_host": "192.168.1.4", "broker_port": 1883, "topic_prefix": "muse-iot"},
                "actuators": ["relay_pump"],
            },
            {
                "id": "node_right",
                "adapter": "esp32_mqtt",
                "config": {"broker_host": "192.168.1.4", "broker_port": 1883, "topic_prefix": "muse-iot"},
                "actuators": ["relay_fan"],
            },
        ],
    }


def test_multi_node_partitions_prefixed_ids():
    latest = {
        "node_left_dht11_temp": _row(29.8),
        "node_right_dht11_temp": _row(29.0),
        "dht11_temp": _row(29.4),
        "dht11_temp_min": _row(29.0),
        "dht11_temp_max": _row(29.8),
    }
    states = {"relay_pump": {"state": "off", "ts": NOW - 60}, "relay_fan": {"state": "on", "ts": NOW - 30}}
    nodes = build_nodes(_multi_config(), latest, states, now=NOW)

    assert [n["id"] for n in nodes] == ["node_left", "node_right"]
    left, right = nodes
    assert list(left["sensors"]) == ["dht11_temp"]
    assert left["sensors"]["dht11_temp"]["value"] == 29.8
    assert right["sensors"]["dht11_temp"]["value"] == 29.0
    # Aggregates belong to no node.
    assert "dht11_temp" not in left["sensors"] or left["sensors"]["dht11_temp"]["value"] == 29.8
    assert "dht11_temp_min" not in left["sensors"]
    assert "dht11_temp_max" not in left["sensors"]
    # Actuator ownership follows node config.
    assert [a["id"] for a in left["actuators"]] == ["relay_pump"]
    assert left["actuators"][0]["state"] == "off"
    assert [a["id"] for a in right["actuators"]] == ["relay_fan"]
    assert right["actuators"][0]["state"] == "on"
    assert left["broker"] == "192.168.1.4:1883"
    assert left["topic_prefix"] == "muse-iot"


def test_overlapping_prefixes_match_longest_first():
    config = {
        "box_id": "b",
        "nodes": [
            {"id": "node_1", "adapter": "a", "config": {}, "actuators": []},
            {"id": "node_10", "adapter": "a", "config": {}, "actuators": []},
        ],
    }
    latest = {"node_10_temp": _row(1.0), "node_1_temp": _row(2.0)}
    nodes = build_nodes(config, latest, {}, now=NOW)
    by_id = {n["id"]: n for n in nodes}
    assert by_id["node_10"]["sensors"]["temp"]["value"] == 1.0
    assert by_id["node_1"]["sensors"]["temp"]["value"] == 2.0


def test_stale_node_reported_offline():
    latest = {
        "node_left_dht11_temp": _row(29.8, age_sec=ONLINE_WINDOW_SEC + 60),
        "node_right_dht11_temp": _row(29.0, age_sec=5),
    }
    nodes = build_nodes(_multi_config(), latest, {}, now=NOW)
    by_id = {n["id"]: n for n in nodes}
    assert by_id["node_left"]["online"] is False
    assert by_id["node_right"]["online"] is True
    assert by_id["node_left"]["last_seen"] == NOW - (ONLINE_WINDOW_SEC + 60)


def test_node_without_readings_is_offline_with_null_seen():
    nodes = build_nodes(_multi_config(), {}, {}, now=NOW)
    assert nodes[0]["online"] is False
    assert nodes[0]["last_seen"] is None
    assert nodes[0]["sensors"] == {}


def test_single_device_mode_yields_local_node():
    config = {
        "box_id": "solo",
        "device": {
            "adapter": "esp32_mqtt",
            "config": {"broker_host": "mqtt", "broker_port": 1883, "topic_prefix": "muse-iot"},
        },
    }
    latest = {"dht11_temp": _row(27.1, unit="celsius"), "soil_cap": _row(55.0, unit="percent")}
    states = {"relay_pump": {"state": "off", "ts": NOW - 10}}
    (node,) = build_nodes(config, latest, states, now=NOW)
    assert node["id"] == "local"
    assert node["adapter"] == "esp32_mqtt"
    assert node["broker"] == "mqtt:1883"
    assert node["online"] is True
    assert set(node["sensors"]) == {"dht11_temp", "soil_cap"}
    assert [a["id"] for a in node["actuators"]] == ["relay_pump"]


def test_unknown_actuator_state_defaults():
    nodes = build_nodes(_multi_config(), {"node_left_dht11_temp": _row(1.0)}, {}, now=NOW)
    assert nodes[0]["actuators"] == [{"id": "relay_pump", "state": "unknown", "ts": None}]


def test_now_defaults_to_current_time():
    ts = time.time()
    (node,) = build_nodes(
        {"device": {"adapter": "a", "config": {}}},
        {"t": {"value": 1.0, "ts": ts, "unit": ""}},
        {},
    )
    assert node["online"] is True
    assert node["sensors"]["t"]["age_sec"] < 5
