"""
RootcauseAnalyzer — post-mortem LLM analysis when a session ends badly.
Triggered automatically when PLANT_DIED event is logged,
or manually via: muse-iot analyze <session_id>

Pulls all sensor data, events, and LLM decisions for the full session
and sends them to the LLM for structured diagnosis.
"""

import json
import logging
import time
from datetime import datetime

from .history.ihistory_store import IHistoryStore, Event
from .llm.llm_adapter import LLMAdapter

log = logging.getLogger("muse-iot.rootcause")


class RootcauseAnalyzer:

    def __init__(self, history: IHistoryStore, llm: LLMAdapter, domain=None):
        self.history = history
        self.llm     = llm
        self.domain  = domain  # optional domain plugin (framing + sensor ids)

    def analyze(self, box_id: str, session_id: str) -> dict:
        """
        Run post-mortem analysis on a completed session.
        Returns structured diagnosis dict.
        """
        log.info(f"Starting rootcause analysis for session {session_id}")
        now = time.time()

        # Session time range — scope everything below to THIS session,
        # not the box's full history across sessions.
        start_ts, end_ts = self._session_range(box_id, session_id, now)

        # 1. Sensor statistics for the session window
        window = max(int(now - start_ts), 60)
        stats = self._bare_only(self.history.sensor_minmax(box_id, window=window))
        avgs  = self._bare_only(self.history.sensor_avg(box_id, window=window))
        trend = self._bare_only(self.history.sensor_trend(box_id, window=window))

        # 2. Session events — anomalies, overrides, observations, actuations
        events = [e for e in self.history.events(box_id)
                  if start_ts <= e.ts <= end_ts]
        anomalies   = [e for e in events if e.type == "SENSOR_ANOMALY"]
        overrides   = [e for e in events if e.type == "SAFETY_OVERRIDE"]
        observations= [e for e in events if e.type == "OBSERVATION"]
        errors      = [e for e in events if e.severity == "critical"]

        # 3. Last 20 LLM decisions within the session
        decisions = [d for d in self.history.llm_decisions(box_id, limit=200)
                     if start_ts <= d.ts <= end_ts][:20]

        # 4. Build post-mortem prompt (domain-aware framing)
        llm_cfg = getattr(self.domain, "llm", None)
        llm_cfg = llm_cfg if isinstance(llm_cfg, dict) else {}
        role = llm_cfg.get("analyst_role", "IoT systems expert")
        article = "an" if role[:1].lower() in "aeiou" else "a"
        sess_cfg = getattr(self.domain, "session", None)
        noun = (sess_cfg.get("concept", "session").replace("_", " ")
                if isinstance(sess_cfg, dict) else "session")

        prompt_system = (
            f"You are {article} {role}.\n"
            f"You are analyzing a completed {noun} to identify root causes "
            f"of failure or success.\n"
            "Be specific, quantitative, and actionable.\n"
            "Respond ONLY in valid JSON."
        )

        prompt_user = f"""
{noun.upper()} ANALYSIS REQUEST

Sensor statistics (this session):
{json.dumps({"averages": avgs, "ranges": stats, "trends": trend}, indent=2)}

Anomaly events ({len(anomalies)} total):
{self._format_events(anomalies[:10])}

Safety overrides ({len(overrides)} total — LLM blocked by safety engine):
{self._format_events(overrides[:10])}

Critical errors ({len(errors)} total):
{self._format_events(errors[:5])}

User observations:
{self._format_events(observations)}

Last 20 LLM decisions this session:
{self._format_decisions(decisions)}

Provide diagnosis as JSON:
{{
  "primary_cause": "most likely root cause in one sentence",
  "contributing_factors": [
    {{"factor": "...", "evidence": "...", "severity": "high|medium|low"}}
  ],
  "sensor_issues": ["list any sensor data that looks suspicious"],
  "llm_decision_quality": "assessment of whether LLM decisions were appropriate",
  "safety_engine_notes": "were safety overrides appropriate or too aggressive?",
  "recommendations": [
    "specific actionable change for the next {noun}"
  ],
  "confidence": "high|medium|low",
  "overall_assessment": "2-3 sentence summary"
}}"""

        # 5. Call LLM for diagnosis
        try:
            response = self.llm.call({
                "system": prompt_system,
                "user":   prompt_user,
            })
            diagnosis = response.commands  # LLMAdapter parses JSON
            diagnosis["reason"] = response.reason
        except Exception as e:
            log.error(f"Rootcause LLM call failed: {e}")
            diagnosis = {"error": str(e), "primary_cause": "Analysis failed — check logs"}

        # 6. Store rootcause in session record
        self.history.conn.execute(
            "UPDATE sessions SET rootcause=? WHERE id=?",
            (json.dumps(diagnosis), session_id)
        ) if hasattr(self.history, "conn") else None

        log.info(f"Rootcause analysis complete: {diagnosis.get('primary_cause')}")
        return diagnosis

    def _session_range(self, box_id: str, session_id: str, now: float) -> tuple:
        """(start_ts, end_ts) for the session; falls back to all history."""
        if hasattr(self.history, "conn"):
            try:
                row = self.history.conn.execute(
                    "SELECT started_at, ended_at FROM sessions WHERE id=? AND box_id=?",
                    (session_id, box_id),
                ).fetchone()
            except Exception as e:
                log.warning(f"Could not load session {session_id}: {e}")
                row = None
            if row:
                return (row["started_at"] or 0, row["ended_at"] or now)
            log.warning(f"Session {session_id} not found — analyzing full history")
        return (0, now)

    def _bare_only(self, stats: dict) -> dict:
        """Drop aggregate/per-node keys. Without a domain, drop *_min/*_max only."""
        if self.domain is not None:
            bare = {s["id"] for s in self.domain.sensors}
            return {k: v for k, v in stats.items() if k in bare}
        return {k: v for k, v in stats.items()
                if not k.endswith(("_min", "_max"))}

    def _format_events(self, events: list) -> str:
        if not events:
            return "  (none)"
        lines = []
        for e in events:
            ts = datetime.fromtimestamp(e.ts).strftime("%m-%d %H:%M")
            line = f"  [{ts}] {e.type}"
            if e.sensor_id:
                line += f" {e.sensor_id}={e.value}"
            if e.note:
                line += f" — {e.note}"
            lines.append(line)
        return "\n".join(lines)

    def _format_decisions(self, decisions: list) -> str:
        if not decisions:
            return "  (none)"
        lines = []
        for d in decisions:
            ts = datetime.fromtimestamp(d.ts).strftime("%m-%d %H:%M")
            override = " [OVERRIDDEN]" if d.was_overridden else ""
            lines.append(
                f"  [{ts}]{override} {json.dumps(d.commands)} — {d.reason}"
            )
        return "\n".join(lines)
