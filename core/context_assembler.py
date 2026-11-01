"""
ContextAssembler — builds the full LLM prompt every poll cycle.

Maps to OpenClaw's context assembly:
  OpenClaw: AGENTS.md + SOUL.md + skills + memory search + session history
  Muse-IoT: domain.yaml + prompt.md + live sensors + history windows + decisions

System message: static identity, safety rules, output schema  (~600 tokens)
User message:   live sensors + 3-horizon history + last 5 decisions (~800 tokens)
"""

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("muse-iot.context_assembler")

TOKEN_BUDGET = 2800  # conservative; leaves ~1200 tokens for LLM response


def estimate_tokens(text: str) -> int:
    """Rough estimate: 1 token ≈ 4 characters."""
    return len(text) // 4


class ContextAssembler:

    def __init__(self, domain, session_state, history_store, device, reflection_engine=None):
        self.domain      = domain
        self.session     = session_state
        self.history     = history_store
        self.device      = device
        self.reflection  = reflection_engine

    def build(self) -> dict:
        """Returns {"system": str, "user": str} ready for LLM adapter."""
        return {
            "system": self._build_system(),
            "user":   self._build_user(),
        }

    # ── SYSTEM — static per domain ────────────────────────────────────────

    def _build_system(self) -> str:
        parts = []

        # 1. Domain identity + full profile context
        identity = (
            f"You are an autonomous IoT controller.\n"
            f"Domain  : {self.domain.name}\n"
            f"Profile : {self.session.profile_id}\n"
            f"Box ID  : {self.session.box_id}\n"
            f"Day     : {self.session.day_number()} "
            f"(phase: {self.session.current_phase()})"
        )
        profile_summary = self.session.profile_summary()
        if profile_summary:
            identity += f"\n\nPROFILE:\n{profile_summary}"
        parts.append(identity)

        # 2. Active sensors only — OpenClaw principle: inject only what's registered
        sensor_lines = "\n".join(
            f"  - {s['id']}: {s.get('name', s['id'])}"
            for s in self.domain.sensors
            if s.get("required", False) or self._sensor_connected(s["id"])
        )
        parts.append(f"SENSORS:\n{sensor_lines}")

        # 3. Available actuators
        actuator_lines = "\n".join(
            f"  - {a['id']}: {a.get('name', a['id'])}"
            for a in self.domain.actuators
        )
        parts.append(f"ACTUATORS:\n{actuator_lines}")

        # 4. Safety rules — always in system, never trimmed, highest precedence
        safety_lines = "\n".join(
            f"  RULE: {r}" for r in self.domain.safety
        )
        parts.append(
            "SAFETY RULES — HARD CONSTRAINTS, NEVER VIOLATE THESE:\n"
            f"{safety_lines}\n"
            "Precedence: safety rules override everything (a separate engine blocks "
            "violating commands, so comply with them directly) — then your expertise "
            "— then the advisory ranges in SESSION STATE."
        )

        # 5. Output schema — type-aware per actuator, JSON-safe placeholders only
        def _actuator_schema(aid: str) -> str:
            if aid.startswith("pwm_"):
                return f'  "{aid}": <integer 0-100>'
            return f'  "{aid}": "on" | "off"'

        schema_fields = ",\n".join(
            _actuator_schema(a["id"]) for a in self.domain.actuators
        )
        reason_hint = self._llm_cfg().get("reason_hint", "one specific sentence")
        parts.append(
            "Respond ONLY in valid JSON — no text outside the object.\n"
            "ALL fields are REQUIRED every response — include every actuator even if unchanged.\n"
            f"{{\n{schema_fields},\n  \"reason\": \"<{reason_hint}>\"\n}}"
        )

        # 6. prompt.md — domain author's custom instructions
        if self.domain.prompt_template_path.exists():
            parts.append(self.domain.prompt_template_path.read_text())

        # 7. Latest reflection — synthesized 48h insights (cache-friendly: stable for 6h)
        if self.reflection:
            latest = self.reflection.latest_reflection()
            if latest:
                parts.append(f"PERIODIC SYNTHESIS (last 48h):\n{latest}")

        return "\n\n".join(parts)

    # ── USER — rebuilt every poll cycle ──────────────────────────────────

    def _build_user(self) -> str:
        now = time.time()
        parts = []

        # 2a. Live sensor snapshot
        snapshot = self._read_sensors()
        snap_block = [
            f"CURRENT SENSORS (live at {datetime.now().strftime('%H:%M:%S')}):",
            json.dumps(snapshot, indent=2),
        ]
        units = {s["id"]: s["unit"] for s in self.domain.sensors if s.get("unit")}
        if units:
            snap_block.append(f"UNITS: {json.dumps(units)}")
        if any(v is None for v in snapshot.values()):
            snap_block.append(
                "Note: null = that sensor read failed right now (not zero). "
                "Decide from history/trends and say so in reason."
            )
        parts.append("\n".join(snap_block))

        # 2b. Three history horizons (bare sensor ids only)
        bare = self._bare_ids()
        h2h  = {k: v for k, v in self.history.sensor_avg(self.session.box_id, window=7_200).items() if k in bare}
        h24h = {k: v for k, v in self.history.sensor_minmax(self.session.box_id, window=86_400).items() if k in bare}
        h7d  = {k: v for k, v in self.history.sensor_trend(self.session.box_id, window=604_800).items() if k in bare}

        history_block = (
            f"HISTORY:\n"
            f"  2h avg    : {json.dumps(h2h)}\n"
            f"  24h range : {json.dumps(h24h)}\n"
            f"  7d trend  : {json.dumps(h7d)}"
        )
        parts.append(history_block)

        # 2c. Recent anomalies (last 48h)
        anomalies = self.history.events(
            self.session.box_id,
            type="SENSOR_ANOMALY",
            since=now - 172_800
        )
        if anomalies:
            lines = [
                f"  [{datetime.fromtimestamp(e.ts).strftime('%m-%d %H:%M')}] "
                f"{e.sensor_id}={e.value} — {e.note}"
                for e in anomalies[:5]
            ]
            parts.append("RECENT ANOMALIES:\n" + "\n".join(lines))

        # 2d. Last 5 LLM decisions — prevents re-watering 10min after watering
        decisions = self.history.llm_decisions(self.session.box_id, limit=5)
        if decisions:
            lines = [
                f"  [{datetime.fromtimestamp(d.ts).strftime('%H:%M')}] "
                f"{json.dumps(d.commands)} — {d.reason}"
                for d in decisions
            ]
            parts.append("LAST 5 DECISIONS:\n" + "\n".join(lines))

        # 2e. Session state — targets are advisory (weakest signal), not hard rules
        targets = self.session.current_phase_targets()
        parts.append(
            f"SESSION STATE:\n"
            f"  Phase         : {self.session.current_phase()} (day {self.session.day_number()})\n"
            f"  Advisory ranges (weakest signal — safety rules and your expertise take precedence): {json.dumps(targets)}\n"
            f"  Time now      : {datetime.now().strftime('%H:%M')} "
            f"{datetime.now().strftime('%A')}\n"
            f"  Last actuations: {json.dumps(self.session.last_actuation_times())}"
        )

        # 2f. User observations from OBSERVATIONS.md
        obs = self._load_observations()
        if obs:
            parts.append(f"USER OBSERVATIONS:\n{obs}")

        # 2g. Trim to token budget — drop oldest history first, keep live always
        return self._trim_to_budget("\n\n".join(parts))

    # ── Helpers ──────────────────────────────────────────────────────────

    def _llm_cfg(self) -> dict:
        """Optional per-domain prompt tuning from domain.yaml `llm:` block."""
        cfg = getattr(self.domain, "llm", None)
        return cfg if isinstance(cfg, dict) else {}

    def _bare_ids(self) -> set:
        """Bare sensor ids for this domain — excludes node_* and *_min/*_max keys."""
        return {s["id"] for s in self.domain.sensors}

    def _read_sensors(self) -> dict:
        snapshot = {}
        for sensor in self.domain.sensors:
            sid = sensor["id"]
            try:
                snapshot[sid] = self.device.read(sid)
            except Exception as e:
                snapshot[sid] = None
                log.warning(f"Could not read sensor {sid}: {e}")
        return snapshot

    def _sensor_connected(self, sensor_id: str) -> bool:
        try:
            self.device.read(sensor_id)
            return True
        except Exception:
            return False

    def _load_observations(self) -> Optional[str]:
        obs_path = Path.home() / ".muse-iot" / self.session.box_id / "OBSERVATIONS.md"
        if not obs_path.exists():
            return None
        lines = obs_path.read_text().strip().splitlines()
        # Return last 3 observations only
        return "\n".join(lines[-3:]) if lines else None

    def _trim_to_budget(self, text: str) -> str:
        """Gracefully trim to token budget: drop 7d first, then 24h, keep live always.

        Whatever is dropped is named in a marker line, so the model can tell
        "trimmed" apart from "none".
        """
        if estimate_tokens(text) <= TOKEN_BUDGET:
            return text

        dropped = []
        # Reserve room for the trim marker so the final text still fits.
        limit = TOKEN_BUDGET - 25

        # Drop 7d trend
        new = re.sub(r"  7d trend\s*:.*?\n", "", text)
        if new != text:
            dropped.append("7d trend")
            text = new
        if estimate_tokens(text) <= limit:
            log.debug("Context trimmed: dropped 7d trend")
            return text + self._trim_marker(dropped)

        # Drop 24h range
        new = re.sub(r"  24h range\s*:.*?\n", "", text)
        if new != text:
            dropped.append("24h range")
            text = new
        if estimate_tokens(text) <= limit:
            log.debug("Context trimmed: dropped 24h range")
            return text + self._trim_marker(dropped)

        # Drop anomalies section
        new = re.sub(r"RECENT ANOMALIES:.*?\n\n", "", text, flags=re.DOTALL)
        if new != text:
            dropped.append("recent anomalies")
            text = new
        log.warning("Context heavily trimmed — consider reducing poll frequency")
        return text + self._trim_marker(dropped)

    @staticmethod
    def _trim_marker(dropped: list) -> str:
        if not dropped:
            return ""
        return f"\n\n[context trimmed for length — dropped: {', '.join(dropped)}]"
