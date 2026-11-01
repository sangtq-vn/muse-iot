"""
One-shot MQTT publishing for the web UI and chat — stdlib + paho (lazy).

Mirrors the poll loop's connection settings so manual commands behave
exactly like orchestrator commands:
  - multi-node `nodes:` config: actuators route to the owning node's
    namespaced topic, using that node's own broker/auth settings
  - single-node `device:` config: legacy flat topics
  - optional username/password auth (see device.yaml schema)

Failures raise MqttPublishError whose message always names the broker,
topic, and likely cause — a bare "timed out" is unactionable.
"""

import json
import logging
import socket
import time

log = logging.getLogger("muse-iot.mqtt_pub")

# Bound for the TCP pre-check. paho's one-shot publish uses a blocking
# connect with no timeout, so a blackholed host would otherwise hang the
# HTTP request for minutes instead of failing fast with a clear error.
CONNECT_TIMEOUT_SEC = 10


class MqttPublishError(RuntimeError):
    """One-shot publish failed — message names host:port, topic, and cause."""


def actuator_targets(config: dict, actuator_id: str) -> list:
    """
    Return [(conn, topic)] for an actuator command.
    conn = {"host", "port", "username", "password"}.
    Unassigned actuators broadcast to every node, like DeviceRouter.
    """
    nodes = config.get("nodes")
    if nodes:
        owners = [n for n in nodes if actuator_id in (n.get("actuators") or [])]
        return [
            (_conn(n.get("config", {})),
             f"{n.get('config', {}).get('topic_prefix', 'muse-iot')}"
             f"/nodes/{n['id']}/actuator/{actuator_id}/set")
            for n in (owners or list(nodes))
        ]
    cfg = config.get("device", {}).get("config", {})
    prefix = cfg.get("topic_prefix", "muse-iot")
    return [(_conn(cfg), f"{prefix}/actuator/{actuator_id}/set")]


def sensor_targets(config: dict, sensor_id: str) -> list:
    """
    Return [(conn, topic)] for a manual sensor reading. In multi-node mode
    a box-level reading fans out to every node so each node's cache and
    the fleet aggregate stay consistent with single-node behaviour.
    """
    nodes = config.get("nodes")
    if nodes:
        return [
            (_conn(n.get("config", {})),
             f"{n.get('config', {}).get('topic_prefix', 'muse-iot')}"
             f"/nodes/{n['id']}/sensors/{sensor_id}")
            for n in nodes
        ]
    cfg = config.get("device", {}).get("config", {})
    prefix = cfg.get("topic_prefix", "muse-iot")
    return [(_conn(cfg), f"{prefix}/sensors/{sensor_id}")]


def publish_actuator(config: dict, actuator_id: str, value, source: str = "manual") -> None:
    """Publish an actuator command. Raises MqttPublishError on failure."""
    payload = json.dumps({"value": value, "ts": time.time(), "source": source})
    publish_targets(actuator_targets(config, actuator_id), payload)


def publish_sensor(config: dict, sensor_id: str, value: float) -> None:
    """Publish a manual sensor reading. Raises MqttPublishError on failure."""
    publish_targets(sensor_targets(config, sensor_id), str(value))


def publish_targets(targets: list, payload: str) -> None:
    """Publish one payload to every (conn, topic) target (QoS 1, like the poll loop)."""
    import paho.mqtt.publish as mqtt_publish

    errors = []
    for conn, topic in targets:
        try:
            _check_reachable(conn, topic)
            auth = None
            if conn.get("username"):
                auth = {"username": conn["username"], "password": conn.get("password", "")}
            mqtt_publish.single(
                topic, payload, qos=1,
                hostname=conn["host"], port=conn["port"],
                auth=auth, keepalive=10,
            )
        except MqttPublishError as e:
            errors.append(str(e))
        except Exception as e:  # broker died between pre-check and publish
            errors.append(
                f"MQTT publish to {conn['host']}:{conn['port']} failed "
                f"(topic '{topic}'): {e}"
            )
    if errors:
        if len(errors) == len(targets):
            raise MqttPublishError("; ".join(errors))
        log.warning(f"MQTT partial publish failure: {'; '.join(errors)}")


def _conn(device_config: dict) -> dict:
    return {
        "host": device_config.get("broker_host", "localhost"),
        "port": int(device_config.get("broker_port", 1883)),
        "username": device_config.get("username") or None,
        "password": device_config.get("password", ""),
    }


def _check_reachable(conn: dict, topic: str) -> None:
    """Fast TCP pre-check so failures are quick and diagnosable."""
    host, port = conn["host"], conn["port"]
    try:
        sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_SEC)
    except socket.timeout:
        raise MqttPublishError(
            f"MQTT publish timed out after {CONNECT_TIMEOUT_SEC}s — "
            f"broker {host}:{port} unreachable from this process "
            f"(topic '{topic}'). Wrong host/port, broker down, "
            f"or firewall blocking {port}?"
        )
    except ConnectionRefusedError:
        raise MqttPublishError(
            f"MQTT connection refused by {host}:{port} (topic '{topic}') — "
            f"broker down, wrong port, or broker requires auth this config lacks?"
        )
    except socket.gaierror:
        hint = (
            " — Docker service names like 'mqtt' only resolve inside compose; "
            "use the LAN IP when running outside Docker"
            if host == "mqtt" else ""
        )
        raise MqttPublishError(
            f"MQTT cannot resolve broker hostname '{host}' (topic '{topic}'){hint}"
        )
    except OSError as e:
        raise MqttPublishError(
            f"MQTT network error reaching {host}:{port} "
            f"(topic '{topic}'): {e}"
        )
    else:
        sock.close()
