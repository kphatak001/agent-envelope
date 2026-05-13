"""Workflow pattern matching: detect when agent trajectory diverges from declared workflows."""

from __future__ import annotations
from dataclasses import dataclass
import re

from agent_envelope.tracker import TrajectoryEvent


@dataclass
class WorkflowPattern:
    name: str
    steps: list[str]  # glob patterns like "search_*", "read_file", "send_*"
    max_steps: int = 20


@dataclass
class MatchResult:
    workflow_name: str | None
    confidence: float  # 0.0 = no match, 1.0 = perfect match
    matched_steps: int
    total_steps: int


class WorkflowMatcher:
    """Matches agent trajectory against declared workflow patterns.
    
    Uses subsequence matching: the trajectory must contain the workflow steps
    in order, but can have other actions interspersed. Confidence is the ratio
    of matched steps to total trajectory length (penalizes off-pattern actions).
    """

    def __init__(self, workflows: list[WorkflowPattern]):
        self.workflows = workflows

    def match(self, trajectory: list[TrajectoryEvent]) -> MatchResult:
        """Find the best-matching workflow for the current trajectory."""
        if not self.workflows or not trajectory:
            return MatchResult(workflow_name=None, confidence=0.0, matched_steps=0, total_steps=len(trajectory))

        best = MatchResult(workflow_name=None, confidence=0.0, matched_steps=0, total_steps=len(trajectory))

        for wf in self.workflows:
            result = self._match_workflow(wf, trajectory)
            if result.confidence > best.confidence:
                best = result

        return best

    def _match_workflow(self, workflow: WorkflowPattern, trajectory: list[TrajectoryEvent]) -> MatchResult:
        """Match a single workflow against the trajectory using subsequence alignment."""
        if not workflow.steps:
            return MatchResult(workflow_name=workflow.name, confidence=0.0, matched_steps=0, total_steps=len(trajectory))

        # Find longest subsequence of trajectory that matches workflow steps in order
        step_idx = 0
        matched = 0

        for event in trajectory:
            if step_idx >= len(workflow.steps):
                break
            if self._step_matches(workflow.steps[step_idx], event.tool_name):
                matched += 1
                step_idx += 1

        # Confidence: how much of the workflow we've matched so far,
        # penalized by extra actions not in the pattern
        if matched == 0:
            confidence = 0.0
        else:
            # Coverage: what fraction of expected steps have we seen?
            coverage = matched / len(workflow.steps)
            # Efficiency: what fraction of actions were on-pattern?
            efficiency = matched / len(trajectory) if trajectory else 0.0
            # Combined score (weighted toward coverage)
            confidence = 0.7 * coverage + 0.3 * efficiency

        # Penalize if trajectory exceeds max_steps
        if len(trajectory) > workflow.max_steps:
            confidence *= 0.5

        return MatchResult(
            workflow_name=workflow.name,
            confidence=round(confidence, 3),
            matched_steps=matched,
            total_steps=len(trajectory),
        )

    def _step_matches(self, pattern: str, tool_name: str) -> bool:
        """Match a workflow step pattern against a tool name. Supports glob-style wildcards."""
        regex = re.escape(pattern).replace(r"\*", ".*")
        return bool(re.fullmatch(regex, tool_name))


def unknown_workflow_score(match_result: MatchResult, threshold: int) -> float:
    """Returns a drift severity based on how far off-pattern the trajectory is.
    
    If the best workflow match confidence is below a threshold after N actions,
    the agent is likely off-script.
    """
    if match_result.total_steps < threshold:
        return 0.0  # Too early to judge

    if match_result.confidence >= 0.6:
        return 0.0  # Good match, no drift

    if match_result.confidence >= 0.3:
        return 0.4  # Partial match, mild drift

    return 0.65  # Poor match, significant drift
