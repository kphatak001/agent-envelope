"""Tests for mcpfw bidirectional integration."""

import json
from pathlib import Path

import pytest

from agent_envelope.envelope import load_envelope
from agent_envelope.session import EnvelopeSession
from agent_envelope.scoring import Decision
from agent_envelope.mcpfw import (
    ingest_mcpfw_audit,
    mcpfw_event_to_trajectory,
    export_mcpfw_policy,
    write_kill_policy,
    McpfwEnvelopeSession,
    KILL_POLICY,
)


ENVELOPE_PATH = Path(__file__).parent.parent / "envelopes" / "support-agent.yaml"


def test_mcpfw_event_to_trajectory():
    record = {"event": "tool_call", "tool": "read_file", "arguments": {"path": "/tmp/x"}, "timestamp": 1713700000}
    event = mcpfw_event_to_trajectory(record)
    assert event is not None
    assert event.tool_name == "read_file"
    assert event.arguments == {"path": "/tmp/x"}


def test_mcpfw_event_skips_non_tool_calls():
    record = {"event": "response_blocked", "pattern": "ignore previous"}
    assert mcpfw_event_to_trajectory(record) is None


def test_ingest_mcpfw_audit(tmp_path):
    audit = tmp_path / "mcpfw-audit.jsonl"
    lines = [
        json.dumps({"event": "tool_call", "tool": "search_kb", "arguments": {"q": "test"}, "decision": "allow"}),
        json.dumps({"event": "tool_call", "tool": "read_file", "arguments": {"path": "/data"}, "decision": "allow"}),
        json.dumps({"event": "response_blocked", "pattern": "ignore"}),  # skipped
    ]
    audit.write_text("\n".join(lines))

    with EnvelopeSession(ENVELOPE_PATH) as session:
        ingest_mcpfw_audit(session, audit)
        assert session.tracker.total_actions == 2


def test_export_mcpfw_policy():
    env = load_envelope(ENVELOPE_PATH)
    policy_yaml = export_mcpfw_policy(env)

    assert "envelope_budget" in policy_yaml
    assert "rate_limit" in policy_yaml
    assert "50" in policy_yaml  # max_actions
    assert "20/minute" in policy_yaml  # velocity


def test_export_mcpfw_policy_to_file(tmp_path):
    out = tmp_path / "generated.yaml"
    export_mcpfw_policy(ENVELOPE_PATH, output=out)
    assert out.exists()
    assert "envelope_budget" in out.read_text()


def test_write_kill_policy(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    write_kill_policy(policy_path)
    content = policy_path.read_text()
    assert "default_action: deny" in content
    assert "envelope-kill" in content


def test_mcpfw_envelope_session_propagates_kill(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text("name: normal\nrules: []")

    env = load_envelope(ENVELOPE_PATH)
    env.bounds.max_actions = 2  # will kill on 3rd

    session = McpfwEnvelopeSession(env, mcpfw_policy_path=policy_path)
    session.__enter__()

    session.check("a")
    session.check("b")
    session.check("c")  # triggers kill

    assert session.is_killed
    # Kill policy should have been written
    content = policy_path.read_text()
    assert "default_action: deny" in content
    assert "envelope-kill" in content

    session.__exit__(None, None, None)
