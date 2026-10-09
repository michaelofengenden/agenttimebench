"""Synthetic-only qualification of an isolated native Claude session.

This module cannot admit study tasks. Its fixed canaries exercise a candidate
runtime; the resulting archive never establishes restore or recovery support.
It has no provider SDK and reads authentication only in ``run_prepared``.
"""
from __future__ import annotations

import base64
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import struct
import subprocess
import sys
import time
import uuid
import zlib


CLI_VERSION = "2.1.280"
CLI_SHA256 = "1e08503dbdf3c2cb0d706d32f3408277388d1c76ef108673e8fe42c1b322925b"
MODEL = "claude-opus-5-5"
CLI_MODEL = MODEL + "[1m]"
_SOURCE_PATH = Path(__file__).resolve()
_LOADED_SOURCE_SHA256 = hashlib.sha256(_SOURCE_PATH.read_bytes()).hexdigest()
STATE_ROOTS = ("home", "config", "tmp", "work", "xdg", "capture")
AUTH_FILENAMES = {".credentials.json", "credentials.json", "auth.json", "tokens.json"}
CASES = {
    "closed_book": ("Reply exactly with SYNTHETIC_NATIVE_OK. This is a synthetic infrastructure check.", []),
    "image": ("Name the single solid color in this synthetic image, then finish. This is an infrastructure check.", []),
    "web_tools": ("Use WebSearch to find Anthropic's official Claude Code documentation, then WebFetch to read its overview. Return its page title and URL, then finish. This is a synthetic infrastructure check.", ["WebSearch", "WebFetch"]),
    "web_subagent": ("Delegate to one native subagent: use WebSearch and WebFetch to find and read Anthropic's official Claude Code documentation overview. Wait for it and return its page title and URL. Use no other tools. This is a synthetic infrastructure check.", ["WebSearch", "WebFetch", "Agent"]),
}


class NativeSessionError(ValueError):
    """A stable error code suitable for metadata, never an exception with secrets."""


def _json_bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n").encode()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""): digest.update(chunk)
    return digest.hexdigest()


def _write_new(path, data):
    path = Path(path)
    with path.open("xb") as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def _read_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file(): raise NativeSessionError("unsafe_metadata_path")
    try: return json.loads(path.read_bytes())
    except (OSError, ValueError): raise NativeSessionError("unreadable_metadata") from None


def _source_pin():
    if _file_sha(_SOURCE_PATH) != _LOADED_SOURCE_SHA256: raise NativeSessionError("loaded_source_changed")
    return _LOADED_SOURCE_SHA256


def _synthetic_image():
    def chunk(kind, value):
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 32, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress((b"\0" + b"\xff\0\0" * 32) * 32)) + chunk(b"IEND", b"")
    return base64.b64encode(png).decode()


def qualification_contract(qualification_id, session_id, *, case="closed_book", offline=False):
    if case not in CASES: raise NativeSessionError("unsupported_qualification_case")
    text, tools = CASES[case]
    content = [{"type": "text", "text": text}]
    if case == "image":
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _synthetic_image()}})
    value = {
        "schema_version": "agenttime.native-qualification.v1", "purpose": "qualification",
        "qualification_id": qualification_id, "session_id": session_id, "case": case,
        "model": CLI_MODEL, "effort": "max", "cli_version": CLI_VERSION, "cli_sha256": CLI_SHA256,
        "source_sha256": _source_pin(), "tools": list(tools),
        "transport": "offline_loopback" if offline else "subscription",
        "input": {"type": "user", "message": {"role": "user", "content": content}},
    }
    validate_contract(value)
    return value


