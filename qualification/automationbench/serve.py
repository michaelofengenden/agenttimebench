#!/usr/bin/env python3
"""Model-free local service demo. This deliberately cannot dispatch an evaluated agent."""

import argparse
import json
from pathlib import Path

from agenttime.automationbench.backend import Backend
from agenttime.automationbench.native import NativeBindings
from agenttime.automationbench.prompts import canonical
from agenttime.automationbench.service import MCPService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-free-fixture", action="store_true", required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--private-task-json", type=Path, required=True)
    parser.add_argument("--private-output", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Choose an unprivileged local fixture port")
    output = args.private_output.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    backend = Backend(
        NativeBindings(args.source_root),
        json.loads(args.private_task_json.read_text()),
        args.attempt_id,
    )
    backend.release()
    service = MCPService(backend)
    import uvicorn

    try:
        uvicorn.run(
            service.http_app(), host="127.0.0.1", port=args.port, log_level="warning"
        )
    finally:
        backend.seal()
        (output / "backend-checkpoint.json").write_bytes(backend.diagnostic_export())
        (output / "grade.json").write_bytes(canonical(backend.grade()))
        (output / "qualification.json").write_bytes(
            canonical(
                {
                    "study_launch_ready": False,
                    "native_harness_qualified": False,
                    "native_timing_qualified": False,
                    "production_controller_qualified": False,
                    "archive_qualified": False,
                    "scope": "standalone_model_free_tool_service",
                    "termination": "fixture_service_shutdown",
                }
            )
        )


if __name__ == "__main__":
    main()
