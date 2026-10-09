"""Container entry for synthetic native-session qualification only.

Stage native_session.py alongside this file or install the pinned agenttime
package. Preparation and baseline acknowledgement never invoke a model.
"""
import argparse
import json
import os
from pathlib import Path
import sys

try:
    from agenttime import native_session
except ModuleNotFoundError:
    import native_session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--root", required=True)
    prepare.add_argument("--contract", required=True)
    run = commands.add_parser("run")
    run.add_argument("--root", required=True)
    run.add_argument("--acknowledgement", required=True)
    run.add_argument("--binary", required=True)
    run.add_argument("--offline-base-url")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.operation == "prepare":
            path = Path(args.contract)
            if path.is_symlink(): raise native_session.NativeSessionError("unsafe_contract_path")
            report = native_session.prepare(args.root, json.loads(path.read_bytes()))
        else:
            token = sys.stdin.readline(32770).rstrip("\n")
            report = native_session.run_prepared(args.root, args.acknowledgement, args.binary, token,
                                                offline_base_url=args.offline_base_url)
        print(json.dumps(report, allow_nan=False), flush=True)
        return 0 if args.operation == "prepare" or report.get("qualified") else 1
    except native_session.NativeSessionError as exc:
        print(json.dumps({"error_code": str(exc), "study_ready": False}), flush=True)
        return 1
    except (OSError, ValueError):
        print(json.dumps({"error_code": "qualification_entry_failed", "study_ready": False}), flush=True)
        return 1


if __name__ == "__main__": raise SystemExit(main())