def validate_contract(value):
    keys = {"schema_version", "purpose", "qualification_id", "session_id", "case", "model", "effort",
            "cli_version", "cli_sha256", "source_sha256", "tools", "transport", "input"}
    if not isinstance(value, dict) or set(value) != keys: raise NativeSessionError("qualification_contract_shape")
    if value["purpose"] != "qualification" or value["schema_version"] != "agenttime.native-qualification.v1":
        raise NativeSessionError("not_qualification")
    if not isinstance(value["qualification_id"], str) or not re.fullmatch(r"synthetic-[a-z0-9-]{1,64}", value["qualification_id"]):
        raise NativeSessionError("not_synthetic_identity")
    try:
        if str(uuid.UUID(value["session_id"])) != value["session_id"]: raise ValueError
    except (ValueError, TypeError, AttributeError): raise NativeSessionError("invalid_session_id") from None
    if (value["model"], value["effort"], value["cli_version"], value["cli_sha256"]) != (CLI_MODEL, "max", CLI_VERSION, CLI_SHA256):
        raise NativeSessionError("runtime_pin_mismatch")
    if value["source_sha256"] != _source_pin(): raise NativeSessionError("source_pin_mismatch")
    case = value["case"]
    if not isinstance(case, str) or case not in CASES: raise NativeSessionError("unsupported_qualification_case")
    text, tools = CASES[case]
    content = [{"type": "text", "text": text}]
    if case == "image": content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _synthetic_image()}})
    if value["tools"] != tools: raise NativeSessionError("tool_policy_mismatch")
    if value["transport"] not in ("subscription", "offline_loopback"): raise NativeSessionError("unknown_transport")
    if value["input"] != {"type": "user", "message": {"role": "user", "content": content}}:
        raise NativeSessionError("not_fixed_synthetic_payload")


def readiness():
    return {"study_ready": False, "native_tools_qualified": False, "native_restore_qualified": False,
            "native_fork_qualified": False, "recovery_qualified": False,
            "initial_native_state_qualified": False, "descendant_capabilities_qualified": False}


def _check_secret(path, secret):
    needle = secret.encode()
    tail = b""
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            data = tail + chunk
            if needle in data: raise NativeSessionError("credential_in_capture")
            tail = data[-max(1, len(needle) - 1):]


def inventory(root, *, secret=None):
    root = Path(root).resolve()
    out = {}
    for name in STATE_ROOTS:
        directory = root / name
        if directory.is_symlink() or not directory.is_dir(): raise NativeSessionError("unsafe_state_root")
        for path in (directory, *sorted(directory.rglob("*"))):
            relative = path.relative_to(root).as_posix()
            if path.name.lower() in AUTH_FILENAMES: raise NativeSessionError("auth_store_in_capture")
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                target = path.resolve()
                if not target.is_relative_to(root) or not target.exists(): raise NativeSessionError("unsafe_archive_link")
                out[relative] = {"type": "link", "target": target.relative_to(root).as_posix()}
            elif stat.S_ISDIR(info.st_mode):
                out[relative] = {"type": "directory", "mode": stat.S_IMODE(info.st_mode)}
            elif stat.S_ISREG(info.st_mode):
                if secret: _check_secret(path, secret)
                out[relative] = {"type": "file", "mode": stat.S_IMODE(info.st_mode), "bytes": info.st_size, "sha256": _file_sha(path)}
            else: raise NativeSessionError("unsupported_archive_object")
    return out


def prepare(root, contract):
    validate_contract(contract)
    root = Path(root).absolute()
    if root.exists() or root.is_symlink(): raise NativeSessionError("state_already_exists")
    root.mkdir(mode=0o700)
    for name in STATE_ROOTS: (root / name).mkdir(mode=0o700)
    _write_new(root / "contract.json", _json_bytes(contract))
    _write_new(root / "input.json", _json_bytes(contract["input"]))
    entries = inventory(root)
    baseline = {"schema_version": "agenttime.native-baseline.v1", "session_id": contract["session_id"],
                "contract_sha256": _sha(_json_bytes(contract)), "input_sha256": _sha(_json_bytes(contract["input"])),
                "inventory": entries, "inventory_sha256": _sha(_json_bytes(entries)),
                "native_state": "empty_store_with_predeclared_session_id", "readiness": readiness()}
    _write_new(root / "baseline.json", _json_bytes(baseline))
    return {"baseline_sha256": _file_sha(root / "baseline.json"), "baseline_path": str(root / "baseline.json"),
            "session_id": contract["session_id"], "readiness": readiness()}


