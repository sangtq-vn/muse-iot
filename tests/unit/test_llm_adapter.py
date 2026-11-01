"""Unit tests for Muse length-cutoff handling (core/llm/llm_adapter.py).

Regression tests for the production failure where muse-spark-1.3 spent the
whole 1024-token budget on reasoning and returned content=None with
finish_reason=length.
"""

import sys
import types

import pytest

from core.llm.llm_adapter import (
    DEFAULT_MAX_TOKENS,
    LLMAdapter,
    LLMOutputCutoffError,
)


def _resp(content=None, finish="stop", reasoning=0, total=100):
    return {
        "choices": [{
            "finish_reason": finish,
            "message": {"content": content, "role": "assistant"},
        }],
        "usage": {
            "total_tokens": total,
            "completion_tokens_details": {"reasoning_tokens": reasoning},
        },
    }


@pytest.fixture
def stub_requests(monkeypatch):
    """Stub `requests` with a scripted list of chat-completion responses."""
    calls = []
    queue = []

    class FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        return FakeResp(queue.pop(0))

    mod = types.ModuleType("requests")
    mod.post = fake_post
    monkeypatch.setitem(sys.modules, "requests", mod)
    return calls, queue


def _ctx():
    return {"system": "S", "user": "U"}


def test_default_budget_is_4096():
    assert LLMAdapter({"api_key": "k"}).max_tokens == 4096
    assert DEFAULT_MAX_TOKENS == 4096


def test_length_cutoff_escalates_then_succeeds(stub_requests):
    calls, queue = stub_requests
    queue.extend([
        _resp(content=None, finish="length", reasoning=1022, total=2952),
        _resp(content='{"relay_fan": "on", "reason": "hot"}', finish="stop"),
    ])
    adapter = LLMAdapter({"api_key": "k"})
    out = adapter.call(_ctx())
    assert out.commands == {"relay_fan": "on"}
    assert out.reason == "hot"
    assert [c["max_tokens"] for c in calls] == [4096, 8192]
    assert calls[0]["response_format"] == {"type": "json_object"}


def test_persistent_cutoff_raises_without_outer_retry(stub_requests):
    calls, queue = stub_requests
    queue.extend([
        _resp(content=None, finish="length", reasoning=4000),
        _resp(content=None, finish="length", reasoning=8000),
        _resp(content=None, finish="length", reasoning=16000),
    ])
    adapter = LLMAdapter({"api_key": "k"})
    with pytest.raises(LLMOutputCutoffError) as exc:
        adapter.call(_ctx())
    # 3 escalating attempts inside, zero outer retries (each would re-burn ~16k)
    assert len(calls) == 3
    assert [c["max_tokens"] for c in calls] == [4096, 8192, 16384]
    msg = str(exc.value)
    assert "max_tokens" in msg and "reasoning_effort" in msg


def test_cutoff_ceiling_respected(stub_requests):
    calls, queue = stub_requests
    queue.extend([_resp(content=None, finish="length", reasoning=1)] * 3)
    adapter = LLMAdapter({"api_key": "k", "max_tokens": 16384})
    with pytest.raises(LLMOutputCutoffError):
        adapter.call(_ctx())
    assert [c["max_tokens"] for c in calls] == [16384]


def test_empty_content_other_reason_stays_retryable(stub_requests, monkeypatch):
    calls, queue = stub_requests
    queue.extend([
        {"choices": [], "usage": {"total_tokens": 10}},
        _resp(content='{"a": "on", "reason": "ok"}', finish="stop"),
    ])
    monkeypatch.setattr("core.llm.llm_adapter.time.sleep", lambda s: None)
    adapter = LLMAdapter({"api_key": "k"})
    out = adapter.call(_ctx())
    assert out.commands == {"a": "on"}
    assert len(calls) == 2  # retried via the normal path, no escalation


def test_reasoning_effort_sent_only_when_configured(stub_requests):
    calls, queue = stub_requests
    queue.append(_resp(content='{"a": "on", "reason": "ok"}', finish="stop"))
    LLMAdapter({"api_key": "k"}).call(_ctx())
    assert "reasoning_effort" not in calls[0]

    queue.append(_resp(content='{"a": "on", "reason": "ok"}', finish="stop"))
    LLMAdapter({"api_key": "k", "reasoning_effort": "low"}).call(_ctx())
    assert calls[1]["reasoning_effort"] == "low"


def test_freetext_cutoff_raises_without_retry(stub_requests):
    calls, queue = stub_requests
    queue.extend([_resp(content=None, finish="length", reasoning=5)] * 3)
    adapter = LLMAdapter({"api_key": "k"})
    with pytest.raises(LLMOutputCutoffError):
        adapter.call_freetext(_ctx())
    assert len(calls) == 3
    assert "response_format" not in calls[0]
