"""Unit tests for RootcauseAnalyzer scoping and framing (no LLM calls)."""

import sqlite3
import time
from types import SimpleNamespace

from core.rootcause_analyzer import RootcauseAnalyzer


def _ev(ts, type="SENSOR_ANOMALY", note="n", severity="info"):
    return SimpleNamespace(ts=ts, type=type, severity=severity,
                           sensor_id="t", value=1.0, note=note)


def _dec(ts, reason="r"):
    return SimpleNamespace(ts=ts, commands={"a": "on"},
                           reason=reason, was_overridden=False)


class _LLM:
    def __init__(self):
        self.contexts = []

    def call(self, context):
        self.contexts.append(context)
        return SimpleNamespace(commands={"primary_cause": "x"}, reason="why")


def _history(events=(), decisions=(), stats=None, conn=None):
    stats = stats if stats is not None else {"t": 1.0}
    ns = SimpleNamespace(
        events=lambda box_id, **k: list(events),
        llm_decisions=lambda box_id, limit=20: list(decisions)[:limit],
        sensor_avg=lambda box_id, window: dict(stats),
        sensor_minmax=lambda box_id, window: dict(stats),
        sensor_trend=lambda box_id, window: dict(stats),
    )
    if conn is not None:
        ns.conn = conn
    return ns


def _domain(concept="grow_cycle", analyst_role="plant pathologist",
            sensors=("t",)):
    return SimpleNamespace(
        sensors=[{"id": s} for s in sensors],
        session={"concept": concept},
        llm={"analyst_role": analyst_role},
    )


def _session_db(rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE sessions (id TEXT, box_id TEXT, domain_id TEXT, "
        "profile_id TEXT, started_at REAL, ended_at REAL, outcome TEXT, "
        "notes TEXT, rootcause TEXT)"
    )
    conn.executemany(
        "INSERT INTO sessions (id, box_id, started_at, ended_at) VALUES (?,?,?,?)", rows
    )
    return conn


def test_framing_uses_domain_role_and_concept():
    llm = _LLM()
    analyzer = RootcauseAnalyzer(
        _history(), llm,
        domain=_domain(concept="health_monitoring_period",
                       analyst_role="health data reviewer"),
    )
    analyzer.analyze("b", "s1")
    ctx = llm.contexts[0]
    assert "health data reviewer" in ctx["system"]
    assert "HEALTH MONITORING PERIOD ANALYSIS REQUEST" in ctx["user"]
    assert "plant" not in ctx["system"].lower()
    assert "plant" not in ctx["user"].lower()


def test_framing_defaults_are_generic():
    llm = _LLM()
    RootcauseAnalyzer(_history(), llm).analyze("b", "s1")
    ctx = llm.contexts[0]
    assert "IoT systems expert" in ctx["system"]
    assert "SESSION ANALYSIS REQUEST" in ctx["user"]


def test_stats_filtered_to_bare_ids_with_domain():
    llm = _LLM()
    stats = {"t": 1.0, "node_left_t": 2.0, "t_min": 0.0, "t_max": 9.0}
    analyzer = RootcauseAnalyzer(_history(stats=stats), llm, domain=_domain())
    analyzer.analyze("b", "s1")
    user = llm.contexts[0]["user"]
    assert "node_left_t" not in user
    assert "t_min" not in user
    assert "t_max" not in user


def test_stats_drop_minmax_without_domain():
    llm = _LLM()
    stats = {"t": 1.0, "t_min": 0.0}
    RootcauseAnalyzer(_history(stats=stats), llm).analyze("b", "s1")
    user = llm.contexts[0]["user"]
    assert "t_min" not in user


def test_session_scope_filters_events_and_decisions():
    now = time.time()
    conn = _session_db([("s1", "b", now - 1000, now - 100)])
    llm = _LLM()
    analyzer = RootcauseAnalyzer(
        _history(
            events=[_ev(now - 500, note="inside"), _ev(now - 50, note="after-end"),
                    _ev(now - 5000, note="before-start")],
            decisions=[_dec(now - 500, reason="inside-dec"), _dec(now - 10, reason="after-dec")],
            conn=conn,
        ),
        llm,
    )
    analyzer.analyze("b", "s1")
    user = llm.contexts[0]["user"]
    assert "inside" in user and "inside-dec" in user
    assert "after-end" not in user and "before-start" not in user
    assert "after-dec" not in user


def test_missing_session_falls_back_to_full_history():
    conn = _session_db([])
    llm = _LLM()
    analyzer = RootcauseAnalyzer(
        _history(events=[_ev(time.time(), note="kept")], conn=conn), llm
    )
    analyzer.analyze("b", "nope")
    assert "kept" in llm.contexts[0]["user"]