def verify_baseline(root, contract, acknowledgement):
    validate_contract(contract)
    root = Path(root)
    baseline = _read_json(root / "baseline.json")
    if _read_json(root / "contract.json") != contract or (root / "input.json").is_symlink():
        raise NativeSessionError("prepared_input_changed")
    input_hash = _sha(_json_bytes(contract["input"]))
    if _file_sha(root / "input.json") != input_hash: raise NativeSessionError("prepared_input_changed")
    expected = {"schema_version": "agenttime.native-baseline-ack.v1", "session_id": contract["session_id"],
                "contract_sha256": _sha(_json_bytes(contract)), "input_sha256": input_hash,
                "baseline_sha256": _file_sha(root / "baseline.json"), "inventory_sha256": _sha(_json_bytes(inventory(root))),
                "archive_verified": True}
    if not isinstance(acknowledgement, dict) or set(acknowledgement) != set(expected) | {"archive_location"}:
        raise NativeSessionError("baseline_ack_shape")
    if any(acknowledgement.get(key) != value for key, value in expected.items()): raise NativeSessionError("baseline_ack_mismatch")
    if acknowledgement["archive_verified"] is not True: raise NativeSessionError("baseline_archive_unverified")
    if not isinstance(acknowledgement["archive_location"], str) or not acknowledgement["archive_location"].strip():
        raise NativeSessionError("missing_independent_archive_location")
    if any(baseline.get(key) != expected[key] for key in ("session_id", "contract_sha256", "input_sha256", "inventory_sha256")):
        raise NativeSessionError("baseline_state_changed")


def verify_binary(binary):
    binary = Path(binary)
    if binary.is_symlink() or not binary.is_file() or _file_sha(binary) != CLI_SHA256:
        raise NativeSessionError("cli_binary_pin_mismatch")


def configuration(root, binary, contract, token, *, offline_base_url=None):
    validate_contract(contract)
    if not isinstance(token, str) or not 12 <= len(token) <= 32768 or any(c in token for c in "\r\n\0"):
        raise NativeSessionError("invalid_credential_channel")
    root = Path(root).absolute()
    env = {"HOME": str(root / "home"), "CLAUDE_CONFIG_DIR": str(root / "config"),
           "TMPDIR": str(root / "tmp") + "/", "XDG_RUNTIME_DIR": str(root / "xdg"),
           "PATH": str(Path(binary).parent) + ":/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
           "USER": "agenttime", "LOGNAME": "agenttime", "SHELL": "/bin/bash", "LANG": "C.UTF-8", "TERM": "dumb",
           "CLAUDE_CODE_OAUTH_TOKEN": token, "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1",
           "CLAUDE_CODE_DISABLE_ATTACHMENTS": "1", "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1", "CLAUDE_CODE_MAX_RETRIES": "0",
           "CLAUDE_CODE_NO_MODEL_FALLBACK": "1", "CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK": "1",
           "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "0",
           "OTEL_LOG_RAW_API_BODIES": "file:" + str(root / "capture" / "raw-bodies")}
    if contract["transport"] == "offline_loopback":
        match = re.fullmatch(r"http://127\.0\.0\.1:([0-9]{1,5})", offline_base_url or "")
        if not match or not 1 <= int(match[1]) <= 65535: raise NativeSessionError("offline_loopback_required")
        env["ANTHROPIC_BASE_URL"] = offline_base_url
    elif offline_base_url is not None: raise NativeSessionError("unexpected_transport_override")
    settings = {"disableAllHooks": True, "disableClaudeAiConnectors": True, "autoMemoryEnabled": False,
                "includeGitInstructions": False, "attribution": {"commit": "", "pr": ""}}
    command = [str(binary), "-p", "--model", CLI_MODEL, "--effort", "max", "--session-id", contract["session_id"],
               "--tools", ",".join(contract["tools"]), "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
               "--setting-sources", "", "--settings", json.dumps(settings), "--disable-slash-commands",
               "--output-format", "stream-json", "--verbose", "--input-format", "stream-json",
               "--debug-file", str(root / "capture" / "debug.log")]
    if not contract["tools"]: command += ["--system-prompt", ""]
    else: command += ["--permission-mode", "bypassPermissions"]
    return {"command": command, "environment": env, "readiness": readiness()}


def event_metadata(event):
    """Return a strict metadata projection, never message/tool/analysis content."""
    result = {"type": event.get("type"), "subtype": event.get("subtype"), "session_id": event.get("session_id")}
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    if isinstance(message.get("model"), str): result["model"] = message["model"]
    blocks = message.get("content") if isinstance(message.get("content"), list) else []
    result["tool_names"] = [b["name"] for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"
                            and isinstance(b.get("name"), str) and re.fullmatch(r"[A-Za-z0-9_:.-]{1,96}", b["name"])]
    return result


