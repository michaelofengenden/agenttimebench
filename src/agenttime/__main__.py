"""Command-line entry point for offline study planning."""

import argparse
import json
import os
from pathlib import Path
import sys

from .plan import build_plan, format_plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AgentTime v1.1 planning and model-free fixture execution")
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="count study slots without launching anything")
    plan.add_argument("--config-dir", type=Path, default=Path("configs"))
    plan.add_argument("--agent", action="append", help="agent ID; repeat for several, omit for all")
    plan.add_argument("--json", action="store_true", help="print the machine-readable plan")
    for command in ("fixture", "harbor-fixture"):
        fixture = commands.add_parser(command, help="model-free local qualification; cannot launch study agents")
        actions = fixture.add_subparsers(dest="action", required=True)
        for action in ("init", "run", "tick", "status", "worker"):
            child = actions.add_parser(action)
            child.add_argument("--dsn-env", default="AGENTTIME_DSN", help="environment variable holding PostgreSQL connection information")
            if action == "worker":
                child.add_argument("--attempt", required=True)
            else:
                child.add_argument("--campaign", required=True)
            if action == "init":
                child.add_argument("--tasks", type=Path, required=True, help="JSON array of disposable fixture tasks")
                child.add_argument("--root", type=Path, required=True)
                child.add_argument("--capacity", type=int, default=4)
                child.add_argument("--agent-id", default=f"{command}-agent-v1")
        if command == "fixture":
            demo = actions.add_parser("demo", help="create an isolated PostgreSQL instance and run disposable fixtures")
            demo.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command in {"fixture", "harbor-fixture"}:
        return fixture_command(args)
    try:
        result = build_plan(args.config_dir, args.agent)
    except (OSError, ValueError) as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2) if args.json else format_plan(result))
    return 0


def fixture_command(args):
    from .fixture import build_manifest, run_campaign, run_worker, status, tick
    from .ledger import Ledger
    from .jsonio import loads_strict
    import psycopg
    if args.command == "harbor-fixture":
        from .harbor_worker import build_manifest, run_worker
    try:
        if args.action == "demo":
            from .demo import run_demo
            result = run_demo(args.root)
        else:
            dsn = os.environ.get(args.dsn_env)
            if not dsn:
                raise ValueError(f"Set {args.dsn_env} to a disposable fixture PostgreSQL database")
            ledger = Ledger(dsn)
            if args.action != "init":
                identity = ledger.attempt(args.attempt)['campaign_id'] if args.action == "worker" else args.campaign
                if ledger.campaign(identity)['manifest']['executor'] != args.command + '-v1':
                    raise ValueError('CLI executor differs from the frozen campaign')
            if args.action == "worker":
                run_worker(ledger, args.attempt)
                return 0
            if args.action == "init":
                tasks = loads_strict(args.tasks.read_text())
                manifest = build_manifest(args.root, tasks, args.agent_id)
                ledger.initialize(args.capacity)
                ledger.create_campaign(args.campaign, manifest)
                result = status(ledger, args.campaign)
            else:
                operation = {"run": run_campaign, "tick": tick, "status": status}[args.action]
                result = operation(ledger, args.campaign)
    except (OSError, ValueError, psycopg.Error) as error:
        print(f"Fixture error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 3 if args.action in {"run", "demo"} and result["finished"] != result["total"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
