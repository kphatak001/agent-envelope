"""CLI for agent-envelope."""

from __future__ import annotations
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from agent_envelope.envelope import load_envelope
from agent_envelope.session import EnvelopeSession
from agent_envelope.scoring import Decision


def cmd_validate(args):
    """Validate an envelope definition."""
    try:
        env = load_envelope(args.envelope)
    except Exception as e:
        print(f"❌ Parse error: {e}", file=sys.stderr)
        sys.exit(1)

    errors = env.validate()
    if errors:
        for err in errors:
            print(f"❌ {err}", file=sys.stderr)
        sys.exit(1)

    print(f"✅ {env.name}")
    print(f"   Purpose: {env.purpose or '(not set)'}")
    print(f"   Budget: {env.bounds.max_actions} actions, {env.bounds.max_tokens} tokens, ${env.bounds.max_cost_usd}, {env.bounds.max_duration_seconds}s")
    print(f"   Velocity: {env.bounds.max_actions_per_minute}/min")
    print(f"   Loops: {env.bounds.max_identical_calls} identical, {env.bounds.max_similar_calls} similar")
    print(f"   Chain depth: {env.max_chain_depth}")
    print(f"   Forbidden flows: {len(env.forbidden_flows)}")
    print(f"   Thresholds: warn={env.responses['warn']}, pause={env.responses['pause']}, kill={env.responses['kill']}")


def cmd_run(args):
    """Run a command under envelope enforcement (stdin/stdout pipe mode)."""
    env = load_envelope(args.envelope)
    audit_path = args.audit_log

    # Start the wrapped process
    cmd = args.command
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True,
    )

    with EnvelopeSession(env, audit_log=audit_path) as session:
        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue

                # Parse JSON-RPC tool call
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    proc.stdin.write(line + "\n")
                    proc.stdin.flush()
                    continue

                # Extract tool info if it's a tool call
                tool_name = ""
                arguments = {}
                if msg.get("method") == "tools/call":
                    params = msg.get("params", {})
                    tool_name = params.get("name", "")
                    arguments = params.get("arguments", {})

                    # Check envelope
                    result = session.check(tool_name, arguments)

                    if result.should_block:
                        # Return error to agent
                        error_resp = {
                            "jsonrpc": "2.0",
                            "id": msg.get("id"),
                            "error": {
                                "code": -32000,
                                "message": f"[agent-envelope] {result.decision.value}: "
                                           + "; ".join(v.message for v in result.violations),
                            },
                        }
                        sys.stdout.write(json.dumps(error_resp) + "\n")
                        sys.stdout.flush()

                        if result.decision == Decision.KILL:
                            print(f"🛑 KILL: {result.violations[0].message if result.violations else 'envelope violated'}", file=sys.stderr)
                            proc.terminate()
                            sys.exit(1)

                        # PAUSE: print warning, skip this call
                        print(f"⏸️  PAUSE: {result.violations[0].message if result.violations else ''}", file=sys.stderr)
                        continue

                    if result.decision == Decision.WARN:
                        print(f"⚠️  WARN: {result.violations[0].message if result.violations else ''}", file=sys.stderr)

                # Forward to wrapped process
                proc.stdin.write(line + "\n")
                proc.stdin.flush()

                # Read response and forward
                resp_line = proc.stdout.readline()
                if resp_line:
                    sys.stdout.write(resp_line)
                    sys.stdout.flush()

        except (BrokenPipeError, KeyboardInterrupt):
            pass
        finally:
            proc.terminate()
            proc.wait()


def cmd_score(args):
    """Score a session from an audit log (forensics mode)."""
    env = load_envelope(args.envelope)
    audit_path = Path(args.audit_log)

    if not audit_path.exists():
        print(f"❌ Audit log not found: {audit_path}", file=sys.stderr)
        sys.exit(1)

    session = EnvelopeSession(env)
    session.__enter__()

    events = []
    with open(audit_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("event") == "action":
                events.append(record)

    print(f"📊 Scoring {len(events)} actions against envelope '{env.name}'")
    print(f"{'─' * 60}")

    max_drift = 0.0
    kills = 0
    pauses = 0
    warns = 0

    for record in events:
        tool = record.get("tool", "unknown")
        result = session.check(tool)
        max_drift = max(max_drift, result.drift_score)
        if result.decision == Decision.KILL:
            kills += 1
        elif result.decision == Decision.PAUSE:
            pauses += 1
        elif result.decision == Decision.WARN:
            warns += 1

    print(f"  Actions:    {len(events)}")
    print(f"  Max drift:  {max_drift:.3f}")
    print(f"  Kills:      {kills}")
    print(f"  Pauses:     {pauses}")
    print(f"  Warnings:   {warns}")
    print(f"  Verdict:    {'🛑 WOULD KILL' if kills else '⚠️  WOULD WARN' if warns else '✅ CLEAN'}")


def main():
    parser = argparse.ArgumentParser(
        prog="agent-envelope",
        description="Runtime behavioral envelope enforcement for AI agents",
    )
    sub = parser.add_subparsers(dest="cmd")

    # validate
    p_val = sub.add_parser("validate", help="Validate an envelope definition")
    p_val.add_argument("envelope", help="Path to envelope YAML")

    # run
    p_run = sub.add_parser("run", help="Run a command under envelope enforcement")
    p_run.add_argument("--envelope", "-e", required=True, help="Path to envelope YAML")
    p_run.add_argument("--audit-log", "-l", help="Path to audit log (JSONL)")
    p_run.add_argument("command", nargs=argparse.REMAINDER, help="Command to wrap")

    # score
    p_score = sub.add_parser("score", help="Score a past session from audit log")
    p_score.add_argument("--envelope", "-e", required=True, help="Path to envelope YAML")
    p_score.add_argument("audit_log", help="Path to audit log (JSONL)")

    args = parser.parse_args()

    if args.cmd == "validate":
        cmd_validate(args)
    elif args.cmd == "run":
        cmd_run(args)
    elif args.cmd == "score":
        cmd_score(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