def _usage_metadata(event):
    info = event.get("rate_limit_info")
    if not isinstance(info, dict): return None
    keys = ("status", "rateLimitType", "resetsAt", "overageStatus", "overageDisabledReason", "isUsingOverage", "hasExtraUsage")
    return {key: info[key] for key in keys if key in info and isinstance(info[key], (str, bool, int, float, type(None)))}


def subscription_allowance_verified(rows):
    """OAuth selection alone is not proof that no paid extra usage was used."""
    return bool(rows and all(row.get("isUsingOverage") is False and row.get("hasExtraUsage") in (None, False)
                             and row.get("overageStatus") == "rejected" for row in rows)
                and any(row.get("status") == "allowed" for row in rows))


class NativeBoundary:
    def __init__(self, capture, contract):
        validate_contract(contract)
        self.capture = Path(capture); self.contract = contract
        self.issues = []; self.usage = []; self.initialized = False
        self.release_ns = self.result_ns = self.root_exit_ns = self.drain_ns = self.terminal_ns = None
        self.exit_code = None; self.answer_sha256 = None; self.cancelled = False
        self._sequence = 0; self._last_event_ns = None; self.init_evidence = None

    def _event(self, kind, observed_ns, **details):
        if type(observed_ns) is not int or observed_ns < 0 or (self._last_event_ns is not None and observed_ns < self._last_event_ns):
            self.fail("nonmonotonic_native_clock")
        self._last_event_ns = observed_ns
        self._sequence += 1
        event = {"sequence": self._sequence, "kind": kind, "monotonic_ns": observed_ns,
                 "audit_utc": datetime.now(timezone.utc).isoformat(), "session_id": self.contract["session_id"], **details}
        with (self.capture / "events.jsonl").open("ab") as stream:
            stream.write(_json_bytes(event)); stream.flush(); os.fsync(stream.fileno())

    def fail(self, code):
        if code not in self.issues: self.issues.append(code)
        raise NativeSessionError(code)

    def release(self, observed_ns):
        if self.release_ns is not None: self.fail("duplicate_prompt_release")
        self.release_ns = observed_ns
        self._event("prompt_released", observed_ns, input_sha256=_sha(_json_bytes(self.contract["input"])))

    def observe(self, event, observed_ns):
        if not isinstance(event, dict): self.fail("malformed_native_event")
        self._event("native_event", observed_ns, metadata=event_metadata(event))
        if event.get("type") == "rate_limit_event":
            usage = _usage_metadata(event)
            if usage is not None: self.usage.append(usage)
            if usage and usage.get("isUsingOverage") is True: self.fail("unexpected_paid_overage")
            if usage and usage.get("status") == "rejected": self.fail("provider_rate_limited")
        if event.get("api_error_status") in (401, 403, 429): self.fail("provider_access_rejected")
        if event.get("type") == "system" and event.get("subtype") == "init":
            if self.initialized: self.fail("duplicate_native_init")
            if (event.get("session_id") != self.contract["session_id"] or event.get("model") not in (MODEL, CLI_MODEL)
                    or event.get("claude_code_version") != CLI_VERSION): self.fail("native_identity_mismatch")
            tools = event.get("tools")
            if not isinstance(tools, list) or any(not isinstance(tool, str) for tool in tools) or sorted("Agent" if tool == "Task" else tool for tool in tools) != sorted(self.contract["tools"]):
                self.fail("native_capability_mismatch")
            if any(event.get(key) != [] for key in ("mcp_servers", "skills", "plugins")): self.fail("unexpected_extension")
            self.initialized = True
            self.init_evidence = {key: event[key] for key in ("model", "claude_code_version", "tools", "mcp_servers", "skills", "plugins")}
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        if message.get("model") not in (None, MODEL): self.fail("response_model_mismatch")
        if set(event_metadata(event)["tool_names"]) - set(self.contract["tools"]): self.fail("observed_tool_escalation")
        if event.get("type") == "result":
            if self.result_ns is not None: self.fail("duplicate_native_result")
            if (not self.initialized or event.get("session_id") != self.contract["session_id"] or event.get("subtype") != "success"
                    or event.get("is_error") is not False or not isinstance(event.get("result"), str)):
                self.fail("invalid_native_result")
            data = event["result"].encode()
            _write_new(self.capture / "sealed-answer.txt", data)
            self.result_ns = observed_ns; self.answer_sha256 = _sha(data)
            self._event("answer_sealed", observed_ns, sha256=self.answer_sha256)

    def root_exit(self, code, observed_ns):
        if self.root_exit_ns is not None: self.fail("duplicate_root_exit")
        self.exit_code = code; self.root_exit_ns = observed_ns
        self._event("root_process_exited", observed_ns, exit_code=code)

    def drain(self, observed_ns, *, census_empty, waitpid_echild):
        if self.root_exit_ns is None or not census_empty or not waitpid_echild:
            if "owned_work_unresolved" not in self.issues: self.issues.append("owned_work_unresolved")
            return
        self.drain_ns = observed_ns
        self._event("owned_work_drained", observed_ns)
        if self.result_ns is not None and self.exit_code == 0 and not self.issues and not self.cancelled:
            self.terminal_ns = max(self.result_ns, observed_ns)
            self._event("native_terminal", self.terminal_ns, sealed_answer_sha256=self.answer_sha256)

    def metadata(self):
        valid = self.terminal_ns is not None and not self.issues and not self.cancelled
        return {"session_id": self.contract["session_id"], "prompt_released_monotonic_ns": self.release_ns,
                "result_monotonic_ns": self.result_ns, "root_exit_monotonic_ns": self.root_exit_ns,
                "owned_work_drained_monotonic_ns": self.drain_ns, "native_terminal_monotonic_ns": self.terminal_ns,
                "runtime_seconds": (self.terminal_ns - self.release_ns) / 1e9 if valid else None,
                "timing_valid": valid, "root_exit_code": self.exit_code, "issues": list(self.issues),
                "sealed_answer_sha256": self.answer_sha256, "usage_evidence": list(self.usage), "cancelled": self.cancelled,
                "observed_native_init": self.init_evidence,
                "readiness": readiness()}


