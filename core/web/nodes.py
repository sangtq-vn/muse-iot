"""
Node model for the web UI — stdlib only, no app imports.

Builds the per-node view served by GET /api/nodes from:
  - the box config (`nodes:` list, or legacy single `device:`)
  - latest readings keyed by stored sensor id
  - last-known actuator states

Stored sensor-id conventions (see DeviceRouter):
  multi-node : "{node_id}_{sensor_id}" per node, plus "{sensor_id}",
               "{sensor_id}_min", "{sensor_id}_max" aggregates
  single-node: bare "{sensor_id}"
"""

from typing import Optional

# A node with no reading newer than this is reported stale.
ONLINE_WINDOW_SEC = 300


def _strip_minmax(sensor_id: str) -> Optional[str]:
    """Return the aggregate base id for a _min/_max key, else None."""
    if sensor_id.endswith(("_min", "_max")):
        return sensor_id[:-4]
    return None


def build_nodes(
    config: dict,
    latest: dict[str, dict],
    actuator_states: dict[str, dict],
    now: Optional[float] = None,
) -> list[dict]:
    """
    Build the node list for /api/nodes.

    config: full box config (uses `nodes:` or `device:` + `box_id`)
    latest: {stored_sensor_id: {"value","ts","unit","age_sec",...}}
    actuator_states: {actuator_id: {"state","ts",...}}
    now: current epoch seconds (injectable for tests)

    Returns a list of node dicts in config order.
    """
    import time as _time
    now = _time.time() if now is None else now

    nodes_cfg = config.get("nodes")
    if nodes_cfg:
        node_defs = [
            {
                "id": n["id"],
                "adapter": n.get("adapter", ""),
                "broker": _broker_label(n.get("config", {})),
                "topic_prefix": n.get("config", {}).get("topic_prefix", ""),
                "actuators": list(n.get("actuators", []) or []),
                "multi": True,
            }
            for n in nodes_cfg
        ]
    else:
        device_cfg = config.get("device", {})
        node_defs = [
            {
                "id": "local",
                "adapter": device_cfg.get("adapter", ""),
                "broker": _broker_label(device_cfg.get("config", {})),
                "topic_prefix": device_cfg.get("config", {}).get("topic_prefix", ""),
                "actuators": sorted(actuator_states),
                "multi": False,
            }
        ]

    # Longest prefix first so node_1 never shadows node_10.
    prefixes = sorted(
        ((f"{d['id']}_", d["id"]) for d in node_defs if d["multi"]),
        key=lambda p: -len(p[0]),
    )

    per_node: dict[str, dict[str, dict]] = {d["id"]: {} for d in node_defs}
    if node_defs and not node_defs[0]["multi"]:
        only = node_defs[0]["id"]
        for sid, row in latest.items():
            if _strip_minmax(sid) is None:
                per_node[only][sid] = row
    else:
        for sid, row in latest.items():
            for prefix, nid in prefixes:
                if sid.startswith(prefix):
                    per_node[nid][sid[len(prefix):]] = row
                    break
            # Bare ids and _min/_max aggregates belong to no node.

    nodes = []
    for d in node_defs:
        sensors = per_node[d["id"]]
        last_seen = max((r["ts"] for r in sensors.values()), default=None)
        online = last_seen is not None and (now - last_seen) <= ONLINE_WINDOW_SEC
        nodes.append(
            {
                "id": d["id"],
                "adapter": d["adapter"],
                "broker": d["broker"],
                "topic_prefix": d["topic_prefix"],
                "online": online,
                "last_seen": last_seen,
                "sensors": {
                    sid: {
                        "value": r["value"],
                        "ts": r["ts"],
                        "unit": r.get("unit") or "",
                        "age_sec": int(now - r["ts"]),
                    }
                    for sid, row in sensors.items()
                    for r in [row]
                },
                "actuators": [
                    {
                        "id": aid,
                        **actuator_states.get(aid, {"state": "unknown", "ts": None}),
                    }
                    for aid in d["actuators"]
                ],
            }
        )
    return nodes


def _broker_label(device_config: dict) -> str:
    host = device_config.get("broker_host", "")
    port = device_config.get("broker_port", "")
    if host and port:
        return f"{host}:{port}"
    return str(host or "")
