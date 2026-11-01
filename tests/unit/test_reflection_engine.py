"""Unit tests for ReflectionEngine prompt construction.

Requires Python 3.10+ at import time (the module uses `X | None`
annotations, evaluated at def time). Skipped on older interpreters;
CI runs 3.11+.
"""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

if sys.version_info < (3, 10):
    pytest.skip("reflection_engine needs Python 3.10+ syntax",
                allow_module_level=True)

from core.reflection_engine import DEFAULT_INTERVAL_SEC, ReflectionEngine


def test_interval_is_six_hours():
    # Regression guard: the constant once read 0.5 * 3600 (30 min)
    # while every docstring claimed 6 hours.
    assert DEFAULT_INTERVAL_SEC == 6 * 3600


def _engine(domain_llm=None, domain_sensors=None):
    history = MagicMock()
    history.sensor_avg.return_value = {"temp": 25.0, "node_left_temp": 26.0, "temp_min": 20.0}
    history.sensor_minmax.return_value = {"temp": {"min": 20.0, "max": 30.0}}
    history.sensor_trend.return_value = {"temp": 0.1}
    history.events.return_value = []
    history.llm_decisions.return_value = []
    session = MagicMock()
    session.box_id = "b"
    session.day_number.return_value = 5
    session.current_phase.return_value = "active"
    session.current_phase_targets.return_value = {"temp": [20, 28]}
    domain = SimpleNamespace(
        sensors=domain_sensors if domain_sensors is not None else [{"id": "temp"}],
        llm=domain_llm if domain_llm is not None else {},
    )
    profile = {"name": "Test", "cultivar": ""}
    return ReflectionEngine(history, session, MagicMock(), domain, profile)


def test_framing_uses_domain_role_and_topic():
    engine = _engine(
        domain_llm={"observer_role": "preventive health analyst", "insight_topic": "health"}
    )
    prompt = engine._build_prompt()
    assert "preventive health analyst" in prompt["system"]
    assert "health insights" in prompt["system"]
    assert "health insights" in prompt["user"]
    assert "plant" not in prompt["system"].lower()
    assert "agronomist" not in prompt["system"]


def test_framing_defaults_are_generic():
    engine = _engine()
    prompt = engine._build_prompt()
    assert "IoT systems analyst" in prompt["system"]
    assert "system-health insights" in prompt["user"]
    assert "plant" not in prompt["system"].lower()


def test_stats_filtered_to_bare_ids():
    engine = _engine()
    user = engine._build_prompt()["user"]
    assert "node_left_temp" not in user
    assert "temp_min" not in user
    assert "25.0" in user
