"""Tests for agent-envelope Phase 2: workflow matching, session data flow, weighted scoring."""

import time
from pathlib import Path

import pytest

from agent_envelope.envelope import load_envelope, Envelope, Bounds, ForbiddenFlow
from agent_envelope.session import EnvelopeSession
from agent_envelope.scoring import Decision
from agent_envelope.workflows import WorkflowMatcher, WorkflowPattern, MatchResult, unknown_workflow_score
from agent_envelope.dataflow import DataFlowTracker
from agent_envelope.tracker import TrajectoryEvent


ENVELOPE_PATH = Path(__file__).parent.parent / "envelopes" / "support-agent.yaml"


# --- Workflow Matcher Tests ---

def test_workflow_matcher_perfect_match():
    patterns = [WorkflowPattern(name="search_and_reply", steps=["search_kb", "format_response", "send_reply"])]
    matcher = WorkflowMatcher(patterns)

    events = [
        TrajectoryEvent(timestamp=time.time(), action_type="tool_call", tool_name="search_kb"),
        TrajectoryEvent(timestamp=time.time(), action_type="tool_call", tool_name="format_response"),
        TrajectoryEvent(timestamp=time.time(), action_type="tool_call", tool_name="send_reply"),
    ]

    result = matcher.match(events)
    assert result.workflow_name == "search_and_reply"
    assert result.confidence > 0.8


def test_workflow_matcher_with_extra_steps():
    patterns = [WorkflowPattern(name="search_and_reply", steps=["search_kb", "send_reply"])]
    matcher = WorkflowMatcher(patterns)

    events = [
        TrajectoryEvent(timestamp=time.time(), action_type="tool_call", tool_name="search_kb"),
        TrajectoryEvent(timestamp=time.time(), action_type="tool_call", tool_name="log_something"),
        TrajectoryEvent(timestamp=time.time(), action_type="tool_call", tool_name="send_reply"),
    ]

    result = matcher.match(events)
    assert result.workflow_name == "search_and_reply"
    assert result.confidence > 0.5  # Still matches but lower confidence


def test_workflow_matcher_no_match():
    patterns = [WorkflowPattern(name="search_and_reply", steps=["search_kb", "send_reply"])]
    matcher = WorkflowMatcher(patterns)

    events = [
        TrajectoryEvent(timestamp=time.time(), action_type="tool_call", tool_name="delete_file"),
        TrajectoryEvent(timestamp=time.time(), action_type="tool_call", tool_name="exec_shell"),
        TrajectoryEvent(timestamp=time.time(), action_type="tool_call", tool_name="send_email"),
    ]

    result = matcher.match(events)
    assert result.confidence == 0.0


def test_workflow_matcher_glob_patterns():
    patterns = [WorkflowPattern(name="read_and_write", steps=["read_*", "write_*"])]
    matcher = WorkflowMatcher(patterns)

    events = [
        TrajectoryEvent(timestamp=time.time(), action_type="tool_call", tool_name="read_file"),
        TrajectoryEvent(timestamp=time.time(), action_type="tool_call", tool_name="write_file"),
    ]

    result = matcher.match(events)
    assert result.workflow_name == "read_and_write"
    assert result.confidence > 0.8


def test_workflow_matcher_best_of_multiple():
    patterns = [
        WorkflowPattern(name="search_flow", steps=["search_kb", "format_response"]),
        WorkflowPattern(name="escalate_flow", steps=["classify_intent", "create_ticket"]),
    ]
    matcher = WorkflowMatcher(patterns)

    events = [
        TrajectoryEvent(timestamp=time.time(), action_type="tool_call", tool_name="classify_intent"),
        TrajectoryEvent(timestamp=time.time(), action_type="tool_call", tool_name="create_ticket"),
    ]

    result = matcher.match(events)
    assert result.workflow_name == "escalate_flow"


def test_unknown_workflow_score_early():
    """Too few actions to judge."""
    result = MatchResult(workflow_name=None, confidence=0.0, matched_steps=0, total_steps=2)
    assert unknown_workflow_score(result, threshold=3) == 0.0


def test_unknown_workflow_score_drifting():
    """Enough actions, poor match."""
    result = MatchResult(workflow_name="search", confidence=0.1, matched_steps=1, total_steps=5)
    assert unknown_workflow_score(result, threshold=3) == 0.65


# --- Data Flow Tracker Tests ---

