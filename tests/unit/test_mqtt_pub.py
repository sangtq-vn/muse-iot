"""Unit tests for one-shot MQTT publishing (core/mqtt_pub.py)."""

import json
import socket
import sys
import types

import pytest

from core.mqtt_pub import (
    MqttPublishError,
    actuator_targets,
    publish_actuator,
    publish_sensor,
    publish_targets,
    sensor_targets,
)


def _single_config(**overrides):
    cfg = {"broker_host": "192.168.1.4", "broker_port": 1883, "topic_prefix": "muse-iot"}
    cfg.update(overrides)
    return {"device": {"adapter": "esp32_mqtt", "config": cfg}}


def _multi_config():
    node_cfg = {"broker_host": "192.168.1.4", "broker_port": 1883, "topic_prefix": "muse-iot"}
    return {
        "nodes": [
            {"id": "node_left", "adapter": "esp32_mqtt", "config": dict(node_cfg), "actuators": []},
            {"id": "node_right", "adapter": "esp32_mqtt", "config": dict(node_cfg),
             "actuators": ["relay_pump", "relay_fan"]},
        ]
    }


class _Sock:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def ok_socket(monkeypatch):
    seen = {}

    def fake_create_connection(addr, timeout=None):
        seen["addr"] = addr
        seen["timeout"] = timeout
        return _Sock()

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)
    return seen


@pytest.fixture
def stub_paho(monkeypatch):
    calls = []
    pkg = types.ModuleType("paho")
    pkg.__path__ = []
    sub = types.ModuleType("paho.mqtt")
    sub.__path__ = []
    mod = types.ModuleType("paho.mqtt.publish")
    mod.single = lambda *a, **k: calls.append((a, k))
    monkeypatch.setitem(sys.modules, "paho", pkg)
    monkeypatch.setitem(sys.modules, "paho.mqtt", sub)
    monkeypatch.setitem(sys.modules, "paho.mqtt.publish", mod)
    pkg.mqtt = sub  # fresh throwaway objects; sys.modules entries are reverted
    sub.publish = mod
    return calls


# ── Topic resolution ──────────────────────────────────────────────────────────


def test_actuator_targets_single_mode():
    (conn, topic), = actuator_targets(_single_config(), "relay_fan")
    assert topic == "muse-iot/actuator/relay_fan/set"
    assert (conn["host"], conn["port"]) == ("192.168.1.4", 1883)
    assert conn["username"] is None


def test_actuator_targets_single_mode_auth():
    (conn, _), = actuator_targets(_single_config(username="u", password="p"), "relay_fan")
    assert conn["username"] == "u"
    assert conn["password"] == "p"


def test_actuator_targets_multi_mode_routes_to_owner():
    targets = actuator_targets(_multi_config(), "relay_fan")
    assert len(targets) == 1
    conn, topic = targets[0]
    assert topic == "muse-iot/nodes/node_right/actuator/relay_fan/set"
    assert conn["host"] == "192.168.1.4"


def test_actuator_targets_unassigned_broadcasts():
    targets = actuator_targets(_multi_config(), "pwm_light")
    assert [t for _, t in targets] == [
        "muse-iot/nodes/node_left/actuator/pwm_light/set",
        "muse-iot/nodes/node_right/actuator/pwm_light/set",
    ]


def test_sensor_targets_single_and_multi():
    (conn, topic), = sensor_targets(_single_config(), "soil_cap")
    assert topic == "muse-iot/sensors/soil_cap"
    assert [t for _, t in sensor_targets(_multi_config(), "soil_cap")] == [
        "muse-iot/nodes/node_left/sensors/soil_cap",
        "muse-iot/nodes/node_right/sensors/soil_cap",
    ]


# ── Publishing ────────────────────────────────────────────────────────────────


def test_publish_actuator_payload_and_qos(ok_socket, stub_paho):
    publish_actuator(_single_config(), "relay_fan", "on", source="manual")
    assert ok_socket["addr"] == ("192.168.1.4", 1883)
    assert ok_socket["timeout"] == 10
    assert len(stub_paho) == 1
    (args, kwargs) = stub_paho[0]
    assert args[0] == "muse-iot/actuator/relay_fan/set"
    body = json.loads(args[1])
    assert body["value"] == "on" and body["source"] == "manual" and "ts" in body
    assert kwargs["qos"] == 1
    assert kwargs["hostname"] == "192.168.1.4" and kwargs["port"] == 1883
    assert kwargs["auth"] is None


def test_publish_actuator_sends_auth(ok_socket, stub_paho):
    publish_actuator(_single_config(username="u", password="p"), "relay_fan", "on")
    (_, kwargs) = stub_paho[0]
    assert kwargs["auth"] == {"username": "u", "password": "p"}


def test_publish_sensor_str_value(ok_socket, stub_paho):
    publish_sensor(_single_config(), "soil_cap", 52.3)
    (args, _) = stub_paho[0]
    assert args[0] == "muse-iot/sensors/soil_cap"
    assert args[1] == "52.3"


def test_timeout_error_names_broker_topic_and_cause(monkeypatch, stub_paho):
    def boom(addr, timeout=None):
        raise socket.timeout("timed out")

    monkeypatch.setattr(socket, "create_connection", boom)
    with pytest.raises(MqttPublishError) as exc:
        publish_actuator(_single_config(), "relay_fan", "on")
    msg = str(exc.value)
    assert "192.168.1.4:1883" in msg
    assert "muse-iot/actuator/relay_fan/set" in msg
    assert "timed out" in msg
    assert stub_paho == []  # pre-check fails before any publish attempt


def test_refused_error_hint(monkeypatch, stub_paho):
    def boom(addr, timeout=None):
        raise ConnectionRefusedError(111, "refused")

    monkeypatch.setattr(socket, "create_connection", boom)
    with pytest.raises(MqttPublishError, match="refused"):
        publish_actuator(_single_config(), "relay_fan", "on")


def test_unresolvable_mqtt_hostname_hint(monkeypatch, stub_paho):
    def boom(addr, timeout=None):
        raise socket.gaierror("nope")

    monkeypatch.setattr(socket, "create_connection", boom)
    cfg = _single_config(broker_host="mqtt")
    with pytest.raises(MqttPublishError) as exc:
        publish_actuator(cfg, "relay_fan", "on")
    assert "mqtt" in str(exc.value) and "compose" in str(exc.value)


def test_partial_failure_does_not_raise(ok_socket, stub_paho, monkeypatch):
    calls = stub_paho
    orig_single = sys.modules["paho.mqtt.publish"].single

    def flaky(*a, **k):
        if "node_left" in a[0]:
            raise RuntimeError("boom")
        return orig_single(*a, **k)

    sys.modules["paho.mqtt.publish"].single = flaky
    publish_actuator(_multi_config(), "pwm_light", "on")  # broadcast, left fails
    assert len(calls) == 1  # right still published


def test_total_failure_raises(ok_socket, stub_paho, monkeypatch):
    def always_boom(*a, **k):
        raise RuntimeError("boom")

    sys.modules["paho.mqtt.publish"].single = always_boom
    with pytest.raises(MqttPublishError, match="boom"):
        publish_actuator(_multi_config(), "pwm_light", "on")
