"""EnvelopeSession: the main runtime interface."""

from __future__ import annotations
import json
import sys
import time
from pathlib import Path

from agent_envelope.envelope import Envelope, load_envelope
from agent_envelope.tracker import TrajectoryEvent, TrajectoryTracker
from agent_envelope.scoring import ScoringEngine, EvalResult, Decision


class EnvelopeSession:
    def __init__(self, envelope: Envelope | str | Path, audit_log: str | Path | None = None):
        if isinstance(envelope, (str, Path)):
            envelope = load_envelope(envelope)
        self.envelope = envelope
        self.tracker = TrajectoryTracker()
        self.engine = ScoringEngine(envelope)
        self._audit_path = Path(audit_log) if audit_log else None
        self._audit_file = None
        self._killed = False

    def __enter__(self):
        if self._audit_path:
            self._audit_file = open(self._audit_path, "a")
        self._emit_event("session_start", {"envelope": self.envelope.name})
        return self

    def __exit__(self, *_):
        self._emit_event("session_end", {
            "actions": self.tracker.total_actions,
            "tokens": self.tracker.total_tokens,
            "cost_usd": self.tracker.total_cost,
            "duration_seconds": self.tracker.elapsed_seconds,
            "killed": self._killed,
        })
        if self._audit_file:
            self._audit_file.close()
        return False

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, *args):
        return self.__exit__(*args)

    @property
    def is_killed(self) -> bool:
        return self._killed

    def check(self, tool_name: str, arguments: dict | None = None, *,
              tokens: int = 0, cost: float = 0.0,
              data_read: list[str] | None = None,
              data_write: list[str] | None = None,
              chain_depth: int = 0) -> EvalResult:
        """Check if an action is within the envelope. Call before executing."""
        if self._killed:
            return EvalResult(
                decision=Decision.KILL,
                drift_score=1.0,
                violations=[],
            )

        event = TrajectoryEvent(
            timestamp=time.time(),
            action_type="tool_call",
            tool_name=tool_name,
            arguments=arguments or {},
            data_sources_read=data_read or [],
            data_destinations=data_write or [],
            tokens_consumed=tokens,
            cost_usd=cost,
            chain_depth=chain_depth,
        )

        result = self.engine.evaluate(self.tracker, event)

        # Record the event regardless of decision
        self.tracker.record(event)

        # Handle kill
        if result.decision == Decision.KILL:
            self._killed = True

        # Audit
        self._emit_event("action", {
            "tool": tool_name,
            "decision": result.decision.value,
            "drift_score": round(result.drift_score, 3),
            "violations": [{"rule": v.rule, "message": v.message} for v in result.violations],
        })

        return result

    @property
    def drift_score(self) -> float:
        """Current session drift score (last evaluation)."""
        if not self.tracker.events:
            return 0.0
        last = self.tracker.events[-1]
        return self.engine.evaluate(self.tracker, last).drift_score

    @property
    def budget_remaining(self) -> dict:
        return {
            "actions": self.envelope.bounds.max_actions - self.tracker.total_actions,
            "tokens": self.envelope.bounds.max_tokens - self.tracker.total_tokens,
            "cost_usd": round(self.envelope.bounds.max_cost_usd - self.tracker.total_cost, 4),
            "seconds": round(self.envelope.bounds.max_duration_seconds - self.tracker.elapsed_seconds, 1),
        }

    def _emit_event(self, event_type: str, data: dict) -> None:
        record = {"event": event_type, "timestamp": time.time(), **data}
        if self._audit_file:
            self._audit_file.write(json.dumps(record) + "\n")
            self._audit_file.flush()