def _owned_processes():
    table = {}
    for folder in Path("/proc").iterdir():
        if not folder.name.isdigit(): continue
        try:
            parts = (folder / "stat").read_text().rsplit(") ", 1)[1].split()
            table[int(folder.name)] = (int(parts[1]), int(parts[19]))
        except (OSError, ValueError, IndexError): continue
    owned = {}; frontier = {os.getpid()}
    while frontier:
        children = {pid for pid, (parent, _) in table.items() if parent in frontier and pid not in owned}
        for pid in children: owned[pid] = table[pid][1]
        frontier = children
    return owned


def _signal_owned(identities, sig):
    current = _owned_processes()
    for pid, start in identities.items():
        if current.get(pid) == start:
            try: os.kill(pid, sig)
            except ProcessLookupError: pass


def supervise(command, environment, cwd, input_bytes, boundary):
    """Own one process tree in a dedicated Linux entry process, with no time cap."""
    if sys.platform != "linux": raise NativeSessionError("linux_subreaper_required")
    if _owned_processes(): raise NativeSessionError("entry_process_already_has_children")
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0: raise NativeSessionError("cannot_enable_subreaper")
    interrupted = []; old_handlers = {}
    def request_stop(number, frame): interrupted.append(number)
    for number in (signal.SIGTERM, signal.SIGINT):
        old_handlers[number] = signal.signal(number, request_stop)
    capture = boundary.capture
    proc = None; stop_started = None
    try:
        with (capture / "stream.jsonl").open("wb") as output, (capture / "stderr.txt").open("wb") as error:
            proc = subprocess.Popen(command, cwd=cwd, env=environment, stdin=subprocess.PIPE, stdout=output, stderr=error,
                                    start_new_session=True)
            boundary._event("root_process_started", time.monotonic_ns(), pid=proc.pid, owner_pid=os.getpid())
            # Record just before delivery so fast native work cannot precede onset.
            # The separate completion event exposes observer/write overhead.
            boundary.release(time.monotonic_ns())
            proc.stdin.write(input_bytes); proc.stdin.flush(); proc.stdin.close()
            boundary._event("prompt_delivery_completed", time.monotonic_ns(), bytes=len(input_bytes))
            with (capture / "stream.jsonl").open("rb") as events:
                def consume():
                    while True:
                        position = events.tell(); line = events.readline()
                        if not line or not line.endswith(b"\n"):
                            events.seek(position); return
                        try: parsed = json.loads(line)
                        except (ValueError, UnicodeDecodeError):
                            if "native_stream_invalid" not in boundary.issues: boundary.issues.append("native_stream_invalid")
                            interrupted.append("invalid_stream"); continue
                        try: boundary.observe(parsed, time.monotonic_ns())
                        except NativeSessionError:
                            # fail() retained the specific reason. It cannot later
                            # become a valid terminal even if a success reply follows.
                            interrupted.append("boundary_integrity_failure")
                while True:
                    consume()
                    echild = False
                    while True:
                        try: pid, result = os.waitpid(-1, os.WNOHANG)
                        except ChildProcessError: echild = True; break
                        if pid == 0: break
                        code = os.waitstatus_to_exitcode(result)
                        boundary._event("owned_process_reaped", time.monotonic_ns(), pid=pid, exit_code=code)
                        if pid == proc.pid:
                            proc.returncode = code; boundary.root_exit(code, time.monotonic_ns())
                    owned = _owned_processes()
                    if boundary.root_exit_ns is not None and echild and not owned:
                        consume()
                        tail = events.read()
                        if tail.strip(): boundary.issues.append("unterminated_native_stream")
                        boundary.drain(time.monotonic_ns(), census_empty=True, waitpid_echild=True)
                        break
                    if interrupted:
                        boundary.cancelled = True
                        if stop_started is None:
                            stop_started = time.monotonic()
                            boundary._event("execution_stop_requested", time.monotonic_ns(),
                                            reason="external_signal" if isinstance(interrupted[0], int) else interrupted[0])
                        _signal_owned(owned, signal.SIGKILL if time.monotonic() - stop_started >= 5 else signal.SIGTERM)
                    time.sleep(.01)
        boundary._event("journal_closed", time.monotonic_ns())
        return boundary.metadata()
    finally:
        # Unexpected entry errors retain files and mark the attempt, never replay it.
        if proc is not None and boundary.drain_ns is None:
            boundary.cancelled = True
            _signal_owned(_owned_processes(), signal.SIGTERM)
        for number, handler in old_handlers.items(): signal.signal(number, handler)


