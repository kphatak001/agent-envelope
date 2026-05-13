"""Data flow analysis: tracks information movement across the full session.

Catches exfiltration patterns where data read in one action is written
in a later action, even if the two actions are separated by many steps.
"""

from __future__ import annotations
from dataclasses import dataclass, field

from agent_envelope.envelope import ForbiddenFlow
from agent_envelope.tracker import TrajectoryEvent


@dataclass
class FlowViolation:
    source: str
    destination: str
    read_at_step: int
    written_at_step: int


class DataFlowTracker:
    """Tracks data sources read across the session and detects forbidden flows.
    
    Key insight: if an agent reads "customer_account" at step 2, and writes to
    "email_external" at step 7, that's a forbidden flow even though 5 allowed
    actions happened in between. Per-call enforcement misses this because it
    only sees step 7 in isolation.
    """

    def __init__(self, forbidden_flows: list[ForbiddenFlow]):
        self.forbidden_flows = forbidden_flows
        self._sources_read: dict[str, int] = {}  # source -> first step read
        self._step = 0

    def record(self, event: TrajectoryEvent) -> list[FlowViolation]:
        """Record an event and return any flow violations detected."""
        self._step += 1
        violations = []

        # Track all sources read in this event
        for source in event.data_sources_read:
            if source not in self._sources_read:
                self._sources_read[source] = self._step

        # Check if any destination in this event violates a flow rule
        # based on sources read ANYWHERE in the session (not just this event)
        for dest in event.data_destinations:
            for flow in self.forbidden_flows:
                if flow.from_source in self._sources_read and dest in flow.to_destinations:
                    violations.append(FlowViolation(
                        source=flow.from_source,
                        destination=dest,
                        read_at_step=self._sources_read[flow.from_source],
                        written_at_step=self._step,
                    ))

        return violations

    @property
    def sources_accessed(self) -> set[str]:
        """All data sources read during this session."""
        return set(self._sources_read.keys())

    @property
    def tainted_sources(self) -> dict[str, int]:
        """Sources that have forbidden destinations, with step they were read."""
        tainted = {}
        for flow in self.forbidden_flows:
            if flow.from_source in self._sources_read:
                tainted[flow.from_source] = self._sources_read[flow.from_source]
        return tainted
