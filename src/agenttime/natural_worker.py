"""Pinned production worker for the frozen fifty-task natural pilot.

This module never reserves or dispatches work. Its controller must verify the
Docker identity, image/resources, qualification proof and independent archives.
Worker receipts do not prove container stop, restoration, grading or admission.
Authentication enters only through run's stdin and the native child's private
environment. No model client, endpoint override, retry or duration cap exists.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import threading
import time
import uuid

from . import native_session as ns


_SOURCE_PATH = Path(__file__).resolve()
_LOADED_SOURCE_SHA256 = ns._file_sha(_SOURCE_PATH)
_FAMILIES = {"gpqa": 12, "hle": 20, "browsecomp": 12, "assistant": 6}
_IMAGE_TASKS = {"hle-03", "hle-07", "hle-09", "hle-19"}
_BROWSER_ACTIONS = ("observe", "noop", "scroll", "fill", "select_option", "click", "press", "go_back", "goto", "send_msg_to_user")
ASSISTANT_TOOLS = tuple("mcp__assistantbench__" + name for name in _BROWSER_ACTIONS)
_BRIDGE_PATH = _SOURCE_PATH.with_name("assistantbench_bridge.py")
_WEB_TOOLS = ["Agent", "WebSearch", "WebFetch"]


def _fail(code):
    raise ns.NativeSessionError(code)


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value): _fail("invalid_digest")


def _text(value):
    if not isinstance(value, str) or not value.strip() or "\0" in value or len(value) > 2048: _fail("invalid_identity_text")


def _uuid(value):
    try:
        if str(uuid.UUID(value)) != value: raise ValueError
    except (ValueError, TypeError, AttributeError): _fail("invalid_execution_uuid")


def document_sha256(value):
    """Match the ledger's canonical JSON digest for identities and receipts."""
    return ns._sha(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode())


def _decode(data):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out: _fail("duplicate_json_key")
            out[key] = value
        return out
    try:
        value = json.loads(data, object_pairs_hook=pairs)
        document_sha256(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError): _fail("invalid_json")


def _read_bytes(path):
    path = Path(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode): _fail("unsafe_worker_file")
            with os.fdopen(descriptor, "rb", closefd=False) as stream: return stream.read()
        finally: os.close(descriptor)
    except OSError: _fail("unreadable_worker_file")


def _read_json(path):
    return _decode(_read_bytes(path))


def source_pins(family_id):
    if family_id not in _FAMILIES: _fail("unsupported_task_family")
    if ns._file_sha(_SOURCE_PATH) != _LOADED_SOURCE_SHA256: _fail("loaded_worker_source_changed")
    return {"worker_sha256": _LOADED_SOURCE_SHA256, "native_session_sha256": ns._source_pin(),
            "python_sha256": ns._file_sha(Path(sys.executable).resolve()),
            "bridge_sha256": ns._file_sha(_BRIDGE_PATH) if family_id == "assistant" else None}


