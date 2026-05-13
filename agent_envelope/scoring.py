"""Scoring engine: evaluates trajectory against envelope bounds."""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum

from agent_envelope.envelope import Envelope
from agent_envelope.tracker import TrajectoryEvent, TrajectoryTracker


class Decision(Enum):
    ALLOW = "allow"
    WARN = "warn"
    PAUSE = "pause"
    KILL = "kill"


@dataclass
class Violation:
    rule: str
    message: str
    severity: float  # 0.0 - 1.0


@dataclass
class EvalResult:
    decision: Decision
    drift_score: float
    violations: list[Violation]

    @property
    def should_block(self) -> bool:
        return self.decision in (Decision.PAUSE, Decision.KILL)


class ScoringEngine:
    def __init__(self, envelope: Envelope):
        self.envelope = envelope

    def evaluate(self, tracker: TrajectoryTracker, event: TrajectoryEvent) -> EvalResult:
        violations = []

        # Budget: total actions (including this one)
        action_count = tracker.total_actions + 1
        if action_count > self.envelope.bounds.max_actions:
            violations.append(Violation(
                rule="budget_actions",
                message=f"Action budget exceeded: {action_count}/{self.envelope.bounds.max_actions}",
                severity=1.0,
            ))

        # Budget: tokens (including this event)
        total_tokens = tracker.total_tokens + event.tokens_consumed
        if total_tokens > self.envelope.bounds.max_tokens:
            violations.append(Violation(
                rule="budget_tokens",
                message=f"Token budget exceeded: {total_tokens}/{self.envelope.bounds.max_tokens}",
                severity=1.0,
            ))

        # Budget: cost (including this event)
        total_cost = tracker.total_cost + event.cost_usd
        if total_cost > self.envelope.bounds.max_cost_usd:
            violations.append(Violation(
                rule="budget_cost",
                message=f"Cost budget exceeded: ${total_cost:.2f}/${self.envelope.bounds.max_cost_usd:.2f}",
                severity=1.0,
            ))

        # Budget: duration
        if tracker.elapsed_seconds >= self.envelope.bounds.max_duration_seconds:
            violations.append(Violation(
                rule="budget_duration",
                message=f"Duration exceeded: {tracker.elapsed_seconds:.0f}s/{self.envelope.bounds.max_duration_seconds:.0f}s",
                severity=1.0,
            ))

        # Chain depth
        if event.chain_depth > self.envelope.max_chain_depth:
            violations.append(Violation(
                rule="chain_depth",
                message=f"Chain depth {event.chain_depth} exceeds max {self.envelope.max_chain_depth}",
                severity=0.9,
            ))

        # Velocity: actions per minute
        actions_last_60s = tracker.actions_in_last_seconds(60)
        if actions_last_60s > self.envelope.bounds.max_actions_per_minute:
            violations.append(Violation(
                rule="velocity",
                message=f"Velocity spike: {actions_last_60s} actions/min (max {self.envelope.bounds.max_actions_per_minute})",
                severity=0.7,
            ))

        # Repetition: identical calls (including this one)
        identical = tracker.identical_call_count(event) + 1
        if identical >= self.envelope.bounds.max_identical_calls:
            violations.append(Violation(
                rule="repetition_identical",
                message=f"Identical call repeated {identical}x (max {self.envelope.bounds.max_identical_calls}): {event.tool_name}",
                severity=0.85,
            ))

        # Repetition: same tool (including this one)
        similar = tracker.similar_tool_count(event.tool_name) + 1
        if similar >= self.envelope.bounds.max_similar_calls:
            violations.append(Violation(
                rule="repetition_similar",
                message=f"Tool '{event.tool_name}' called {similar}x (max {self.envelope.bounds.max_similar_calls})",
                severity=0.7,
            ))

        # Forbidden data flows
        for flow in self.envelope.forbidden_flows:
            if flow.from_source in event.data_sources_read:
                for dest in event.data_destinations:
                    if dest in flow.to_destinations:
                        violations.append(Violation(
                            rule="forbidden_flow",
                            message=f"Forbidden data flow: {flow.from_source} → {dest}",
                            severity=0.95,
                        ))

        # Compute drift score (max violation severity)
        drift_score = max((v.severity for v in violations), default=0.0)

        # Determine decision from thresholds
        decision = Decision.ALLOW
        if drift_score >= self.envelope.responses.get("kill", 0.8):
            decision = Decision.KILL
        elif drift_score >= self.envelope.responses.get("pause", 0.6):
            decision = Decision.PAUSE
        elif drift_score >= self.envelope.responses.get("warn", 0.3):
            decision = Decision.WARN

        return EvalResult(decision=decision, drift_score=drift_score, violations=violations)
