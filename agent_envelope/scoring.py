"""Scoring engine: evaluates trajectory against envelope bounds."""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum

from agent_envelope.envelope import Envelope
from agent_envelope.tracker import TrajectoryEvent, TrajectoryTracker
from agent_envelope.workflows import WorkflowMatcher, WorkflowPattern, MatchResult, unknown_workflow_score
from agent_envelope.dataflow import DataFlowTracker


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
    workflow_match: MatchResult | None = None

    @property
    def should_block(self) -> bool:
        return self.decision in (Decision.PAUSE, Decision.KILL)


class ScoringEngine:
    def __init__(self, envelope: Envelope):
        self.envelope = envelope
        # Build workflow matcher from envelope definitions
        patterns = []
        for wf in envelope.workflows:
            patterns.append(WorkflowPattern(
                name=wf.get("name", "unnamed"),
                steps=wf.get("steps", []),
                max_steps=wf.get("max_steps", 20),
            ))
        self.workflow_matcher = WorkflowMatcher(patterns)
        # Session-level data flow tracker
        self.flow_tracker = DataFlowTracker(envelope.forbidden_flows)

    def evaluate(self, tracker: TrajectoryTracker, event: TrajectoryEvent) -> EvalResult:
        violations = []

        # === Budget checks ===

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

        # === Structural checks ===

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

        # === Data flow checks (Phase 2: session-level) ===

        # Per-event forbidden flows (Phase 1 behavior preserved)
        for flow in self.envelope.forbidden_flows:
            if flow.from_source in event.data_sources_read:
                for dest in event.data_destinations:
                    if dest in flow.to_destinations:
                        violations.append(Violation(
                            rule="forbidden_flow",
                            message=f"Forbidden data flow: {flow.from_source} → {dest}",
                            severity=0.95,
                        ))

        # Session-level data flow: catches cross-action exfiltration
        flow_violations = self.flow_tracker.record(event)
        for fv in flow_violations:
            # Only add if not already caught by per-event check above
            msg = f"Session data flow: {fv.source} (read step {fv.read_at_step}) → {fv.destination} (written step {fv.written_at_step})"
            if not any(v.rule == "forbidden_flow" and fv.source in v.message for v in violations):
                violations.append(Violation(
                    rule="session_flow",
                    message=msg,
                    severity=0.95,
                ))

        # === Workflow matching (Phase 2) ===

        # Build trajectory including current event for matching
        all_events = list(tracker.events) + [event]
        workflow_match = self.workflow_matcher.match(all_events)

        # Check if trajectory is off-pattern
        wf_drift = unknown_workflow_score(workflow_match, self.envelope.unknown_workflow_threshold)
        if wf_drift > 0:
            violations.append(Violation(
                rule="workflow_drift",
                message=f"Trajectory doesn't match any declared workflow (best: {workflow_match.workflow_name or 'none'}, confidence: {workflow_match.confidence:.2f})",
                severity=wf_drift,
            ))

        # === Compute final drift score (weighted, not just max) ===

        if not violations:
            drift_score = 0.0
        elif len(violations) == 1:
            drift_score = violations[0].severity
        else:
            # Weighted: max severity dominates, but multiple violations compound
            sorted_severities = sorted((v.severity for v in violations), reverse=True)
            # Primary = max, secondary violations add 10% each (capped at 1.0)
            drift_score = min(1.0, sorted_severities[0] + sum(s * 0.1 for s in sorted_severities[1:]))

        # Determine decision from thresholds
        decision = Decision.ALLOW
        if drift_score >= self.envelope.responses.get("kill", 0.8):
            decision = Decision.KILL
        elif drift_score >= self.envelope.responses.get("pause", 0.6):
            decision = Decision.PAUSE
        elif drift_score >= self.envelope.responses.get("warn", 0.3):
            decision = Decision.WARN

        return EvalResult(decision=decision, drift_score=drift_score, violations=violations, workflow_match=workflow_match)
