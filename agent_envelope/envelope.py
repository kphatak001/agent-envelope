"""Envelope definition loading and validation."""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import yaml


@dataclass
class Bounds:
    max_actions: int = 100
    max_tokens: int = 500000
    max_duration_seconds: float = 600
    max_cost_usd: float = 10.0
    max_actions_per_minute: float = 30
    max_identical_calls: int = 3
    max_similar_calls: int = 10


@dataclass
class ForbiddenFlow:
    from_source: str
    to_destinations: list[str]


@dataclass
class Envelope:
    name: str
    purpose: str = ""
    bounds: Bounds = field(default_factory=Bounds)
    forbidden_flows: list[ForbiddenFlow] = field(default_factory=list)
    requires_human_approval: list[str] = field(default_factory=list)
    max_chain_depth: int = 5
    responses: dict[str, float] = field(default_factory=lambda: {
        "warn": 0.3, "pause": 0.6, "kill": 0.8
    })

    def validate(self) -> list[str]:
        errors = []
        if not self.name:
            errors.append("envelope must have a name")
        if self.bounds.max_actions < 1:
            errors.append("max_actions must be >= 1")
        if self.bounds.max_duration_seconds <= 0:
            errors.append("max_duration_seconds must be > 0")
        return errors


def load_envelope(path: str | Path) -> Envelope:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Envelope not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    bounds_raw = raw.get("bounds", {})
    bounds = Bounds(
        max_actions=bounds_raw.get("max_actions_per_session", 100),
        max_tokens=bounds_raw.get("max_tokens_consumed", 500000),
        max_duration_seconds=bounds_raw.get("max_duration_seconds", 600),
        max_cost_usd=bounds_raw.get("max_cost_usd", 10.0),
        max_actions_per_minute=bounds_raw.get("max_actions_per_minute",
                                              raw.get("drift", {}).get("velocity", {}).get("max_actions_per_minute", 30)),
        max_identical_calls=bounds_raw.get("max_identical_calls",
                                           raw.get("drift", {}).get("repetition", {}).get("max_identical_calls", 3)),
        max_similar_calls=bounds_raw.get("max_similar_calls",
                                         raw.get("drift", {}).get("repetition", {}).get("max_similar_calls", 10)),
    )

    forbidden_flows = []
    for flow in bounds_raw.get("data_flow", {}).get("forbidden_flows", []):
        forbidden_flows.append(ForbiddenFlow(
            from_source=flow["from"],
            to_destinations=flow["to"] if isinstance(flow["to"], list) else [flow["to"]],
        ))

    autonomy = bounds_raw.get("autonomy", {})
    responses_raw = raw.get("responses", {})
    thresholds = {}
    for level in ("warn", "pause", "kill"):
        if level in responses_raw:
            thresholds[level] = responses_raw[level].get("threshold", {"warn": 0.3, "pause": 0.6, "kill": 0.8}[level])
        else:
            thresholds[level] = {"warn": 0.3, "pause": 0.6, "kill": 0.8}[level]

    return Envelope(
        name=raw.get("name", path.stem),
        purpose=raw.get("purpose", ""),
        bounds=bounds,
        forbidden_flows=forbidden_flows,
        requires_human_approval=autonomy.get("requires_human_approval", []),
        max_chain_depth=autonomy.get("max_chain_depth", 5),
        responses=thresholds,
    )
