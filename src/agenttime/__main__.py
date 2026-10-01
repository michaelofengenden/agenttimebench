"""Command-line entry point for offline study planning."""

import argparse
import json
from pathlib import Path
import sys

from .plan import build_plan, format_plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AgentTime v1.1 offline planning")
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="count study slots without launching anything")
    plan.add_argument("--config-dir", type=Path, default=Path("configs"))
    plan.add_argument("--agent", action="append", help="agent ID; repeat for several, omit for all")
    plan.add_argument("--json", action="store_true", help="print the machine-readable plan")
    args = parser.parse_args(argv)
    try:
        result = build_plan(args.config_dir, args.agent)
    except (OSError, ValueError) as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2) if args.json else format_plan(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