def initial_message_matches(actual, expected, root, session_id):
    """Accept only the pinned CLI's image-path annotation, preserving all bytes."""
    if actual == expected:
        return True
    if not isinstance(actual, dict) or set(actual) != set(expected) or actual.get("role") != expected.get("role"):
        return False
    original = expected.get("content")
    if not isinstance(original, list) or not any(b.get("type") == "image" for b in original if isinstance(b, dict)):
        return False
    expanded = []; ordinal = 0
    slug = str(Path(root) / "work").replace("/", "-")
    for block in original:
        expanded.append(block)
        if isinstance(block, dict) and block.get("type") == "image":
            ordinal += 1
            path = Path(root) / "tmp" / "claude-10001" / slug / session_id / "images" / f"{ordinal}.png"
            expanded.append({"type": "text", "text": f"[Image: source: {path}]"})
    return actual == {**expected, "content": expanded}


def _complete_native_response_ids(raw, session_id):
    """Return response identities only from a complete original native stream."""
    stream = raw.parent / "stream.jsonl"
    if stream.is_symlink():
        return set()
    try:
        data = stream.read_bytes()
        if not data.endswith(b"\n"):
            return set()
        events = [json.loads(line) for line in data.splitlines() if line.strip()]
        results = [e for e in events if isinstance(e, dict) and e.get("type") == "result"]
        if len(results) != 1:
            return set()
        result = results[0]
        if result.get("session_id") != session_id or result.get("subtype") != "success" or result.get("is_error") is not False:
            return set()
        ids = set()
        for event in events:
            if not isinstance(event, dict):
                return set()
            message = event.get("message")
            if (event.get("type") == "assistant" and event.get("session_id") == session_id
                    and isinstance(message, dict) and message.get("type") == "message"
                    and message.get("model") == MODEL and isinstance(message.get("id"), str)):
                ids.add(message["id"])
        return ids
    except (OSError, ValueError, TypeError):
        return set()


def inspect_transport(raw, contract):
    """Synthetic-only entry point; validates the fixed qualification contract."""
    validate_contract(contract)
    return inspect_transport_records(raw, contract)