def validate_spec(spec, input_bytes=None):
    keys = {"schema_version", "executor", "arm", "attempt_id", "task_id", "family_id", "session_id", "identity",
            "preparation_contract_sha256", "input_sha256", "model", "effort", "route", "tool_policy", "resources", "runtime_pins"}
    if not isinstance(spec, dict) or set(spec) != keys: _fail("natural_worker_spec_shape")
    if (spec["schema_version"], spec["executor"], spec["arm"]) != ("agenttime.natural-worker.v1", "claude-natural-v1", "natural"):
        _fail("not_natural_worker_spec")
    if (spec["model"], spec["effort"], spec["route"]) != (ns.CLI_MODEL, "max", "subscription"):
        _fail("natural_runtime_policy_mismatch")
    family = spec["family_id"]
    if not isinstance(family, str) or family not in _FAMILIES: _fail("unsupported_task_family")
    if spec["task_id"] not in {f"{family}-{i:02}" for i in range(1, _FAMILIES[family] + 1)}: _fail("task_outside_frozen_pilot")
    _uuid(spec["attempt_id"]); _uuid(spec["session_id"])
    identity = spec["identity"]
    if not isinstance(identity, dict) or set(identity) != {"worker_id", "session_id", "host_id", "daemon_id", "docker_endpoint", "container_id"}:
        _fail("worker_identity_shape")
    for value in identity.values(): _text(value)
    if identity["session_id"] != spec["session_id"]: _fail("worker_session_mismatch")
    _digest(identity["container_id"]); _digest(spec["preparation_contract_sha256"]); _digest(spec["input_sha256"])
    policy = {"native_tools": _WEB_TOOLS if family == "browsecomp" else [],
              "mcp_tools": list(ASSISTANT_TOOLS) if family == "assistant" else [], "native_subagents": family == "browsecomp"}
    if spec["tool_policy"] != policy or type(spec["tool_policy"].get("native_subagents")) is not bool: _fail("natural_tool_policy_mismatch")
    closed = family in {"gpqa", "hle"}
    resources = {"allocation_scope": "no_task_compute" if closed else "natural_subject",
                 "cpus": None if closed else 4, "memory_gib": None if closed else 16, "gpu_count": 0}
    if spec["resources"] != resources or type(spec["resources"].get("gpu_count")) is not int: _fail("natural_resource_policy_mismatch")
    if not closed and any(type(spec["resources"][name]) is not int for name in ("cpus", "memory_gib")):
        _fail("natural_resource_policy_mismatch")
    pins = spec["runtime_pins"]
    expected = {"cli_version": ns.CLI_VERSION, "cli_sha256": ns.CLI_SHA256, **source_pins(family)}
    if not isinstance(pins, dict) or set(pins) != set(expected) | {"image_sha256"}: _fail("natural_runtime_pin_shape")
    if any(pins.get(key) != value for key, value in expected.items()): _fail("natural_source_pin_mismatch")
    if not isinstance(pins["image_sha256"], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", pins["image_sha256"]): _fail("invalid_image_pin")
    if input_bytes is None: return
    if not isinstance(input_bytes, bytes) or not input_bytes.endswith(b"\n") or len(input_bytes.splitlines()) != 1:
        _fail("not_one_native_input_event")
    if ns._sha(input_bytes) != spec["input_sha256"]: _fail("natural_input_hash_mismatch")
    event = _decode(input_bytes)
    if (not isinstance(event, dict) or set(event) != {"type", "message"} or event["type"] != "user"
            or not isinstance(event["message"], dict) or set(event["message"]) != {"role", "content"}
            or event["message"]["role"] != "user"):
        _fail("not_native_user_input")
    blocks = event["message"]["content"]
    if not isinstance(blocks, list) or not blocks: _fail("missing_native_input")
    images = texts = 0
    for block in blocks:
        if not isinstance(block, dict): _fail("invalid_native_content_block")
        if block.get("type") == "text" and set(block) == {"type", "text"}:
            if not isinstance(block["text"], str) or not block["text"].strip() or "\0" in block["text"]: _fail("invalid_native_text")
            texts += 1
        elif block.get("type") == "image" and set(block) == {"type", "source"}:
            source = block["source"]
            if (not isinstance(source, dict) or set(source) != {"type", "media_type", "data"}
                    or source["type"] != "base64" or source["media_type"] != "image/png"):
                _fail("invalid_native_image")
            try: data = base64.b64decode(source["data"], validate=True)
            except (ValueError, TypeError): _fail("invalid_native_image")
            if len(data) < 33 or data[:16] != b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR": _fail("invalid_native_image")
            images += 1
        else: _fail("unsupported_native_content")
    if texts < 1 or images != int(spec["task_id"] in _IMAGE_TASKS): _fail("native_material_count_mismatch")


def _observation(root, spec, boundary=None, *, state=None, issues=None, timing_admissible=None):
    metadata = boundary.metadata() if boundary else {}
    if state is None:
        state = ("held" if metadata.get("issues") else "drained" if metadata.get("owned_work_drained_monotonic_ns") is not None
                 else "active" if metadata.get("prompt_released_monotonic_ns") is not None else "prepared")
    value = {"schema_version": "agenttime.natural-worker-observation.v1", "attempt_id": spec["attempt_id"],
        "task_id": spec["task_id"], "session_id": spec["session_id"], "state": state,
        "source_observed_at": datetime.now(timezone.utc).isoformat(), "worker_monotonic_ns": time.monotonic_ns(),
        "last_native_event_monotonic_ns": boundary._last_event_ns if boundary else None,
        "clock_id": metadata.get("clock_id"), "timing_admissible": timing_admissible,
        "holds_capacity_until_controller_verifies_stop": True}
    for key in ("prompt_released_monotonic_ns", "result_monotonic_ns", "submission_monotonic_ns", "root_exit_monotonic_ns",
                "owned_work_drained_monotonic_ns", "native_terminal_monotonic_ns", "runtime_seconds", "timing_valid"):
        value[key] = metadata.get(key)
    value["issues"] = list(metadata.get("issues", []) if issues is None else issues)
    temporary = Path(root) / (".observation-" + str(uuid.uuid4()))
    ns._write_new(temporary, ns._json_bytes(value))
    os.replace(temporary, Path(root) / "observation.json")
    descriptor = os.open(root, os.O_RDONLY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def prepare(root, spec, input_bytes):
    validate_spec(spec, input_bytes)
    root = Path(root).absolute()
    if root.exists() or root.is_symlink(): _fail("state_already_exists")
    root.mkdir(mode=0o700)
    for name in ns.STATE_ROOTS: (root / name).mkdir(mode=0o700)
    spec_bytes = ns._json_bytes(spec)
    for path, data in ((root / "spec.json", spec_bytes), (root / "input.json", input_bytes),
                       (root / "capture" / "prepared-spec.json", spec_bytes), (root / "capture" / "prepared-input.json", input_bytes)):
        ns._write_new(path, data)
    if spec["family_id"] == "assistant":
        text = "\n\n".join(block["text"] for block in _decode(input_bytes)["message"]["content"])
        ns._write_new(root / "capture" / "browser-goal.txt", text.encode())
        for path in (root / "home" / "browser", root / "tmp" / "browser", root / "capture" / "browser-config", root / "capture" / "browser-cache"):
            path.mkdir(mode=0o700)
    entries = ns.inventory(root)
    baseline = {"schema_version": "agenttime.natural-worker-baseline.v1", "attempt_id": spec["attempt_id"],
        "session_id": spec["session_id"], "identity_sha256": document_sha256(spec["identity"]),
        "spec_sha256": ns._sha(spec_bytes), "contract_sha256": spec["preparation_contract_sha256"],
        "input_sha256": spec["input_sha256"], "inventory": entries, "inventory_sha256": ns._sha(ns._json_bytes(entries)),
        "native_state": "empty_store_with_predeclared_session_id", "study_admitted": False}
    ns._write_new(root / "baseline.json", ns._json_bytes(baseline))
    _observation(root, spec)
    return {"state": "prepared", "attempt_id": spec["attempt_id"], "session_id": spec["session_id"],
            "spec_sha256": baseline["spec_sha256"], "baseline_sha256": ns._file_sha(root / "baseline.json"),
            "inventory_sha256": baseline["inventory_sha256"], "study_admitted": False}


def verify_release(root, spec, input_bytes, acknowledgement, authorization):
    validate_spec(spec, input_bytes); root = Path(root)
    spec_bytes = ns._json_bytes(spec)
    if (_read_bytes(root / "spec.json") != spec_bytes or _read_bytes(root / "input.json") != input_bytes
            or _read_bytes(root / "capture" / "prepared-spec.json") != spec_bytes
            or _read_bytes(root / "capture" / "prepared-input.json") != input_bytes): _fail("prepared_state_changed")
    baseline = _read_json(root / "baseline.json")
    entries = ns.inventory(root); inventory_hash = ns._sha(ns._json_bytes(entries))
    bindings = {"attempt_id": spec["attempt_id"], "session_id": spec["session_id"],
                "identity_sha256": document_sha256(spec["identity"]), "spec_sha256": ns._sha(spec_bytes),
                "contract_sha256": spec["preparation_contract_sha256"], "input_sha256": spec["input_sha256"],
                "inventory_sha256": inventory_hash}
    if (baseline.get("schema_version") != "agenttime.natural-worker-baseline.v1" or baseline.get("inventory") != entries
            or any(baseline.get(key) != value for key, value in bindings.items())): _fail("baseline_state_changed")
    expected = {"schema_version": "agenttime.natural-baseline-ack.v1", **{k: v for k, v in bindings.items() if k != "spec_sha256"},
                "archive_verified": True, "independent_archive": True}
    if not isinstance(acknowledgement, dict) or set(acknowledgement) != set(expected) | {"archive_manifest_sha256", "archive_location"}:
        _fail("baseline_ack_shape")
    if any(acknowledgement.get(k) != v for k, v in expected.items()): _fail("baseline_ack_mismatch")
    if acknowledgement["archive_verified"] is not True or acknowledgement["independent_archive"] is not True: _fail("baseline_ack_mismatch")
    _digest(acknowledgement["archive_manifest_sha256"]); _text(acknowledgement["archive_location"])
    expected = {"schema_version": "agenttime.natural-release-authorization.v1", "attempt_id": spec["attempt_id"],
                "identity_sha256": bindings["identity_sha256"], "spec_sha256": bindings["spec_sha256"],
                "input_sha256": spec["input_sha256"], "baseline_ack_sha256": document_sha256(acknowledgement), "ledger_authorized": True}
    if not isinstance(authorization, dict) or set(authorization) != set(expected) | {"authorization_id", "campaign_id", "manifest_sha256"}:
        _fail("release_authorization_shape")
    if any(authorization.get(k) != v for k, v in expected.items()) or authorization["ledger_authorized"] is not True:
        _fail("release_authorization_mismatch")
    _uuid(authorization["authorization_id"]); _text(authorization["campaign_id"]); _digest(authorization["manifest_sha256"])


def verify_runtime(binary, spec):
    if sys.platform != "linux": _fail("linux_worker_required")
    validate_spec(spec)
    ns.verify_binary(binary)
    boot_id = _read_bytes("/proc/sys/kernel/random/boot_id").decode("ascii").strip()
    _uuid(boot_id)
    # Container identity, image and cgroup allocation are inspected by the
    # controller. A worker cannot independently attest its own Docker daemon.
    return {"clock_id": "linux-boot:" + boot_id, "source_pins_verified": True, "cli_verified": True,
            "python_executable": str(Path(sys.executable).absolute()), "image_and_resources_verified": False}


def _browser_environment(root):
    root = Path(root).absolute()
    return {"HOME": str(root / "home" / "browser"), "TMPDIR": str(root / "tmp" / "browser"),
            "XDG_CONFIG_HOME": str(root / "capture" / "browser-config"), "XDG_CACHE_HOME": str(root / "capture" / "browser-cache"),
            "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONPATH": str(_SOURCE_PATH.parents[1]),
            "PYTHONDONTWRITEBYTECODE": "1", "PLAYWRIGHT_BROWSERS_PATH": "/opt/playwright"}


def configuration(root, binary, spec, token):
    validate_spec(spec)
    if not isinstance(token, str) or not 12 <= len(token) <= 32768 or any(c in token for c in "\r\n\0"):
        _fail("invalid_credential_channel")
    root = Path(root).absolute()
    env = {"HOME": str(root / "home"), "CLAUDE_CONFIG_DIR": str(root / "config"),
        "TMPDIR": str(root / "tmp") + "/", "XDG_RUNTIME_DIR": str(root / "xdg"),
        "PATH": str(Path(binary).parent) + ":/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "USER": "agenttime", "LOGNAME": "agenttime", "SHELL": "/bin/bash", "LANG": "C.UTF-8", "TERM": "dumb",
        "CLAUDE_CODE_OAUTH_TOKEN": token, "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_ATTACHMENTS": "1", "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1", "CLAUDE_CODE_MAX_RETRIES": "0",
        "CLAUDE_CODE_NO_MODEL_FALLBACK": "1", "CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK": "1",
        "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "0", "OTEL_LOG_RAW_API_BODIES": "file:" + str(root / "capture" / "raw-bodies")}
    settings = {"disableAllHooks": True, "disableClaudeAiConnectors": True, "autoMemoryEnabled": False,
                "includeGitInstructions": False, "attribution": {"commit": "", "pr": ""}}
    mcp = {"mcpServers": {}}
    if spec["family_id"] == "assistant":
        mcp["mcpServers"]["assistantbench"] = {"command": "/usr/bin/env", "args": ["-i",
            *[f"{key}={value}" for key, value in _browser_environment(root).items()], str(Path(sys.executable).absolute()),
            "-m", "agenttime.natural_worker", "bridge", "--root", str(root)]}
    command = [str(binary), "-p", "--model", ns.CLI_MODEL, "--effort", "max", "--session-id", spec["session_id"],
        "--tools", ",".join(spec["tool_policy"]["native_tools"]), "--strict-mcp-config", "--mcp-config", json.dumps(mcp),
        "--setting-sources", "", "--settings", json.dumps(settings), "--disable-slash-commands",
        "--output-format", "stream-json", "--verbose", "--input-format", "stream-json",
        "--debug-file", str(root / "capture" / "debug.log")]
    if spec["family_id"] in {"gpqa", "hle"}: command += ["--system-prompt", ""]
    else: command += ["--permission-mode", "bypassPermissions"]
    return {"command": command, "environment": env}


class NaturalBoundary(ns.NativeBoundary):
    """Natural execution evidence, initialized without a qualification contract."""

    def __init__(self, capture, spec, input_bytes, *, clock_id=None):
        validate_spec(spec, input_bytes)
        self.capture = Path(capture); self.spec = _decode(ns._json_bytes(spec))
        self.contract = {"session_id": spec["session_id"], "input": _decode(input_bytes),
            "tools": spec["tool_policy"]["native_tools"] + spec["tool_policy"]["mcp_tools"],
            "case": "web_subagent" if spec["family_id"] == "browsecomp" else "natural"}
        self.issues = []; self.usage = []; self.initialized = False
        self.release_ns = self.result_ns = self.root_exit_ns = self.drain_ns = self.terminal_ns = None
        self.exit_code = None; self.answer_sha256 = None; self.cancelled = False
        self._sequence = 0; self._last_event_ns = None; self.init_evidence = None; self.submission_ns = None
        self.clock_id = clock_id

    def _event(self, kind, observed_ns, **details):
        return super()._event(kind, observed_ns, clock_id=self.clock_id, **details)

    def release(self, observed_ns):
        if self.release_ns is not None: self.fail("duplicate_prompt_release")
        self.release_ns = observed_ns
        self._event("prompt_released", observed_ns, input_sha256=self.spec["input_sha256"], attempt_id=self.spec["attempt_id"])

    def _submission(self, observed_ns):
        state = self.capture / "browser-state"
        if not (state / "submission.json").exists(): return
        receipt = _read_json(state / "submission.json"); answer = _read_bytes(state / "final-answer.txt")
        expected = {"schema_version", "attempt_id", "native_submission_monotonic_ns", "answer_sha256", "answer_bytes", "endpoint", "grade_status"}
        if (not isinstance(receipt, dict) or set(receipt) != expected or receipt["schema_version"] != 1
                or receipt["attempt_id"] != self.spec["attempt_id"] or receipt["endpoint"] != "send_msg_to_user"
                or receipt["grade_status"] != "not_run" or receipt["answer_sha256"] != ns._sha(answer)
                or type(receipt["answer_bytes"]) is not int or receipt["answer_bytes"] != len(answer)
                or type(receipt["native_submission_monotonic_ns"]) is not int
                or not self.release_ns <= receipt["native_submission_monotonic_ns"] <= observed_ns):
            self.fail("invalid_native_browser_submission")
        if self.submission_ns is not None:
            if self.submission_ns != receipt["native_submission_monotonic_ns"] or self.answer_sha256 != receipt["answer_sha256"]:
                self.fail("native_browser_submission_changed")
            return
        ns._write_new(self.capture / "sealed-answer.txt", answer)
        self.submission_ns = receipt["native_submission_monotonic_ns"]; self.answer_sha256 = receipt["answer_sha256"]
        self._event("answer_sealed", observed_ns, sha256=self.answer_sha256, native_submission_monotonic_ns=self.submission_ns)

    def observe(self, event, observed_ns):
        if self.release_ns is None: self.fail("native_event_before_release")
        if self.spec["family_id"] != "assistant": return super().observe(event, observed_ns)
        if not isinstance(event, dict): self.fail("malformed_native_event")
        self._submission(observed_ns)
        if event.get("type") == "system" and event.get("subtype") == "init":
            self._event("native_event", observed_ns, metadata=ns.event_metadata(event))
            if self.initialized: self.fail("duplicate_native_init")
            if (event.get("session_id") != self.spec["session_id"] or event.get("model") not in (ns.MODEL, ns.CLI_MODEL)
                    or event.get("claude_code_version") != ns.CLI_VERSION): self.fail("native_identity_mismatch")
            if event.get("tools") != list(ASSISTANT_TOOLS) and (not isinstance(event.get("tools"), list)
                    or sorted(event["tools"]) != sorted(ASSISTANT_TOOLS)): self.fail("native_capability_mismatch")
            native_mcp = [{"name": "assistantbench", "status": "connected"}]
            annotated_mcp = [{"name": "assistantbench", "status": "connected", "source": "dynamic"}]
            if event.get("mcp_servers") not in (native_mcp, annotated_mcp): self.fail("unexpected_extension")
            if any(event.get(k) != [] for k in ("skills", "plugins")): self.fail("unexpected_extension")
            self.initialized = True
            self.init_evidence = {k: event[k] for k in ("model", "claude_code_version", "tools", "mcp_servers", "skills", "plugins")}
        elif event.get("type") == "result":
            self._event("native_event", observed_ns, metadata=ns.event_metadata(event))
            if self.result_ns is not None: self.fail("duplicate_native_result")
            if (not self.initialized or event.get("session_id") != self.spec["session_id"] or event.get("subtype") != "success"
                    or event.get("is_error") is not False or not isinstance(event.get("result"), str)):
                self.fail("invalid_native_result")
            if self.submission_ns is None: self.fail("missing_native_browser_submission")
            self.result_ns = observed_ns
            self._event("native_result", observed_ns, sealed_answer_sha256=self.answer_sha256)
        else: return super().observe(event, observed_ns)

    def metadata(self):
        value = super().metadata(); value.pop("readiness", None)
        return {**value, "submission_monotonic_ns": self.submission_ns, "clock_id": self.clock_id, "study_admitted": False}


def _native_rows(path, root):
    path = Path(path); root = Path(root).absolute()
    if not path.is_relative_to(root) or any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(root)):
        _fail("native_store_unsafe_path")
    data = _read_bytes(path)
    if not data or not data.endswith(b"\n"): _fail("native_store_incomplete")
    rows = [_decode(line) for line in data.splitlines() if line.strip()]
    if not rows or any(not isinstance(row, dict) for row in rows): _fail("native_store_malformed")
    return rows, ns._sha(data)


def _native_messages(rows, session_id, cwd, agent_id=None):
    messages = []; seen = set()
    for row in rows:
        if "sessionId" in row and row["sessionId"] != session_id: _fail("native_store_identity_mismatch")
        if "uuid" in row:
            _uuid(row["uuid"])
            if row["uuid"] in seen: _fail("native_store_duplicate_record")
            if row.get("parentUuid") is not None and row["parentUuid"] not in seen: _fail("native_store_broken_parent_chain")
            seen.add(row["uuid"])
        if row.get("type") not in {"user", "assistant"}: continue
        if (row.get("sessionId") != session_id or row.get("version") != ns.CLI_VERSION or row.get("cwd") != cwd
                or row.get("isSidechain") is not (agent_id is not None) or row.get("agentId") != agent_id):
            _fail("native_store_identity_mismatch")
        _uuid(row.get("uuid"))
        message = row.get("message")
        if not isinstance(message, dict) or message.get("role") != row["type"]: _fail("native_store_message_malformed")
        content = message.get("content")
        if not isinstance(content, (list, str)) or (isinstance(content, list) and any(not isinstance(b, dict) for b in content)):
            _fail("native_store_message_malformed")
        if row["type"] == "assistant":
            if (message.get("type") != "message" or message.get("model") != ns.MODEL
                    or not isinstance(message.get("id"), str) or not message["id"]
                    or not isinstance(row.get("requestId"), str) or not row["requestId"].startswith("req_")):
                _fail("native_store_assistant_identity_mismatch")
        messages.append(row)
    if not messages or messages[0]["type"] != "user": _fail("native_store_initial_user_missing")
    if not any(row["type"] == "assistant" for row in messages): _fail("native_store_final_message_missing")
    return messages


def _native_message_content(message):
    # Native child inputs use a string in the original store and one text block
    # in the SDK stream. Preserve exact text; normalize only that representation.
    content = message.get("content")
    if isinstance(content, str): content = [{"type": "text", "text": content}]
    return {key: message.get(key) for key in ("role", "type", "model", "id")} | {"content": content}


def validate_native_stores(root, spec, input_bytes, *, runtime_cwd=None):
    """Validate original stores after drainage; never reconstruct missing files.

    This checks archival completeness, not transport admission or resumability.
    Its caller validates the execution spec and separately proves process drain.
    An empty optional API index is acceptable here, not in the transport gate.
    """
    root = Path(root).absolute(); sid = spec["session_id"]
    cwd = runtime_cwd if runtime_cwd is not None else str(root / "work")
    stream, stream_sha = _native_rows(root / "capture/stream.jsonl", root)
    init = [row for row in stream if row.get("type") == "system" and row.get("subtype") == "init"]
    results = [row for row in stream if row.get("type") == "result"]
    if (len(init) != 1 or init[0].get("session_id") != sid or init[0].get("cwd") != cwd
            or init[0].get("claude_code_version") != ns.CLI_VERSION or init[0].get("model") not in (ns.MODEL, ns.CLI_MODEL)
            or len(results) != 1 or results[0].get("session_id") != sid or results[0].get("subtype") != "success"
            or results[0].get("is_error") is not False or not isinstance(results[0].get("result"), str)):
        _fail("native_store_stream_identity_mismatch")
    slug = re.sub(r"[^a-zA-Z0-9-]", "-", cwd)
    projects = root / "config/projects"; parent_path = projects / slug / (sid + ".jsonl")
    if not parent_path.is_file(): _fail("native_parent_store_missing")
    parent_rows, parent_sha = _native_rows(parent_path, root)
    parent = _native_messages(parent_rows, sid, cwd)
    initial = _decode(input_bytes)["message"]
    if parent[0]["message"] != initial: _fail("native_store_input_mismatch")

    # Native task lifecycle and Agent results bind a tool invocation to its
    # persisted child ID. The transport index, when present, supplies additional
    # independently observed child request/message pairs; it is never invented.
    tool_to_child = {}; observed_children = set(); failed_agent_tools = set(); completed_children = {}
    def bind(tool_id, child_id):
        if (not isinstance(tool_id, str) or not tool_id or not isinstance(child_id, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", child_id)
                or (tool_id in tool_to_child and tool_to_child[tool_id] != child_id)):
            _fail("native_child_identity_unverified")
        tool_to_child[tool_id] = child_id; observed_children.add(child_id)
    for row in stream:
        if row.get("type") == "system" and row.get("subtype") == "task_started" and row.get("task_type") == "local_agent":
            bind(row.get("tool_use_id"), row.get("task_id"))
        result = row.get("tool_use_result")
        if isinstance(result, dict) and "agentId" in result:
            blocks = row.get("message", {}).get("content", [])
            ids = [b.get("tool_use_id") for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
            if len(ids) != 1: _fail("native_child_identity_unverified")
            bind(ids[0], result["agentId"])
            if result.get("status") == "completed":
                completed_children[result["agentId"]] = result.get("content")
        for block in row.get("message", {}).get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error") is True:
                failed_agent_tools.add(block.get("tool_use_id"))
    if observed_children and not spec["tool_policy"]["native_subagents"]: _fail("native_child_not_permitted")
    children = {}; stores = {None: parent}
    expected_paths = {parent_path}
    for agent_id in sorted(observed_children):
        path = parent_path.with_suffix("") / "subagents" / ("agent-" + agent_id + ".jsonl")
        if not path.is_file(): _fail("native_child_store_missing")
        rows, digest = _native_rows(path, root); messages = _native_messages(rows, sid, cwd, agent_id)
        stores[agent_id] = messages; expected_paths.add(path)
        children[agent_id] = {"path": path.relative_to(root).as_posix(), "sha256": digest, "message_count": len(messages)}
    if set(projects.rglob("*.jsonl")) != expected_paths: _fail("native_store_unexpected_session")

    native_by_uuid = {}
    for agent_id, messages in stores.items():
        for row in messages:
            if row["uuid"] in native_by_uuid: _fail("native_store_duplicate_record")
            native_by_uuid[row["uuid"]] = (agent_id, row)
    streamed_parent = set(); streamed_users = set(); streamed_child_users = set()
    for row in stream:
        if row.get("type") not in {"user", "assistant"}: continue
        if row.get("session_id") != sid: _fail("native_store_stream_identity_mismatch")
        parent_tool = row.get("parent_tool_use_id")
        if parent_tool is not None and parent_tool not in tool_to_child: _fail("native_child_identity_unverified")
        agent_id = tool_to_child.get(parent_tool) if parent_tool else None
        match = native_by_uuid.get(row.get("uuid"))
        if (match is None or match[0] != agent_id or match[1]["type"] != row["type"]
                or _native_message_content(match[1]["message"]) != _native_message_content(row.get("message", {}))
                or (row["type"] == "assistant" and match[1]["requestId"] != row.get("request_id"))):
            _fail("native_store_stream_content_mismatch")
        if agent_id is not None and row["type"] == "user": streamed_child_users.add(row["uuid"])
        if agent_id is None:
            (streamed_parent if row["type"] == "assistant" else streamed_users).add(row["uuid"])
    if {row["uuid"] for row in parent if row["type"] == "assistant"} != streamed_parent:
        _fail("native_store_final_message_missing")
    # Only the supplied user message, exact native image companion and observed
    # tool-result messages may appear as parent user rows in this fresh session.
    image_count = sum(block.get("type") == "image" for block in initial["content"])
    expected_companion = {"role": "user", "content": [{"type": "text", "text":
        f"[Image: source: {Path(cwd).parent / 'tmp/claude-10001' / slug / sid / 'images' / (str(i) + '.png')}]"}
        for i in range(1, image_count + 1)]}
    companions = 0
    for row in parent[1:]:
        if row["type"] != "user" or row["uuid"] in streamed_users: continue
        if (not image_count or row.get("isMeta") is not True or row.get("turnCompanion") is not True
                or row["message"] != expected_companion): _fail("native_store_extra_user_input")
        companions += 1
    if companions != int(bool(image_count)): _fail("native_store_image_companion_mismatch")
    def final_text_blocks(messages):
        if messages[-1]["type"] != "assistant": _fail("native_store_final_message_missing")
        final_id = messages[-1]["message"]["id"]
        return [block for row in messages if row["type"] == "assistant" and row["message"]["id"] == final_id
                for block in row["message"]["content"] if block.get("type") == "text"]
    text = "".join(block["text"] for block in final_text_blocks(parent))
    if text != results[0]["result"]: _fail("native_store_final_answer_mismatch")
    for agent_id, messages in stores.items():
        if agent_id is None: continue
        if messages[0]["uuid"] not in streamed_child_users: _fail("native_child_input_unverified")
        blocks = final_text_blocks(messages)
        if agent_id in completed_children and completed_children[agent_id] != blocks:
            _fail("native_child_final_answer_mismatch")

    agent_tools = set()
    child_pairs = set()
    for agent_id, messages in stores.items():
        for row in messages:
            if row["type"] != "assistant": continue
            if agent_id is not None: child_pairs.add((row["requestId"], row["message"]["id"]))
            for block in row["message"]["content"]:
                if block.get("type") == "tool_use" and block.get("name") in ("Agent", "Task"):
                    agent_tools.add(block.get("id"))
    if set(tool_to_child) - agent_tools or agent_tools - set(tool_to_child) - failed_agent_tools:
        _fail("native_child_identity_unverified")
    index = root / "capture/raw-bodies/index.jsonl"
    if index.exists() and _read_bytes(index):
        records, _ = _native_rows(index, root)
        for record in records:
            if str(record.get("query_source", "")).startswith("agent:"):
                if (record.get("session_id") != sid or (record.get("request_id"), record.get("message_id")) not in child_pairs):
                    _fail("native_child_store_missing")
    spawned = results[0].get("subagent_stats", {}).get("spawned")
    if spawned is not None and (type(spawned) is not int or spawned != len(observed_children)):
        _fail("native_child_count_mismatch")
    return {"original_native_stores_verified": True, "restoration_proven": False, "stream_sha256": stream_sha,
        "session_id": sid, "parent": {"path": parent_path.relative_to(root).as_posix(), "sha256": parent_sha,
            "message_count": len(parent)}, "children": children}



def validate_browser_snapshot(root, spec, input_bytes, boundary):
    """Bind the private partial browser checkpoint only after native work drains.

    BrowserGym submission seals the answer before the snapshot can finish. The
    stream-time submission check must therefore not require these files. A
    missing or invalid final snapshot holds capture while retaining the answer
    and all original state. This does not qualify full browser restoration.
    """
    if spec["family_id"] != "assistant": _fail("native_browser_snapshot_wrong_family")
    if (type(boundary.submission_ns) is not int or type(boundary.drain_ns) is not int
            or not boundary.submission_ns <= boundary.drain_ns):
        _fail("native_browser_snapshot_drain_unproven")
    root = Path(root).absolute(); state = root / "capture" / "browser-state"
    snapshot_path = state / "browser-state.json"; receipt_path = state / "browser-state-receipt.json"
    if state.is_symlink() or not state.is_dir() or not state.resolve().is_relative_to(root.resolve()):
        _fail("native_browser_snapshot_missing")
    if not snapshot_path.is_file() or not receipt_path.is_file(): _fail("native_browser_snapshot_missing")
    try:
        data = _read_bytes(snapshot_path); receipt_bytes = _read_bytes(receipt_path); receipt = _decode(receipt_bytes)
    except (ValueError, OSError): _fail("native_browser_snapshot_invalid")
    fields = {"schema_version", "attempt_id", "sha256", "bytes", "captured_at_monotonic_ns", "persisted",
              "full_session_restore_qualified"}
    persisted = ["cookies", "localStorage", "open_page_urls", "active_page_index"]
    if (type(receipt) is not dict or set(receipt) != fields or type(receipt["schema_version"]) is not int
            or receipt["schema_version"] != 1 or receipt["attempt_id"] != spec["attempt_id"]
            or receipt["full_session_restore_qualified"] is not False or receipt["persisted"] != persisted
            or type(receipt["bytes"]) is not int or receipt["bytes"] != len(data)
            or receipt["sha256"] != ns._sha(data)):
        _fail("native_browser_snapshot_receipt_invalid")
    captured = receipt["captured_at_monotonic_ns"]
    if type(captured) is not int or not boundary.submission_ns <= captured <= boundary.drain_ns:
        _fail("native_browser_snapshot_time_invalid")
    try:
        from .assistantbench_bridge import _validate_snapshot
        snapshot = _validate_snapshot(_decode(data))
    except (ValueError, OSError, TypeError): _fail("native_browser_snapshot_invalid")
    goal = "\n\n".join(block["text"] for block in _decode(input_bytes)["message"]["content"]).encode()
    goal_hash = ns._sha(goal)
    if snapshot["goal_sha256"] != goal_hash or _read_bytes(root / "capture" / "browser-goal.txt") != goal:
        _fail("native_browser_snapshot_goal_mismatch")
    # Revalidate the already sealed submission; do not create a new submission
    # or replace the sealed answer during this final evidence check.
    if not (state / "submission.json").is_file(): _fail("native_browser_snapshot_submission_missing")
    boundary._submission(boundary.drain_ns)
    return {"attempt_id": spec["attempt_id"], "goal_sha256": goal_hash,
            "snapshot": {"path": snapshot_path.relative_to(root).as_posix(), "sha256": ns._sha(data), "bytes": len(data)},
            "receipt": {"path": receipt_path.relative_to(root).as_posix(), "sha256": ns._sha(receipt_bytes)},
            "captured_at_monotonic_ns": captured, "native_submission_monotonic_ns": boundary.submission_ns,
            "owned_work_drained_monotonic_ns": boundary.drain_ns, "persisted": persisted,
            "full_session_restore_qualified": False}


def run_prepared(root, acknowledgement_path, authorization_path, binary, token):
    root = Path(root).absolute()
    if root.is_symlink() or not root.is_dir(): _fail("unsafe_session_root")
    if (root / "run-started.json").exists() or (root / "run-started.json").is_symlink(): _fail("attempt_already_started")
    spec_bytes = _read_bytes(root / "spec.json"); spec = _decode(spec_bytes)
    input_bytes = _read_bytes(root / "input.json")  # Only this verified sequence is delivered.
    validate_spec(spec, input_bytes); runtime_verification = verify_runtime(binary, spec)
    acknowledgement = _read_json(acknowledgement_path); authorization = _read_json(authorization_path)
    verify_release(root, spec, input_bytes, acknowledgement, authorization)
    conf = configuration(root, binary, spec, token)
    intent = {"attempt_id": spec["attempt_id"], "session_id": spec["session_id"], "identity_sha256": document_sha256(spec["identity"]),
        "spec_sha256": ns._sha(spec_bytes), "input_sha256": ns._sha(input_bytes),
        "authorization_sha256": document_sha256(authorization), "automatic_retry": False}
    try: ns._write_new(root / "run-started.json", ns._json_bytes(intent))
    except FileExistsError: _fail("attempt_already_started")
    boundary = NaturalBoundary(root / "capture", spec, input_bytes, clock_id=(runtime_verification or {}).get("clock_id"))
    report = {"schema_version": "agenttime.natural-worker-report.v1", **intent, "state": "held", "issues": [],
        "requested_route": "subscription", "configured_authentication": "stdin_to_child_environment_only",
        "archive_verified": False, "native_sessions_verified": False, "independent_initial_archive_acknowledged": True,
        "independent_final_archive_acknowledged": False, "included_subscription_allowance_verified": False,
        "timing_admissible": False, "study_admitted": False, "experiment_time_cap_seconds": None,
        "requires_controller_stop_verification": True, "capture_finished_monotonic_ns": None,
        "runtime_image_and_resources_verified_by_worker": False, "runtime_verification": runtime_verification}
    if spec["family_id"] == "assistant": report["browser_snapshot_verified"] = False
    stop = threading.Event()
    def heartbeat():
        while not stop.wait(5):
            try: _observation(root, spec, boundary)
            except OSError:
                if "heartbeat_write_failed" not in boundary.issues: boundary.issues.append("heartbeat_write_failed")
    thread = threading.Thread(target=heartbeat, name="private-worker-heartbeat", daemon=True)
    thread.start()
    try:
        for name, data in (("delivered-input.json", input_bytes), ("baseline-ack.json", ns._json_bytes(acknowledgement)),
                           ("release-authorization.json", ns._json_bytes(authorization))):
            ns._write_new(root / "capture" / name, data)
        _observation(root, spec, boundary)
        ns.supervise(conf["command"], conf["environment"], root / "work", input_bytes, boundary)
        if boundary.answer_sha256 is not None and ns._file_sha(root / "capture" / "sealed-answer.txt") != boundary.answer_sha256:
            boundary.fail("sealed_answer_changed")
        report["transport"] = ns.inspect_transport_records(root / "capture" / "raw-bodies", boundary.contract)
        report["included_subscription_allowance_verified"] = ns.subscription_allowance_verified(boundary.usage)
        report["native_session_evidence"] = validate_native_stores(root, spec, input_bytes)
        report["native_sessions_verified"] = True
        if spec["family_id"] == "assistant":
            report["browser_snapshot_evidence"] = validate_browser_snapshot(root, spec, input_bytes, boundary)
            report["browser_snapshot_verified"] = True
    except (ValueError, OSError) as exc:
        code = str(exc) if isinstance(exc, ns.NativeSessionError) and re.fullmatch(r"[a-z0-9_]{1,96}", str(exc)) else "worker_execution_failed"
        if code not in boundary.issues: boundary.issues.append(code)
    finally:
        stop.set(); thread.join()
    report["execution"] = boundary.metadata()
    report["issues"] = list(boundary.issues)
    for issue in report.get("transport", {}).get("issues", []):
        if issue not in report["issues"]: report["issues"].append(issue)
    if not report["included_subscription_allowance_verified"]: report["issues"].append("subscription_allowance_unverified")
    if boundary.drain_ns is not None:
        try:
            report["archive"] = ns.capture_archive(root, token); report["archive_verified"] = True
            report["capture_finished_monotonic_ns"] = time.monotonic_ns()
        except (ValueError, OSError): report["issues"].append("archive_held_preserve_original_state")
    else: report["issues"].append("owned_work_unresolved")
    report["timing_admissible"] = bool(report["execution"]["timing_valid"] and not report["issues"])
    if report["timing_admissible"] and report["archive_verified"]: report["state"] = "captured"
    ns._write_new(root / "report.json", ns._json_bytes(report))
    _observation(root, spec, boundary, state=report["state"], issues=report["issues"], timing_admissible=report["timing_admissible"])
    return report


def bridge_entry(root):
    root = Path(root).absolute()
    os.environ.clear(); os.environ.update(_browser_environment(root))
    spec = _read_json(root / "spec.json"); input_bytes = _read_bytes(root / "input.json")
    validate_spec(spec, input_bytes)
    if spec["family_id"] != "assistant" or not (root / "run-started.json").is_file(): _fail("browser_launch_without_natural_onset")
    from .assistantbench_bridge import AssistantBenchBridge, NativeBrowserBackend, serve_stdio
    goal = _read_bytes(root / "capture" / "browser-goal.txt").decode("utf-8")
    expected = "\n\n".join(block["text"] for block in _decode(input_bytes)["message"]["content"])
    if goal != expected: _fail("browser_goal_changed")
    bridge = AssistantBenchBridge(NativeBrowserBackend(goal), root / "capture" / "browser-state", spec["attempt_id"])
    try: serve_stdio(bridge)
    finally: bridge.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    stage = commands.add_parser("prepare"); stage.add_argument("--root", required=True)
    stage.add_argument("--spec", required=True); stage.add_argument("--input", required=True)
    run = commands.add_parser("run"); run.add_argument("--root", required=True)
    run.add_argument("--baseline-ack", required=True); run.add_argument("--release-authorization", required=True)
    run.add_argument("--binary", required=True)
    bridge = commands.add_parser("bridge"); bridge.add_argument("--root", required=True)
    args = parser.parse_args(argv); os.umask(0o077)
    try:
        if args.operation == "prepare": report = prepare(args.root, _read_json(args.spec), _read_bytes(args.input))
        elif args.operation == "bridge": bridge_entry(args.root); return 0
        else:
            token = sys.stdin.readline(32770).rstrip("\n")
            report = run_prepared(args.root, args.baseline_ack, args.release_authorization, args.binary, token)
        print(json.dumps(report, allow_nan=False), flush=True)
        return 0 if report["state"] in {"prepared", "captured"} else 1
    except (ValueError, OSError) as exc:
        code = str(exc) if isinstance(exc, ns.NativeSessionError) and re.fullmatch(r"[a-z0-9_]{1,96}", str(exc)) else "natural_worker_entry_failed"
        if args.operation != "bridge": print(json.dumps({"state": "held", "error_code": code, "study_admitted": False}), flush=True)
        return 1


if __name__ == "__main__": raise SystemExit(main())