def test_dataflow_immediate_violation():
    """Read and write in same event."""
    tracker = DataFlowTracker([ForbiddenFlow(from_source="secrets", to_destinations=["external"])])
    event = TrajectoryEvent(
        timestamp=time.time(), action_type="tool_call", tool_name="send",
        data_sources_read=["secrets"], data_destinations=["external"],
    )
    violations = tracker.record(event)
    assert len(violations) == 1
    assert violations[0].source == "secrets"


def test_dataflow_cross_action_violation():
    """Read in step 1, write in step 3 — the key Phase 2 feature."""
    tracker = DataFlowTracker([ForbiddenFlow(from_source="customer_db", to_destinations=["email_external"])])

    # Step 1: read customer data
    e1 = TrajectoryEvent(timestamp=time.time(), action_type="tool_call", tool_name="query_db",
                         data_sources_read=["customer_db"])
    assert tracker.record(e1) == []

    # Step 2: innocent action
    e2 = TrajectoryEvent(timestamp=time.time(), action_type="tool_call", tool_name="format_csv")
    assert tracker.record(e2) == []

    # Step 3: write to external — should catch the flow from step 1
    e3 = TrajectoryEvent(timestamp=time.time(), action_type="tool_call", tool_name="send_email",
                         data_destinations=["email_external"])
    violations = tracker.record(e3)
    assert len(violations) == 1
    assert violations[0].source == "customer_db"
    assert violations[0].read_at_step == 1
    assert violations[0].written_at_step == 3


def test_dataflow_no_violation_without_read():
    """Writing to forbidden destination is fine if source was never read."""
    tracker = DataFlowTracker([ForbiddenFlow(from_source="customer_db", to_destinations=["email_external"])])

    event = TrajectoryEvent(timestamp=time.time(), action_type="tool_call", tool_name="send_email",
                            data_destinations=["email_external"])
    assert tracker.record(event) == []


def test_dataflow_tainted_sources():
    tracker = DataFlowTracker([ForbiddenFlow(from_source="secrets", to_destinations=["external"])])
    e = TrajectoryEvent(timestamp=time.time(), action_type="tool_call", tool_name="read",
                        data_sources_read=["secrets"])
    tracker.record(e)
    assert "secrets" in tracker.tainted_sources


# --- Integration: Session with Workflows ---

def test_session_workflow_match_on_pattern():
    env = load_envelope(ENVELOPE_PATH)
    with EnvelopeSession(env) as session:
        session.check("search_kb", {"q": "reset password"})
        session.check("read_account", {"id": "123"})
        result = session.check("format_response", {"template": "answer"})
        # Should match "answer_question" workflow
        assert result.workflow_match is not None
        assert result.workflow_match.confidence > 0.4


def test_session_workflow_drift_on_unknown_pattern():
    env = load_envelope(ENVELOPE_PATH)
    env.unknown_workflow_threshold = 3

    with EnvelopeSession(env) as session:
        session.check("delete_database")
        session.check("exec_shell")
        session.check("upload_to_s3")
        result = session.check("send_to_attacker")
        # None of these match any workflow — should trigger drift
        assert any(v.rule == "workflow_drift" for v in result.violations)


def test_session_cross_action_flow_detection():
    """The killer feature: catches exfiltration across multiple steps."""
    env = load_envelope(ENVELOPE_PATH)

    with EnvelopeSession(env) as session:
        # Step 1: read customer data (allowed)
        r1 = session.check("read_account", data_read=["customer_account"])
        assert r1.decision == Decision.ALLOW

        # Step 2: format data (allowed)
        r2 = session.check("format_csv")
        assert r2.decision == Decision.ALLOW

        # Step 3: send externally — session flow tracker catches this
        r3 = session.check("send_email", data_write=["email_external"])
        assert r3.decision == Decision.KILL
        assert any(v.rule == "session_flow" for v in r3.violations)


def test_weighted_scoring_compounds():
    """Multiple violations compound the drift score."""
    env = load_envelope(ENVELOPE_PATH)
    env.bounds.max_similar_calls = 2  # trigger similar (0.7)

    with EnvelopeSession(env) as session:
        session.check("search_kb", {"q": "a"})
        # Second call triggers similar (0.7) + potentially workflow drift
        result = session.check("search_kb", {"q": "b"})
        # With compounding, drift should be > 0.7 if multiple violations fire
        if len(result.violations) > 1:
            assert result.drift_score > result.violations[0].severity
