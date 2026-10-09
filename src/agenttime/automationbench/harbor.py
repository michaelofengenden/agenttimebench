"""Public Harbor task configuration, without private sources, state or grading data."""

import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from . import SOURCE_COMMIT, VARIANT
from .prompts import adapt_prompt, canonical


def agent_route(name):
    if name not in {"codex", "claude-code"}:
        raise ValueError("Unsupported native harness; no API fallback")
    return {
        "name": name,
        "qualification_status": "pending",
        "study_launch_ready": False,
    }


def materialize_task(directory, messages, *, mcp_url, image):
    parsed = urlsplit(mcp_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path != "/mcp"
    ):
        raise ValueError("Require a dedicated MCP service URL without secrets")
    if not re.fullmatch(r"[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64}", image):
        raise ValueError("Agent environment image must be digest pinned")
    prompt = adapt_prompt(messages)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "instruction.md").write_text(prompt["text"])

    def quote(value):
        return json.dumps(value, ensure_ascii=False)

    task = 'schema_version = "1.4"\n[environment]\n'
    task += f"docker_image = {quote(image)}\ncpus = 1\nmemory_mb = 1024\n"
    task += '[[environment.mcp_servers]]\nname = "automationbench"\ntransport = "streamable-http"\n'
    task += f"url = {quote(mcp_url)}\n"
    (directory / "task.toml").write_text(task)
    receipt = {
        "schema": 1,
        "source_commit": SOURCE_COMMIT,
        "variant": VARIANT,
        "toolset": "api",
        "prompt_sha256": prompt["delivered_text_sha256"],
        "harbor_version": "0.23.0",
        "supported_harness_candidates": ["codex", "claude-code"],
        "study_launch_ready": False,
        "native_harness_qualified": False,
        "native_timing_qualified": False,
        "production_controller_qualified": False,
        "archive_qualified": False,
        "private_backend_network_isolation_qualified": False,
        "note": "Configuration only. Requires a separate private backend, owner sealing, native verifier integration and qualified agent route.",
    }
    (directory / "adapter-qualification.json").write_bytes(canonical(receipt))
    return receipt
