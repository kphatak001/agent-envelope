"""Trajectory event recording and analysis."""

from __future__ import annotations
from dataclasses import dataclass, field
from hashlib import sha256
import json
import time


@dataclass
class TrajectoryEvent:
    timestamp: float
    action_type: str  # "tool_call", "llm_invoke", "spawn_agent"
    tool_name: str = ""
    arguments: dict = field(default_factory=dict)
    data_sources_read: list[str] = field(default_factory=list)
    data_destinations: list[str] = field(default_factory=list)
    tokens_consumed: int = 0
    cost_usd: float = 0.0
    chain_depth: int = 0

    @property
    def arguments_hash(self) -> str:
        return sha256(json.dumps(self.arguments, sort_keys=True).encode()).hexdigest()[:16]

    @property
    def signature(self) -> str:
        return f"{self.tool_name}:{self.arguments_hash}"

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "action_type": self.action_type,
            "tool_name": self.tool_name,
            "arguments_hash": self.arguments_hash,
            "data_sources_read": self.data_sources_read,
            "data_destinations": self.data_destinations,
            "tokens_consumed": self.tokens_consumed,
            "cost_usd": self.cost_usd,
            "chain_depth": self.chain_depth,
        }


class TrajectoryTracker:
    def __init__(self):
        self.events: list[TrajectoryEvent] = []
        self._start_time = time.time()

    def record(self, event: TrajectoryEvent) -> None:
        self.events.append(event)

    @property
    def total_actions(self) -> int:
        return len(self.events)

    @property
    def total_tokens(self) -> int:
        return sum(e.tokens_consumed for e in self.events)

    @property
    def total_cost(self) -> float:
        return sum(e.cost_usd for e in self.events)

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self._start_time

    @property
    def max_chain_depth(self) -> int:
        return max((e.chain_depth for e in self.events), default=0)

    def actions_in_last_seconds(self, seconds: float) -> int:
        cutoff = time.time() - seconds
        return sum(1 for e in self.events if e.timestamp > cutoff)

    def identical_call_count(self, event: TrajectoryEvent) -> int:
        return sum(1 for e in self.events if e.signature == event.signature)

    def similar_tool_count(self, tool_name: str) -> int:
        return sum(1 for e in self.events if e.tool_name == tool_name)