def inspect_transport_records(raw, contract):
    """Pure capture checker. Caller must validate and pin its execution contract.

    Accepts only the current pinned model, effort and explicit capability list.
    This function neither authorizes execution nor marks a study ready.
    """
    raw = Path(raw); issues = []; request_count = 0; session_ids = set()
    native_ids = _complete_native_response_ids(raw, contract["session_id"])
    native_fallback_ids = []
    def add(code):
        if code not in issues: issues.append(code)
    try:
        records = [json.loads(line) for line in (raw / "index.jsonl").read_text().splitlines() if line.strip()]
        files = {p.name for p in raw.glob("*.request.json") if p.is_file() and not p.is_symlink()}
        referenced = set()
        for record in records:
            name = record.get("request_file")
            if not isinstance(name, str) or Path(name).name != name or name not in files or name in referenced:
                add("invalid_transport_reference"); continue
            referenced.add(name); body = _read_json(raw / name); request_count += 1
            if body.get("model") != MODEL or record.get("model") != MODEL: add("request_model_mismatch")
            if body.get("output_config", {}).get("effort") != "max": add("effort_mismatch")
            if body.get("fallbacks") not in (None, []): add("fallback_configured")
            tools = body.get("tools")
            source = record.get("query_source", "sdk")
            if source == "web_search_tool":
                # Claude Code's WebSearch backend is a separate native request,
                # with a server-side tool. It is not an extra subject capability.
                if "WebSearch" not in contract["tools"] or tools != [{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}]:
                    add("request_capability_mismatch")
            elif not isinstance(tools, list) or any(not isinstance(t, dict) or t.get("name") not in contract["tools"] for t in tools):
                add("request_capability_mismatch")
            if not record.get("request_id"): add("transport_acceptance_unproven")
            response_name = record.get("response_file")
            if not isinstance(response_name, str) or Path(response_name).name != response_name:
                add("transport_response_unavailable")
            else:
                try: response = _read_json(raw / response_name)
                except NativeSessionError:
                    message_id = record.get("message_id")
                    if (source == "sdk" and record.get("session_id") == contract["session_id"]
                            and message_id in native_ids):
                        native_fallback_ids.append(message_id)
                    else:
                        add("transport_response_unavailable")
                else:
                    if (response.get("type") != "message" or not response.get("id") or response.get("id") != record.get("message_id")
                            or response.get("model") != MODEL): add("transport_response_mismatch")
            sid = record.get("session_id")
            if not isinstance(sid, str): add("transport_session_unproven")
            else: session_ids.add(sid)
            if request_count == 1:
                if (not isinstance(tools, list) or any(not isinstance(t, dict) for t in tools)
                        or sorted(t.get("name", "") for t in tools) != sorted(contract["tools"])):
                    add("initial_request_capability_mismatch")
                messages = body.get("messages", [])
                if not messages or not initial_message_matches(messages[0], contract["input"]["message"], raw.parent.parent, contract["session_id"]):
                    add("delivered_input_mismatch")
        if files != referenced or not referenced: add("transport_coverage_incomplete")
        if contract["session_id"] not in session_ids: add("transport_session_mismatch")
        if contract["case"] != "web_subagent" and session_ids - {contract["session_id"]}: add("unexpected_child_session")
    except (OSError, ValueError, TypeError, AttributeError): add("transport_capture_unreadable")
    return {"request_count": request_count, "session_ids": sorted(session_ids), "issues": issues,
            "native_stream_response_ids": native_fallback_ids, "descendant_capabilities_qualified": False}


