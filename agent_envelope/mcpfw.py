"""Bidirectional integration with mcpfw.

Direction 1 (mcpfw → envelope): Ingest mcpfw audit logs as trajectory events.
Direction 2 (envelope → mcpfw): Export envelope constraints as mcpfw policy YAML.
Direction 3 (envelope → mcpfw): Kill signal that mcpfw enforces (block all calls).
"""

from __future__ import annotations
import json
from pathlib import Path

import yaml

from agent_envelope.envelope import Envelope, load_envelope
from agent_envelope.tracker import TrajectoryEvent
from agent_envelope.session import EnvelopeSession


# === Direction 1: mcpfw audit → envelope trajectory ===

def ingest_mcpfw_audit(session: EnvelopeSession, audit_path: str | Path) -> None:
    """Feed mcpfw audit log events into an envelope session for scoring.
    
    Use case: you already have mcpfw running. Point agent-envelope at its
    audit log to get session-level behavioral analysis on top of per-call policy.
    """
    with open(audit_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("event") != "tool_call":
                continue

            session.check(
                tool_name=record.get("tool", "unknown"),
                arguments=record.get("arguments", {}),
            )


def mcpfw_event_to_trajectory(record: dict) -> TrajectoryEvent | None:
    """Convert a single mcpfw audit record to a TrajectoryEvent.
    
    Use case: real-time streaming from mcpfw into envelope scoring.
    """
    if record.get("event") != "tool_call":
        return None

    import time
    return TrajectoryEvent(
        timestamp=record.get("timestamp", time.time()),
        action_type="tool_call",
        tool_name=record.get("tool", "unknown"),
        arguments=record.get("arguments", {}),
    )


# === Direction 2: envelope → mcpfw policy ===

def export_mcpfw_policy(envelope: Envelope | str | Path, output: str | Path | None = None) -> str:
    """Generate a mcpfw policy YAML from an envelope definition.
    
    Extracts the subset of envelope constraints that can be expressed as
    per-call mcpfw rules: forbidden paths, rate limits, budgets.
    Workflow-level and data-flow constraints stay in the envelope layer.
    """
    if isinstance(envelope, (str, Path)):
        envelope = load_envelope(envelope)

    rules = []

    # Session budget → mcpfw budget rule
    rules.append({
        "name": "envelope_budget",
        "action": "budget",
        "max_calls": envelope.bounds.max_actions,
        "max_per_tool": envelope.bounds.max_similar_calls,
        "message": f"Envelope budget: {envelope.bounds.max_actions} calls, {envelope.bounds.max_similar_calls}/tool",
    })

    # Rate limit from velocity bounds
    rules.append({
        "action": "rate_limit",
        "tools": ["*"],
        "rate": f"{int(envelope.bounds.max_actions_per_minute)}/minute",
    })

    # Human approval requirements → mcpfw ask rules
    if envelope.requires_human_approval:
        rules.append({
            "name": "envelope_approval",
            "action": "ask",
            "tools": envelope.requires_human_approval,
            "message": "Envelope requires human approval for this action",
        })

    # Default allow (envelope handles the behavioral layer)
    rules.append({
        "action": "allow",
        "tools": ["*"],
    })

    policy = {
        "name": f"{envelope.name}-envelope-generated",
        "rules": rules,
    }

    policy_yaml = yaml.dump(policy, default_flow_style=False, sort_keys=False)

    if output:
        Path(output).write_text(policy_yaml)

    return policy_yaml


# === Direction 3: envelope kill → mcpfw block-all ===

KILL_POLICY = """name: envelope-kill
default_action: deny
rules:
  - action: deny
    tools: ["*"]
    message: "agent-envelope KILL: session terminated"
"""


def write_kill_policy(path: str | Path) -> None:
    """Write a mcpfw policy that blocks everything.
    
    Use case: when agent-envelope issues a KILL decision, write this policy
    to the mcpfw policy path. mcpfw hot-reloads and blocks all subsequent calls.
    
    Integration pattern:
        mcpfw --policy /tmp/agent-policy.yaml --watch -- server
        
    When envelope kills:
        write_kill_policy("/tmp/agent-policy.yaml")
        # mcpfw detects file change, reloads, blocks everything
    """
    Path(path).write_text(KILL_POLICY)


# === Convenience: combined session that feeds mcpfw events ===

class McpfwEnvelopeSession(EnvelopeSession):
    """EnvelopeSession that also watches a mcpfw policy file for kill propagation.
    
    Usage:
        session = McpfwEnvelopeSession(
            envelope="envelopes/support-agent.yaml",
            mcpfw_policy_path="/tmp/agent-policy.yaml",
        )
    """

    def __init__(self, envelope, mcpfw_policy_path: str | Path | None = None, **kwargs):
        super().__init__(envelope, **kwargs)
        self._mcpfw_policy_path = Path(mcpfw_policy_path) if mcpfw_policy_path else None

    def check(self, *args, **kwargs):
        result = super().check(*args, **kwargs)

        # If killed, propagate to mcpfw
        if self.is_killed and self._mcpfw_policy_path:
            write_kill_policy(self._mcpfw_policy_path)

        return result
