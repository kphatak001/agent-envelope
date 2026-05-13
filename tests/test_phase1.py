"""Tests for agent-envelope Phase 1: budget, loops, scoring."""

import time
from pathlib import Path

import pytest

from agent_envelope.envelope import load_envelope
from agent_envelope.session import EnvelopeSession
from agent_envelope.scoring import Decision


ENVELOPE_PATH = Path(__file__).parent.parent / "envelopes" / "support-agent.yaml"


def test_load_envelope():
    env = load_envelope(ENVELOPE_PATH)
    assert env.name == "support-agent"
    assert env.bounds.max_actions == 50
    assert env.bounds.max_cost_usd == 1.00
    assert len(env.forbidden_flows) == 2


def test_validate_envelope():
    env = load_envelope(ENVELOPE_PATH)
    assert env.validate() == []


def test_session_allows_normal_calls():
    with EnvelopeSession(ENVELOPE_PATH) as session:
        result = session.check("search_kb", {"query": "how to reset password"})
        assert result.decision == Decision.ALLOW
        assert result.drift_score == 0.0


def test_budget_actions_kill():
    env = load_envelope(ENVELOPE_PATH)
    env.bounds.max_actions = 2  # kill on 3rd action (exceeds 2)

    with EnvelopeSession(env) as session:
        session.check("tool_a")
        session.check("tool_b")
        result = session.check("tool_c")
        assert result.decision == Decision.KILL
        assert session.is_killed


def test_budget_cost_kill():
    env = load_envelope(ENVELOPE_PATH)
    env.bounds.max_cost_usd = 0.10

    with EnvelopeSession(env) as session:
        session.check("expensive_call", cost=0.05)
        result = session.check("another_call", cost=0.06)
        assert result.decision == Decision.KILL


def test_repetition_identical_kills():
    env = load_envelope(ENVELOPE_PATH)
    env.bounds.max_identical_calls = 3

    with EnvelopeSession(env) as session:
        session.check("read_file", {"path": "/tmp/x"})
        session.check("read_file", {"path": "/tmp/x"})
        result = session.check("read_file", {"path": "/tmp/x"})
        assert result.decision == Decision.KILL


def test_repetition_similar_pauses():
    env = load_envelope(ENVELOPE_PATH)
    env.bounds.max_similar_calls = 5

    with EnvelopeSession(env) as session:
        for i in range(4):
            session.check("search_kb", {"query": f"query_{i}"})
        result = session.check("search_kb", {"query": "query_5"})
        assert result.decision == Decision.PAUSE  # severity 0.7 > pause threshold 0.6


def test_forbidden_flow_kills():
    with EnvelopeSession(ENVELOPE_PATH) as session:
        result = session.check(
            "send_email",
            {"to": "attacker@evil.com"},
            data_read=["customer_account"],
            data_write=["email_external"],
        )
        assert result.decision == Decision.KILL
        assert any(v.rule == "forbidden_flow" for v in result.violations)


def test_chain_depth_kills():
    env = load_envelope(ENVELOPE_PATH)
    env.max_chain_depth = 2

    with EnvelopeSession(env) as session:
        session.check("tool_a", chain_depth=1)
        result = session.check("tool_b", chain_depth=3)
        assert result.decision == Decision.KILL


def test_killed_session_stays_killed():
    env = load_envelope(ENVELOPE_PATH)
    env.bounds.max_actions = 2

    with EnvelopeSession(env) as session:
        session.check("a")
        session.check("b")  # kills
        result = session.check("c")  # already dead
        assert result.decision == Decision.KILL
        assert session.is_killed


def test_budget_remaining():
    env = load_envelope(ENVELOPE_PATH)
    with EnvelopeSession(env) as session:
        session.check("tool_a", tokens=1000, cost=0.05)
        remaining = session.budget_remaining
        assert remaining["actions"] == 49
        assert remaining["tokens"] == 99000
        assert remaining["cost_usd"] == 0.95


def test_audit_log(tmp_path):
    import json
    log_path = tmp_path / "audit.jsonl"

    with EnvelopeSession(ENVELOPE_PATH, audit_log=log_path) as session:
        session.check("search_kb", {"q": "test"})

    lines = log_path.read_text().strip().split("\n")
    assert len(lines) == 3  # session_start + action + session_end
    records = [json.loads(l) for l in lines]
    assert records[0]["event"] == "session_start"
    assert records[1]["event"] == "action"
    assert records[1]["decision"] == "allow"
    assert records[2]["event"] == "session_end"