def capture_archive(root, secret):
    root = Path(root)
    if not isinstance(secret, str) or len(secret) < 12: raise NativeSessionError("invalid_secret_scan")
    before = inventory(root, secret=secret)
    destination = root / "archive"
    if destination.exists() or destination.is_symlink(): raise NativeSessionError("archive_already_exists")
    destination.mkdir(mode=0o700); destination.chmod(0o700)
    directory_modes = {}
    # Explicit state roots only. Account homes and provider configuration are never imported.
    for relative, entry in before.items():
        target = destination / relative
        if entry["type"] == "directory":
            # Keep the copy writable and private while populating it. mkdir's
            # mode is filtered by umask; source directories may also be read-only.
            target.mkdir(mode=0o700, parents=True, exist_ok=True); target.chmod(0o700)
            directory_modes[target] = entry["mode"]
        elif entry["type"] == "file":
            shutil.copy2(root / relative, target)
            with target.open("rb") as stream: os.fsync(stream.fileno())
        else: target.symlink_to(os.path.relpath(destination / entry["target"], target.parent))
    for folder, mode in sorted(directory_modes.items(), key=lambda item: len(item[0].parts), reverse=True):
        folder.chmod(mode)
    if inventory(root, secret=secret) != before or inventory(destination, secret=secret) != before:
        raise NativeSessionError("archive_changed_during_capture")
    for folder in sorted((p for p in destination.rglob("*") if p.is_dir() and not p.is_symlink()), key=lambda p: len(p.parts), reverse=True) + [destination]:
        descriptor = os.open(folder, os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
    receipt = {"schema_version": "agenttime.native-local-archive.v1", "inventory": before,
               "inventory_sha256": _sha(_json_bytes(before)), "verified": True, "native_restore_qualified": False,
               "native_fork_qualified": False, "independent_mac_acknowledgement": False,
               "symlinks_rebased_within_archive": True}
    _write_new(root / "archive-receipt.json", _json_bytes(receipt))
    return receipt


def run_prepared(root, acknowledgement_path, binary, token, *, offline_base_url=None):
    """Run one synthetic qualification after independent baseline acknowledgement."""
    root = Path(root).absolute()
    if root.is_symlink(): raise NativeSessionError("unsafe_session_root")
    contract = _read_json(root / "contract.json")
    validate_contract(contract)
    # Keep a single immutable sequence from the in-memory contract through
    # acknowledgement, onset evidence and delivery. Never reopen a validated
    # payload path at the delivery boundary.
    contract_bytes = _json_bytes(contract)
    input_bytes = _json_bytes(contract["input"])
    verify_binary(binary)
    acknowledgement = _read_json(acknowledgement_path)
    verify_baseline(root, contract, acknowledgement)
    conf = configuration(root, binary, contract, token, offline_base_url=offline_base_url)
    # Atomic, permanent onset intent. An incomplete intent never permits another launch.
    try: _write_new(root / "run-started.json", _json_bytes({"session_id": contract["session_id"], "input_sha256": _sha(input_bytes),
                        "contract_sha256": _sha(contract_bytes), "source_sha256": _source_pin(), "cli_sha256": CLI_SHA256}))
    except FileExistsError: raise NativeSessionError("qualification_already_claimed") from None
    boundary = NativeBoundary(root / "capture", contract)
    report = {"qualification_id": contract["qualification_id"], "session_id": contract["session_id"],
              "requested_route": contract["transport"], "configured_authentication": "stdin_to_child_environment_only",
              "readiness": readiness(), "automatic_retry": False, "experiment_time_cap_seconds": None,
              "archive_verified": False, "included_subscription_allowance_verified": False, "qualified": False}
    try:
        _write_new(root / "capture" / "delivered-input.json", input_bytes)
        report["execution"] = supervise(conf["command"], conf["environment"], root / "work", input_bytes, boundary)
        transport = inspect_transport(root / "capture" / "raw-bodies", contract)
        report["transport"] = transport
        usage = boundary.usage
        report["included_subscription_allowance_verified"] = subscription_allowance_verified(usage)
        report["archive"] = capture_archive(root, token)
        report["archive_verified"] = True
        report["qualified"] = bool(report["execution"]["timing_valid"] and not transport["issues"]
            and (contract["transport"] == "offline_loopback" or report["included_subscription_allowance_verified"]))
    except (NativeSessionError, OSError, subprocess.SubprocessError) as exc:
        # Never persist exception text from a process/API or an environment containing authentication.
        report["error_code"] = str(exc) if isinstance(exc, NativeSessionError) else type(exc).__name__
        report["execution"] = boundary.metadata()
        if boundary.drain_ns is not None and not (root / "archive").exists():
            try:
                report["archive"] = capture_archive(root, token); report["archive_verified"] = True
            except (NativeSessionError, OSError): report["archive_error"] = "capture_held_preserve_original_state"
        else:
            report["archive_error"] = "capture_held_preserve_original_state"
    _write_new(root / "report.json", _json_bytes(report))
    return report
