# agent-envelope

Runtime behavioral envelope enforcement for AI agents. Detects when an agent's overall trajectory deviates from its declared purpose, even when every individual action is "allowed."

```
Agent ──actions──▶ agent-envelope ──(if allowed)──▶ mcpfw ──▶ MCP Server
                        │
                   Envelope Engine
                   (trajectory tracking,
                    budget enforcement,
                    loop detection,
                    data flow analysis)
```

## Why

[mcpfw](https://github.com/kphatak001/mcpfw) enforces per-call policy: "block write_file to ~/.ssh". But attacks increasingly look like sequences of individually-allowed actions:

1. Agent reads customer database ✅ (allowed)
2. Agent formats data as CSV ✅ (allowed)
3. Agent sends email externally ✅ (allowed)

Each step passes. But the **trajectory** is data exfiltration.

**agent-envelope catches this.** It tracks what the agent is *doing* against what it's *supposed to be doing*.

## Install

```bash
pip install agent-envelope
```

## Quick Start

### Define an envelope

```yaml
# envelopes/support-agent.yaml
name: support-agent
purpose: "Answer customer questions using knowledge base"

bounds:
  max_actions_per_session: 50
  max_tokens_consumed: 100000
  max_duration_seconds: 300
  max_cost_usd: 1.00

  data_flow:
    forbidden_flows:
      - from: "customer_account"
        to: ["email_external", "file_export"]

  autonomy:
    max_chain_depth: 3

drift:
  repetition:
    max_identical_calls: 3
    max_similar_calls: 10
```

### Use in code

```python
from agent_envelope import EnvelopeSession

with EnvelopeSession("envelopes/support-agent.yaml") as session:
    # Check each action before executing
    result = session.check("search_kb", {"query": "password reset"})
    if not result.should_block:
        # execute the tool call
        ...

    # Forbidden flow → KILL
    result = session.check("send_email",
        data_read=["customer_account"],
        data_write=["email_external"])
    # result.decision == Decision.KILL
```

### CLI

```bash
# Validate envelope
agent-envelope validate envelopes/support-agent.yaml

# Run a process under envelope enforcement (pipe mode)
agent-envelope run -e envelopes/support-agent.yaml -- python my_agent.py

# Score a past session
agent-envelope score -e envelopes/support-agent.yaml audit.jsonl
```

## What It Enforces

| Check | What it catches | Response |
|-------|----------------|----------|
| Action budget | Runaway agents consuming resources | KILL |
| Token/cost budget | Cost amplification attacks | KILL |
| Duration limit | Infinite execution | KILL |
| Identical call repetition | Infinite loops | KILL |
| Similar call repetition | Subtle loops | WARN |
| Velocity spike | Sudden burst of activity | WARN |
| Chain depth | Unbounded sub-agent spawning | KILL |
| Forbidden data flows | Exfiltration via allowed actions | KILL |

## Graduated Response

| Drift Score | Decision | Action |
|-------------|----------|--------|
| 0.0 - 0.3 | ALLOW | Continue normally |
| 0.3 - 0.6 | WARN | Log warning, continue |
| 0.6 - 0.8 | PAUSE | Halt, request human review |
| 0.8 - 1.0 | KILL | Terminate, preserve forensics |

## Audit Trail

Every session produces JSONL:

```jsonl
{"event":"session_start","envelope":"support-agent","timestamp":1713700000}
{"event":"action","tool":"search_kb","decision":"allow","drift_score":0.0}
{"event":"action","tool":"send_email","decision":"kill","drift_score":0.95,"violations":[{"rule":"forbidden_flow","message":"Forbidden data flow: customer_account → email_external"}]}
{"event":"session_end","actions":2,"killed":true}
```

## The Trilogy

| Layer | Tool | Question |
|-------|------|----------|
| Pre-deploy | [agentspec](https://github.com/kphatak001/agentspec) | "Is this agent config risky?" |
| Runtime (session) | **agent-envelope** | "Is this agent off-script?" |
| Runtime (per-call) | [mcpfw](https://github.com/kphatak001/mcpfw) | "Is this specific call allowed?" |

## Regulatory Alignment

- **EU AI Act Art 72**: Post-market monitoring via continuous drift detection
- **Singapore MGF**: Kill switch + plan logging
- **DORA**: 4-hour reconstruction via immutable audit trail
- **NIST Cyber AI Profile**: Behavioral baselines + anomaly detection

## License

Apache-2.0
