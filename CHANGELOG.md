# Changelog

## 0.3.1 (2026-05-14)

### mcpfw HTTP Proxy Integration (Network-Enforced)

- agent-envelope now runs inside mcpfw's HTTP proxy as the session-level enforcement engine
- Cross-action data flow detection works at the network layer (agent can't bypass)
- Demo at `mcpfw/demo/run_demo.sh` shows envelope catching exfiltration that per-call policy misses
- This is the architecture the greenfield product would use: network-enforced, not in-process

## 0.3.0 (2026-05-13)

### mcpfw Bidirectional Integration

**New features:**
- **Ingest mcpfw audit logs** — Feed mcpfw's JSONL audit output into an envelope session for session-level behavioral analysis on top of per-call policy.
- **Export envelope as mcpfw policy** — Generate mcpfw-compatible YAML (budgets, rate limits, approval gates) from envelope definitions.
- **Kill signal propagation** — When envelope issues KILL, writes a deny-all policy that mcpfw hot-reloads, blocking all subsequent calls instantly.
- **McpfwEnvelopeSession** — Combined session class that auto-propagates kills to mcpfw policy file.

**New module:**
- `agent_envelope.mcpfw` — All integration functions in one place.

**Tests:** 34 total (7 new), all passing.

## 0.2.0 (2026-05-13)

### Phase 2: Workflow Matching + Session Data Flow

**New features:**
- **Workflow pattern matching** — Define expected agent workflows as step sequences with glob patterns. The engine uses subsequence alignment to detect when an agent's trajectory diverges from declared patterns.
- **Cross-action data flow tracking** — Detects exfiltration where data read in step N is written to a forbidden destination in step N+M, even with many allowed actions in between. This is the key differentiator over per-call enforcement.
- **Weighted drift scoring** — Multiple violations now compound (primary severity + 10% per additional violation, capped at 1.0) instead of using max-only.
- **Workflow match metadata** — `EvalResult.workflow_match` exposes the best-matching workflow name and confidence score.

**Envelope schema additions:**
- `workflows` — List of `{name, steps, max_steps}` defining expected behavior patterns
- `drift.unknown_workflow_threshold` — Number of off-pattern actions before drift alert fires

**New modules:**
- `agent_envelope.workflows` — WorkflowMatcher with glob-based subsequence alignment
- `agent_envelope.dataflow` — DataFlowTracker for session-level information flow analysis

**Tests:** 27 total (15 new), all passing.

## 0.1.0 (2026-05-13)

### Phase 1: Budget Enforcement + Loop Detection

**Core features:**
- Envelope YAML definition (purpose, bounds, forbidden flows, thresholds)
- Trajectory tracker with typed event recording
- Scoring engine: budget (actions, tokens, cost, duration), velocity spikes, repetition (identical + similar), chain depth, forbidden data flows
- Graduated response: ALLOW → WARN → PAUSE → KILL
- Kill propagation (session stays dead after kill)
- Immutable JSONL audit trail

**CLI:**
- `agent-envelope validate` — Check envelope definition
- `agent-envelope run` — Pipe-mode enforcement for any MCP process
- `agent-envelope score` — Forensic scoring of past sessions

**Bundled envelopes:**
- `support-agent.yaml` — Customer support with data flow restrictions
- `coding-agent.yaml` — Locked-down coding agent

**Tests:** 12 total, all passing.
