"""Offline production-worker boundaries; no model, credentials or provider calls."""
import base64
import copy
import hashlib
import importlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
import threading
from unittest.mock import patch
from uuid import uuid4

from agenttime import native_session as ns


TOKEN = "synthetic-worker-secret-not-real-authentication"
SESSION = "2535f4d2-daa6-47b7-ad93-32fbf7f07a04"
ATTEMPT = "3f3f3895-9709-48d4-9a30-ce32ed21a190"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical_hash(value):
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode())


class NaturalWorkerTests(unittest.TestCase):
    def setUp(self):
        try: self.worker = importlib.import_module("agenttime.natural_worker")
        except ModuleNotFoundError: self.fail("Production natural worker is not implemented")
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.input = ns._json_bytes({"type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": "SYNTHETIC TASK MATERIAL. Return a short response."}]}})

    def spec(self, family="gpqa"):
        tools = ["Agent", "WebSearch", "WebFetch"] if family == "browsecomp" else []
        mcp = list(self.worker.ASSISTANT_TOOLS) if family == "assistant" else []
        return {"schema_version": "agenttime.natural-worker.v1", "executor": "claude-natural-v1", "arm": "natural",
            "attempt_id": ATTEMPT, "task_id": family + "-01", "family_id": family, "session_id": SESSION,
            "identity": {"worker_id": "synthetic-worker", "session_id": SESSION, "host_id": "synthetic-host",
                "daemon_id": "synthetic-daemon", "docker_endpoint": "unix:///var/run/docker.sock", "container_id": "a" * 64},
            "preparation_contract_sha256": "b" * 64, "input_sha256": sha(self.input),
            "model": ns.CLI_MODEL, "effort": "max", "route": "subscription",
            "tool_policy": {"native_tools": tools, "mcp_tools": mcp, "native_subagents": family == "browsecomp"},
            "resources": {"allocation_scope": "no_task_compute" if family in {"gpqa", "hle"} else "natural_subject",
                "cpus": None if family in {"gpqa", "hle"} else 4,
                "memory_gib": None if family in {"gpqa", "hle"} else 16, "gpu_count": 0},
            "runtime_pins": {"cli_version": ns.CLI_VERSION, "cli_sha256": ns.CLI_SHA256,
                "image_sha256": "sha256:" + "c" * 64, **self.worker.source_pins(family)}}

    def prepared(self, family="gpqa"):
        root = self.base / (family + "-session"); spec = self.spec(family)
        self.worker.prepare(root, spec, self.input)
        return root, spec

    def acknowledgements(self, root, spec):
        baseline = json.loads((root / "baseline.json").read_bytes())
        ack = {"schema_version": "agenttime.natural-baseline-ack.v1", "attempt_id": ATTEMPT,
            "identity_sha256": canonical_hash(spec["identity"]), "session_id": SESSION,
            "contract_sha256": spec["preparation_contract_sha256"], "input_sha256": spec["input_sha256"],
            "inventory_sha256": baseline["inventory_sha256"], "archive_manifest_sha256": "d" * 64,
            "archive_verified": True, "independent_archive": True, "archive_location": "mac:synthetic-private-baseline"}
        grant = {"schema_version": "agenttime.natural-release-authorization.v1", "attempt_id": ATTEMPT,
            "identity_sha256": canonical_hash(spec["identity"]), "spec_sha256": sha((root / "spec.json").read_bytes()),
            "input_sha256": spec["input_sha256"], "baseline_ack_sha256": canonical_hash(ack),
            "authorization_id": str(uuid4()), "campaign_id": "synthetic-pilot", "manifest_sha256": "e" * 64,
            "ledger_authorized": True}
        ackpath = root / "baseline-ack.json"; grantpath = root / "release-authorization.json"
        ackpath.write_text(json.dumps(ack)); grantpath.write_text(json.dumps(grant))
        return ackpath, grantpath, ack, grant

    def init(self, spec, **changes):
        tools = spec["tool_policy"]["native_tools"] + spec["tool_policy"]["mcp_tools"]
        # Startup alias differs from the API-layer Agent tool name in this CLI.
        tools = ["Task" if name == "Agent" else name for name in tools]
        event = {"type": "system", "subtype": "init", "session_id": SESSION, "model": ns.CLI_MODEL,
            "claude_code_version": ns.CLI_VERSION, "tools": tools, "skills": [], "plugins": [],
            "mcp_servers": [{"name": "assistantbench", "status": "connected"}] if spec["family_id"] == "assistant" else []}
        return {**event, **changes}

    def result(self, answer="synthetic private final answer"):
        return {"type": "result", "subtype": "success", "is_error": False, "session_id": SESSION, "result": answer}

    def native_store(self, root, session=SESSION):
        slug = re.sub(r"[^a-zA-Z0-9-]", "-", str(root / "work"))
        return root / "config" / "projects" / slug / (session + ".jsonl")

    def simulate(self, command, environment, cwd, input_bytes, boundary):
        now = time.monotonic_ns(); boundary.release(now)
        init = self.init(boundary.spec, cwd=str(cwd))
        usage = {"type": "rate_limit_event", "rate_limit_info": {
            "status": "allowed", "isUsingOverage": False, "overageStatus": "rejected", "hasExtraUsage": False}}
        answer = self.result()
        assistant = {"type": "assistant", "session_id": SESSION, "uuid": str(uuid4()),
            "parent_tool_use_id": None, "request_id": "req_synthetic", "message": {
                "type": "message", "role": "assistant", "model": ns.MODEL, "id": "msg_synthetic",
                "content": [{"type": "text", "text": answer["result"]}]}}
        stream = [init, usage, assistant, answer]
        browser = None
        for index, row in enumerate(stream):
            if boundary.spec['family_id'] == 'assistant' and row['type'] == 'result':
                from agenttime.assistantbench_bridge import AssistantBenchBridge
                from test_assistantbench_bridge import FakeBackend
                backend = FakeBackend(); snapshot = backend.snapshot()
                snapshot['goal_sha256'] = sha((Path(cwd).parent / 'capture/browser-goal.txt').read_bytes())
                backend.snapshot = lambda: snapshot
                browser = AssistantBenchBridge(backend, Path(cwd).parent / 'capture/browser-state', ATTEMPT)
                browser.call_tool('send_msg_to_user', {'text': answer['result']})
            boundary.observe(row, time.monotonic_ns())
        if browser is not None: browser.close()
        root = Path(cwd).parent; store = self.native_store(root); store.parent.mkdir(parents=True)
        user_id = str(uuid4())
        common = {"sessionId": SESSION, "cwd": str(cwd), "version": ns.CLI_VERSION, "isSidechain": False}
        records = [{**common, "type": "user", "uuid": user_id, "parentUuid": None,
            "message": json.loads(input_bytes)["message"]}, {**common, "type": "assistant", "uuid": assistant["uuid"],
            "parentUuid": user_id, "requestId": assistant["request_id"], "message": assistant["message"]}]
        store.write_bytes(b"".join(ns._json_bytes(row) for row in records))
        (root / "capture/stream.jsonl").write_bytes(b"".join(ns._json_bytes(row) for row in stream))
        boundary.root_exit(0, time.monotonic_ns()); boundary.drain(time.monotonic_ns(), census_empty=True, waitpid_echild=True)
        return boundary.metadata()

    def add_child_evidence(self, root):
        store = self.native_store(root); parent = [json.loads(line) for line in store.read_bytes().splitlines()]
        stream_file = root / "capture/stream.jsonl"; stream = [json.loads(line) for line in stream_file.read_bytes().splitlines()]
        child_id = "asyntheticchild"; tool_id = "toolu_synthetic_agent"; call_uuid = str(uuid4()); result_uuid = str(uuid4())
        call_message = {"type": "message", "role": "assistant", "id": "msg_agent_call", "model": ns.MODEL,
            "content": [{"type": "tool_use", "name": "Agent", "id": tool_id,
                "input": {"prompt": "Synthetic child request", "subagent_type": "general-purpose"}}]}
        returned = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id,
            "content": "Synthetic child response"}]}
        common = {k: parent[0][k] for k in ("sessionId", "cwd", "version", "isSidechain")}
        parent.insert(1, {**common, "type": "assistant", "uuid": call_uuid, "parentUuid": parent[0]["uuid"],
            "requestId": "req_agent_call", "message": call_message})
        parent.insert(2, {**common, "type": "user", "uuid": result_uuid, "parentUuid": call_uuid,
            "message": returned, "toolUseResult": {"agentId": child_id, "status": "completed",
                "content": [{"type": "text", "text": "Synthetic child response"}]}})
        parent[-1]["parentUuid"] = result_uuid
        store.write_bytes(b"".join(ns._json_bytes(row) for row in parent))
        child_user = {**common, "isSidechain": True, "agentId": child_id, "type": "user", "uuid": str(uuid4()),
            "parentUuid": None, "message": {"role": "user", "content": "Synthetic child request"}}
        child_assistant = {**common, "isSidechain": True, "agentId": child_id, "type": "assistant", "uuid": str(uuid4()),
            "parentUuid": child_user["uuid"], "requestId": "req_child", "message": {"type": "message", "role": "assistant",
                "model": ns.MODEL, "id": "msg_child", "content": [{"type": "text", "text": "Synthetic child response"}]}}
        child_file = store.with_suffix("") / "subagents" / ("agent-" + child_id + ".jsonl")
        child_file.parent.mkdir(parents=True); child_file.write_bytes(ns._json_bytes(child_user) + ns._json_bytes(child_assistant))
        added = [
            {"type": "assistant", "session_id": SESSION, "parent_tool_use_id": None, "uuid": call_uuid,
                "request_id": "req_agent_call", "message": call_message},
            {"type": "system", "subtype": "task_started", "session_id": SESSION, "task_type": "local_agent",
                "task_id": child_id, "tool_use_id": tool_id},
            {"type": "user", "session_id": SESSION, "parent_tool_use_id": tool_id, "uuid": child_user["uuid"],
                "message": {"role": "user", "content": [{"type": "text", "text": "Synthetic child request"}]}},
            {"type": "assistant", "session_id": SESSION, "parent_tool_use_id": tool_id, "uuid": child_assistant["uuid"],
                "request_id": "req_child", "message": child_assistant["message"]},
            {"type": "system", "subtype": "task_notification", "session_id": SESSION, "task_id": child_id,
                "tool_use_id": tool_id, "status": "completed"},
            {"type": "user", "session_id": SESSION, "parent_tool_use_id": None, "uuid": result_uuid,
                "message": returned, "tool_use_result": {"agentId": child_id, "status": "completed",
                "content": [{"type": "text", "text": "Synthetic child response"}]}},
        ]
        stream[-2:-2] = added
        stream[-1]["subagent_stats"] = {"spawned": 1, "completed": 1}
        stream_file.write_bytes(b"".join(ns._json_bytes(row) for row in stream))
        return child_file

    def run_fake(self, root, spec, *, simulate=None):
        ackpath, grantpath, _, _ = self.acknowledgements(root, spec)
        with patch.object(self.worker, "verify_runtime", return_value=None), \
             patch.object(ns, "supervise", side_effect=simulate or self.simulate), \
             patch.object(ns, "inspect_transport_records", return_value={"issues": [], "request_count": 1}):
            return self.worker.run_prepared(root, ackpath, grantpath, "/unused/claude", TOKEN)

    def test_spec_rejects_other_scope_models_routes_tools_and_source_pins(self):
        spec = self.spec()
        for key, value in (("arm", "timed"), ("model", "another-model"), ("route", "anthropic_api"),
            ("effort", "low"), ("executor", "fixture-v1"), ("task_id", "gpqa-99"), ("session_id", str(uuid4()))):
            with self.subTest(key=key), self.assertRaises(ns.NativeSessionError):
                self.worker.validate_spec({**spec, key: value}, self.input)
        for path, value in (("native_tools", ["Bash"]), ("native_subagents", True), ("mcp_tools", ["unknown"])):
            bad = copy.deepcopy(spec); bad["tool_policy"][path] = value
            with self.subTest(path=path), self.assertRaises(ns.NativeSessionError): self.worker.validate_spec(bad, self.input)
        bad = copy.deepcopy(spec); bad["runtime_pins"]["worker_sha256"] = "0" * 64
        with self.assertRaises(ns.NativeSessionError): self.worker.validate_spec(bad, self.input)

    def test_input_requires_exact_hash_one_user_event_and_native_text_or_images(self):
        spec = self.spec()
        bad_inputs = [self.input + self.input, b'{"type":"user","type":"user"}\n',
            ns._json_bytes({"type": "user", "message": {"role": "system", "content": "secret"}}),
            ns._json_bytes({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "answer"}]}})]
        for data in bad_inputs:
            with self.subTest(data=data), self.assertRaises(ns.NativeSessionError):
                self.worker.validate_spec({**spec, "input_sha256": sha(data)}, data)
        with self.assertRaises(ns.NativeSessionError): self.worker.validate_spec(spec, self.input.replace(b"SYNTHETIC", b"CHANGED"))

    def test_selected_image_tasks_require_valid_embedded_png_and_exact_image_count(self):
        spec = self.spec("hle"); spec["task_id"] = "hle-03"
        with self.assertRaises(ns.NativeSessionError): self.worker.validate_spec(spec, self.input)
        value = json.loads(self.input); value["message"]["content"].append({"type": "image", "source": {
            "type": "base64", "media_type": "image/png", "data": ns._synthetic_image()}})
        data = ns._json_bytes(value); spec["input_sha256"] = sha(data)
        self.worker.validate_spec(spec, data)
        value["message"]["content"][-1]["source"]["data"] = base64.b64encode(b"not png").decode()
        data = ns._json_bytes(value)
        with self.assertRaises(ns.NativeSessionError): self.worker.validate_spec({**spec, "input_sha256": sha(data)}, data)

    def test_web_resources_and_canonical_agent_policy_are_frozen(self):
        spec = self.spec("browsecomp"); self.worker.validate_spec(spec, self.input)
        for key, value in (("cpus", 2), ("memory_gib", 8), ("gpu_count", 1)):
            bad = copy.deepcopy(spec); bad["resources"][key] = value
            with self.subTest(key=key), self.assertRaises(ns.NativeSessionError): self.worker.validate_spec(bad, self.input)
        bad = copy.deepcopy(spec); bad["tool_policy"]["native_tools"][0] = "Task"
        with self.assertRaises(ns.NativeSessionError): self.worker.validate_spec(bad, self.input)

    def test_prepare_creates_fresh_isolated_state_and_never_overwrites(self):
        root, spec = self.prepared()
        self.assertEqual((root / "input.json").read_bytes(), self.input)
        self.assertEqual((root / "capture" / "prepared-input.json").read_bytes(), self.input)
        for name in ("home", "config", "tmp", "work", "xdg"):
            self.assertEqual(list((root / name).iterdir()), [])
        with self.assertRaises(ns.NativeSessionError): self.worker.prepare(root, spec, self.input)
        self.assertFalse((root / "run-started.json").exists())

    def test_ack_and_release_grant_bind_all_staged_state_and_identity(self):
        root, spec = self.prepared(); _, _, ack, grant = self.acknowledgements(root, spec)
        self.worker.verify_release(root, spec, self.input, ack, grant)
        for key, value in (("archive_verified", False), ("independent_archive", False), ("session_id", str(uuid4())),
                           ("input_sha256", "0" * 64), ("inventory_sha256", "0" * 64)):
            with self.subTest(ack=key), self.assertRaises(ns.NativeSessionError):
                self.worker.verify_release(root, spec, self.input, {**ack, key: value}, grant)
        for key, value in (("ledger_authorized", False), ("attempt_id", str(uuid4())), ("spec_sha256", "0" * 64),
                           ("baseline_ack_sha256", "0" * 64), ("identity_sha256", "0" * 64)):
            with self.subTest(grant=key), self.assertRaises(ns.NativeSessionError):
                self.worker.verify_release(root, spec, self.input, ack, {**grant, key: value})
        (root / "work" / "memory.txt").write_text("not pristine")
        with self.assertRaises(ns.NativeSessionError): self.worker.verify_release(root, spec, self.input, ack, grant)

    def test_configuration_uses_only_stdin_secret_environment_and_no_cap(self):
        root, spec = self.prepared()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "bad", "ANTHROPIC_BASE_URL": "https://bad.example", "HOME": "/personal"}):
            conf = self.worker.configuration(root, "/runtime/claude", spec, TOKEN)
        cmd, env = conf["command"], conf["environment"]
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], TOKEN)
        self.assertNotIn("ANTHROPIC_API_KEY", env); self.assertNotIn("ANTHROPIC_BASE_URL", env)
        self.assertNotIn(TOKEN, json.dumps(cmd)); self.assertNotIn("/personal", json.dumps(conf))
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        for option in ("--max-turns", "--max-budget-usd", "--resume", "--continue", "--fallback-model"):
            self.assertNotIn(option, cmd)

    def test_assistant_has_exact_mcp_tools_and_auth_free_browser_command(self):
        root, spec = self.prepared("assistant")
        conf = self.worker.configuration(root, "/runtime/claude", spec, TOKEN)
        cmd = conf["command"]; config = json.loads(cmd[cmd.index("--mcp-config") + 1])
        self.assertEqual(list(config["mcpServers"]), ["assistantbench"])
        server = config["mcpServers"]["assistantbench"]
        self.assertEqual(server["command"], "/usr/bin/env"); self.assertEqual(server["args"][0], "-i")
        self.assertNotIn(TOKEN, json.dumps(server)); self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", json.dumps(server))
        self.assertIn("PLAYWRIGHT_BROWSERS_PATH=/opt/playwright", server["args"])
        self.assertIn(str(Path(sys.executable).absolute()), server["args"])
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertEqual(len(spec["tool_policy"]["mcp_tools"]), 10)
        bad = copy.deepcopy(spec); bad["tool_policy"]["mcp_tools"].append("mcp__assistantbench__Bash")
        with self.assertRaises(ns.NativeSessionError): self.worker.configuration(root, "/runtime/claude", bad, TOKEN)

    def test_runtime_pin_checks_actual_binary_python_and_source(self):
        spec = self.spec(); binary = self.base / "claude"; binary.write_text("not the pinned CLI")
        with self.assertRaises(ns.NativeSessionError): self.worker.verify_runtime(binary, spec)
        bad = copy.deepcopy(spec); bad["runtime_pins"]["python_sha256"] = "0" * 64
        with patch.object(ns, "verify_binary", return_value=None), self.assertRaises(ns.NativeSessionError):
            self.worker.verify_runtime(binary, bad)

    def test_runtime_requires_linux_and_an_identifiable_monotonic_clock(self):
        spec = self.spec()
        with patch.object(ns, "verify_binary", return_value=None), patch.object(sys, "platform", "darwin"), \
             self.assertRaisesRegex(ns.NativeSessionError, "linux_worker_required"):
            self.worker.verify_runtime("/unused/claude", spec)

    def test_onset_intent_is_permanent_after_unknown_launch_failure(self):
        root, spec = self.prepared()
        def failed(*args): raise OSError("secret exception body " + TOKEN)
        report = self.run_fake(root, spec, simulate=failed)
        self.assertEqual(report["state"], "held")
        self.assertTrue((root / "run-started.json").exists())
        ackpath = root / "baseline-ack.json"; grantpath = root / "release-authorization.json"
        with self.assertRaisesRegex(ns.NativeSessionError, "attempt_already_started"):
            self.worker.run_prepared(root, ackpath, grantpath, "/unused/claude", TOKEN)
        self.assertNotIn(TOKEN, (root / "report.json").read_text())
        self.assertFalse(report["archive_verified"])

    def test_two_competing_entries_cannot_launch_twice(self):
        root, spec = self.prepared(); ackpath, grantpath, _, _ = self.acknowledgements(root, spec)
        barrier = threading.Barrier(2); original_verify = self.worker.verify_release; calls = []
        def verified_together(*args):
            original_verify(*args); barrier.wait(timeout=5)
        def interrupted(*args):
            calls.append(True); raise OSError("synthetic launch interruption")
        def run():
            try: return self.worker.run_prepared(root, ackpath, grantpath, "/unused/claude", TOKEN)
            except ns.NativeSessionError as exc: return str(exc)
        with patch.object(self.worker, "verify_runtime", return_value=None), \
             patch.object(self.worker, "verify_release", side_effect=verified_together), \
             patch.object(ns, "supervise", side_effect=interrupted), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: run(), range(2)))
        self.assertEqual(len(calls), 1); self.assertIn("attempt_already_started", results)

    def test_delivery_uses_the_verified_read_once_bytes_after_disk_changes(self):
        root, spec = self.prepared(); ackpath, grantpath, _, _ = self.acknowledgements(root, spec)
        original_verify = self.worker.verify_release; delivered = []
        def mutate(*args):
            original_verify(*args)
            (root / "input.json").write_bytes(b"not the verified input")
            (root / "spec.json").write_bytes(b"changed metadata")
        def intercept(command, env, cwd, data, boundary):
            delivered.append(data); raise OSError("offline intercept")
        with patch.object(self.worker, "verify_runtime", return_value=None), \
             patch.object(self.worker, "verify_release", side_effect=mutate), patch.object(ns, "supervise", side_effect=intercept):
            self.worker.run_prepared(root, ackpath, grantpath, "/unused/claude", TOKEN)
        self.assertEqual(delivered, [self.input])
        self.assertEqual((root / "capture" / "delivered-input.json").read_bytes(), self.input)
        self.assertEqual(json.loads((root / "run-started.json").read_bytes())["input_sha256"], sha(self.input))

    def test_result_seals_answer_but_natural_terminal_requires_owned_drain(self):
        root, spec = self.prepared(); b = self.worker.NaturalBoundary(root / "capture", spec, self.input)
        b.release(100); b.observe(self.init(spec), 110); b.observe(self.result(), 120)
        self.assertEqual((root / "capture" / "sealed-answer.txt").read_text(), "synthetic private final answer")
        self.assertIsNone(b.metadata()["native_terminal_monotonic_ns"])
        b.root_exit(0, 130); b.drain(150, census_empty=True, waitpid_echild=True)
        self.assertEqual(b.metadata()["runtime_seconds"], 50 / 1e9)
        self.assertNotIn("synthetic private final answer", json.dumps(b.metadata()))

    def test_startup_task_alias_is_allowed_but_other_capability_escalation_is_held(self):
        root, spec = self.prepared("browsecomp"); b = self.worker.NaturalBoundary(root / "capture", spec, self.input)
        b.release(100); b.observe(self.init(spec), 110)
        self.assertTrue(b.initialized)
        with self.assertRaises(ns.NativeSessionError):
            b.observe({"type": "assistant", "message": {"model": ns.MODEL, "content": [{"type": "tool_use", "name": "Bash"}]}}, 120)
        b.observe(self.result(), 125); b.root_exit(0, 130); b.drain(150, census_empty=True, waitpid_echild=True)
        self.assertFalse(b.metadata()["timing_valid"])

    def test_assistant_uses_native_submission_answer_and_rejects_extra_mcp_servers(self):
        root, spec = self.prepared("assistant"); b = self.worker.NaturalBoundary(root / "capture", spec, self.input)
        b.release(100); b.observe(self.init(spec), 110)
        state = root / "capture" / "browser-state"; state.mkdir()
        answer = b"native browser submission"; (state / "final-answer.txt").write_bytes(answer)
        (state / "submission.json").write_bytes(ns._json_bytes({"schema_version": 1, "attempt_id": ATTEMPT,
            "native_submission_monotonic_ns": 120, "answer_sha256": sha(answer), "answer_bytes": len(answer),
            "endpoint": "send_msg_to_user", "grade_status": "not_run"}))
        b.observe(self.result("CLI prose is not the browser answer"), 130)
        self.assertEqual((root / "capture" / "sealed-answer.txt").read_bytes(), answer)
        self.assertEqual(b.metadata()["submission_monotonic_ns"], 120)
        b.root_exit(0, 140); b.drain(150, census_empty=True, waitpid_echild=True)
        self.assertTrue(b.metadata()["timing_valid"])
        other = self.base / "other-capture"; other.mkdir()
        bad = self.worker.NaturalBoundary(other, spec, self.input); bad.release(100)
        with self.assertRaises(ns.NativeSessionError): bad.observe(self.init(spec, mcp_servers=[{"name": "other", "status": "connected"}]), 110)

    def test_assistant_accepts_exact_native_dynamic_source_only(self):
        root, spec = self.prepared('assistant')
        event = self.init(spec, mcp_servers=[{'name':'assistantbench','status':'connected','source':'dynamic'}])
        b = self.worker.NaturalBoundary(root/'capture', spec, self.input)
        b.release(100); b.observe(event, 110)
        self.assertTrue(b.initialized)
        for index, extra in enumerate(({'source':'local'}, {'source':'dynamic','unexpected':True})):
            capture = self.base / ('bad-source-'+str(index)); capture.mkdir()
            bad = self.worker.NaturalBoundary(capture, spec, self.input); bad.release(100)
            with self.assertRaises(ns.NativeSessionError):
                bad.observe(self.init(spec, mcp_servers=[{'name':'assistantbench','status':'connected',**extra}]),110)

    def test_assistant_result_without_native_submission_cannot_complete(self):
        root, spec = self.prepared("assistant"); b = self.worker.NaturalBoundary(root / "capture", spec, self.input)
        b.release(100); b.observe(self.init(spec), 110)
        with self.assertRaises(ns.NativeSessionError): b.observe(self.result(), 120)
        b.root_exit(0, 130); b.drain(150, census_empty=True, waitpid_echild=True)
        self.assertFalse(b.metadata()["timing_valid"])

    def test_original_native_session_and_all_state_are_archived_after_drain(self):
        root, spec = self.prepared(); report = self.run_fake(root, spec)
        self.assertEqual(report["state"], "captured")
        original = self.native_store(root)
        self.assertEqual((root / "archive" / original.relative_to(root)).read_bytes(), original.read_bytes())
        self.assertTrue(report["archive_verified"])
        self.assertFalse(report["independent_final_archive_acknowledged"])
        self.assertFalse(report["study_admitted"])
        self.assertIsNotNone(report["capture_finished_monotonic_ns"])

    def test_genuine_bridge_snapshot_is_bound_after_native_drain_and_archived(self):
        root, spec = self.prepared('assistant')
        report = self.run_fake(root, spec)
        self.assertEqual(report['state'], 'captured', report['issues'])
        self.assertTrue(report['browser_snapshot_verified'])
        evidence = report['browser_snapshot_evidence']
        self.assertEqual(evidence['attempt_id'], ATTEMPT)
        self.assertEqual(evidence['goal_sha256'], sha((root / 'capture/browser-goal.txt').read_bytes()))
        self.assertFalse(evidence['full_session_restore_qualified'])
        self.assertLessEqual(report['execution']['submission_monotonic_ns'], evidence['captured_at_monotonic_ns'])
        self.assertLessEqual(evidence['captured_at_monotonic_ns'], report['execution']['owned_work_drained_monotonic_ns'])
        for name in ('browser-state.json', 'browser-state-receipt.json', 'submission.json', 'final-answer.txt'):
            path = Path('capture/browser-state') / name
            self.assertEqual((root / path).read_bytes(), (root / 'archive' / path).read_bytes())
        self.assertNotIn('synthetic-private-state', (root / 'observation.json').read_text())

    def test_browser_snapshot_failures_hold_capture_and_preserve_submission_archive(self):
        cases = ('missing_snapshot', 'missing_receipt', 'tampered_snapshot', 'wrong_attempt', 'wrong_goal',
                 'wrong_bytes', 'before_submission', 'after_drain', 'invalid_time_type', 'invalid_snapshot')
        for mutation in cases:
            with self.subTest(mutation=mutation):
                previous = self.base; self.base = previous / mutation; self.base.mkdir()
                root, spec = self.prepared('assistant')
                def altered(*args):
                    result = self.simulate(*args); state = root / 'capture/browser-state'
                    snap_path = state / 'browser-state.json'; receipt_path = state / 'browser-state-receipt.json'
                    receipt = json.loads(receipt_path.read_bytes())
                    if mutation == 'missing_snapshot': snap_path.unlink()
                    elif mutation == 'missing_receipt': receipt_path.unlink()
                    elif mutation == 'tampered_snapshot': snap_path.write_bytes(snap_path.read_bytes() + b' ')
                    elif mutation == 'wrong_attempt': receipt['attempt_id'] = str(uuid4())
                    elif mutation == 'wrong_bytes': receipt['bytes'] += 1
                    elif mutation == 'before_submission': receipt['captured_at_monotonic_ns'] = result['submission_monotonic_ns'] - 1
                    elif mutation == 'after_drain': receipt['captured_at_monotonic_ns'] = result['owned_work_drained_monotonic_ns'] + 1
                    elif mutation == 'invalid_time_type': receipt['captured_at_monotonic_ns'] = True
                    elif mutation in ('wrong_goal', 'invalid_snapshot'):
                        snapshot = json.loads(snap_path.read_bytes())
                        if mutation == 'wrong_goal': snapshot['goal_sha256'] = '0' * 64
                        else: snapshot['pages'] = []
                        data = ns._json_bytes(snapshot); snap_path.write_bytes(data)
                        receipt.update(sha256=sha(data), bytes=len(data))
                    if mutation != 'missing_receipt': receipt_path.write_bytes(ns._json_bytes(receipt))
                    return result
                report = self.run_fake(root, spec, simulate=altered)
                self.assertEqual(report['state'], 'held')
                self.assertFalse(report['timing_admissible'])
                self.assertFalse(report['browser_snapshot_verified'])
                self.assertTrue(report['included_subscription_allowance_verified'])
                self.assertTrue(report['native_sessions_verified'])
                self.assertTrue(report['archive_verified'])
                self.assertTrue(any(x.startswith('native_browser_snapshot_') for x in report['issues']), report['issues'])
                self.assertEqual((root / 'capture/sealed-answer.txt').read_text(), 'synthetic private final answer')
                for name in ('submission.json', 'final-answer.txt'):
                    path = Path('capture/browser-state') / name
                    self.assertEqual((root / path).read_bytes(), (root / 'archive' / path).read_bytes())
                self.base = previous

    def test_missing_native_parent_store_holds_completion_but_preserves_archive(self):
        root, spec = self.prepared()
        def missing(*args):
            result = self.simulate(*args); self.native_store(root).unlink(); return result
        report = self.run_fake(root, spec, simulate=missing)
        self.assertEqual(report["state"], "held")
        self.assertFalse(report["timing_admissible"])
        self.assertFalse(report["native_sessions_verified"])
        self.assertTrue(report["archive_verified"])
        self.assertIn("native_parent_store_missing", report["issues"])

    def test_invalid_original_native_parent_identity_content_or_completion_is_held(self):
        for mutation in ("session", "input", "content", "request_id", "incomplete", "missing_final", "extra_store"):
            with self.subTest(mutation=mutation):
                previous = self.base; self.base = previous / mutation; self.base.mkdir()
                root, spec = self.prepared()
                def changed(*args):
                    result = self.simulate(*args); store = self.native_store(root)
                    rows = [json.loads(x) for x in store.read_bytes().splitlines()]
                    if mutation == "session": rows[-1]["sessionId"] = str(uuid4())
                    elif mutation == "input": rows[0]["message"]["content"][0]["text"] = "Changed initial task"
                    elif mutation == "content": rows[-1]["message"]["content"][0]["text"] = "Changed final answer"
                    elif mutation == "request_id": rows[-1]["requestId"] = "req_other"
                    elif mutation == "missing_final": rows = rows[:-1]
                    data = b"".join(ns._json_bytes(row) for row in rows)
                    store.write_bytes(data.rstrip(b"\n") if mutation == "incomplete" else data)
                    if mutation == "extra_store": store.with_name(str(uuid4()) + ".jsonl").write_bytes(data)
                    return result
                report = self.run_fake(root, spec, simulate=changed)
                self.assertEqual(report["state"], "held"); self.assertFalse(report["timing_admissible"])
                self.assertFalse(report["native_sessions_verified"])
                self.assertTrue(any(code.startswith("native_") for code in report["issues"]))
                self.base = previous

    def test_observed_child_requires_original_child_store(self):
        root, spec = self.prepared("browsecomp")
        def missing(*args):
            result = self.simulate(*args); child = self.add_child_evidence(root); child.unlink(); return result
        report = self.run_fake(root, spec, simulate=missing)
        self.assertEqual(report["state"], "held"); self.assertIn("native_child_store_missing", report["issues"])
        self.assertFalse(report["native_sessions_verified"])

    def test_verified_native_child_store_and_empty_optional_index_are_accepted(self):
        root, spec = self.prepared("browsecomp")
        def complete(*args):
            result = self.simulate(*args); self.add_child_evidence(root)
            raw = root / "capture/raw-bodies"; raw.mkdir(); (raw / "index.jsonl").write_bytes(b"")
            return result
        report = self.run_fake(root, spec, simulate=complete)
        self.assertEqual(report["state"], "captured"); self.assertTrue(report["native_sessions_verified"])
        self.assertEqual(len(report["native_session_evidence"]["children"]), 1)
        self.assertFalse(report["native_session_evidence"]["restoration_proven"])

    def test_completed_child_final_answer_must_match_original_child_store(self):
        root, spec = self.prepared("browsecomp")
        def altered(*args):
            result = self.simulate(*args); child = self.add_child_evidence(root)
            # The SDK stream need not repeat a child's final text. The native
            # Agent result still contains the immutable completed child output.
            stream_file = root / "capture/stream.jsonl"
            stream = [json.loads(x) for x in stream_file.read_bytes().splitlines()]
            stream = [x for x in stream if not (x.get("type") == "assistant" and x.get("parent_tool_use_id"))]
            stream_file.write_bytes(b"".join(ns._json_bytes(x) for x in stream))
            rows = [json.loads(x) for x in child.read_bytes().splitlines()]
            rows[-1]["message"]["content"][0]["text"] = "Changed child answer"
            child.write_bytes(b"".join(ns._json_bytes(x) for x in rows))
            return result
        report = self.run_fake(root, spec, simulate=altered)
        self.assertEqual(report["state"], "held"); self.assertIn("native_child_final_answer_mismatch", report["issues"])

    def test_observed_child_request_without_child_store_is_held(self):
        root, spec = self.prepared("browsecomp")
        def child_request(*args):
            result = self.simulate(*args); raw = root / "capture/raw-bodies"; raw.mkdir()
            (raw / "index.jsonl").write_bytes(ns._json_bytes({"query_source": "agent:builtin:general-purpose",
                "session_id": SESSION, "request_id": "req_child", "message_id": "msg_child"}))
            return result
        report = self.run_fake(root, spec, simulate=child_request)
        self.assertEqual(report["state"], "held"); self.assertIn("native_child_store_missing", report["issues"])

    def test_retained_real_native_store_formats_pass_without_modifying_evidence(self):
        repo = Path(__file__).resolve().parents[1]
        cases = [("synthetic-native-closed-book-ba4db711e948", "gpqa"),
            ("synthetic-native-image-ccfd7964f077", "hle"), ("synthetic-native-web-tools-9159fde3da47", "browsecomp"),
            ("synthetic-native-web-subagent-78cd93e4a144", "browsecomp"), ("synthetic-native-browser-4615dee9f7fa", "assistant")]
        for name, family in cases:
            with self.subTest(case=name):
                root = repo / "qualification/native-session-20261009" / name / "final-state"
                if not root.exists(): continue
                stream = [json.loads(x) for x in (root / "capture/stream.jsonl").read_bytes().splitlines()]
                init = next(x for x in stream if x.get("type") == "system" and x.get("subtype") == "init")
                spec = {"session_id": init["session_id"], "family_id": family,
                    "tool_policy": {"native_subagents": family == "browsecomp"}}
                files = [root / "capture/stream.jsonl", *list((root / "config/projects").rglob("*.jsonl"))]
                before = {str(path): sha(path.read_bytes()) for path in files}
                evidence = self.worker.validate_native_stores(root, spec, (root / "capture/delivered-input.json").read_bytes(),
                    runtime_cwd=init["cwd"])
                self.assertTrue(evidence["original_native_stores_verified"])
                self.assertEqual(before, {str(path): sha(path.read_bytes()) for path in files})

    def test_private_tokens_answers_and_reasoning_never_enter_heartbeat_projection(self):
        root, spec = self.prepared(); self.run_fake(root, spec)
        observation = (root / "observation.json").read_text()
        for secret in (TOKEN, "synthetic private final answer", "SYNTHETIC TASK MATERIAL", "thinking"):
            self.assertNotIn(secret, observation)
        safe = json.loads(observation)
        self.assertEqual(safe["attempt_id"], ATTEMPT); self.assertEqual(safe["state"], "captured")
        self.assertIn("source_observed_at", safe)

    def test_transport_or_missing_subscription_evidence_holds_timing_admission(self):
        root, spec = self.prepared(); ackpath, grantpath, _, _ = self.acknowledgements(root, spec)
        with patch.object(self.worker, "verify_runtime", return_value=None), patch.object(ns, "supervise", side_effect=self.simulate), \
             patch.object(ns, "inspect_transport_records", return_value={"issues": ["effort_mismatch"]}):
            report = self.worker.run_prepared(root, ackpath, grantpath, "/unused/claude", TOKEN)
        self.assertEqual(report["state"], "held"); self.assertFalse(report["timing_admissible"])
        observation = json.loads((root / "observation.json").read_bytes())
        self.assertIn("effort_mismatch", observation["issues"])
        self.assertFalse(observation["timing_admissible"])

    def test_absent_included_allowance_evidence_remains_held(self):
        root, spec = self.prepared()
        def without_receipt(*args):
            result = self.simulate(*args); args[-1].usage.clear(); return result
        report = self.run_fake(root, spec, simulate=without_receipt)
        self.assertEqual(report["state"], "held"); self.assertFalse(report["included_subscription_allowance_verified"])
        self.assertIn("subscription_allowance_unverified", report["issues"])

    def test_credentials_in_capture_hold_archive_and_preserve_original_state(self):
        root, spec = self.prepared()
        def leaked(*args):
            result = self.simulate(*args); (root / "capture" / "unexpected-file").write_text(TOKEN); return result
        report = self.run_fake(root, spec, simulate=leaked)
        self.assertEqual(report["state"], "held"); self.assertFalse(report["archive_verified"])
        self.assertTrue((root / "capture" / "unexpected-file").is_file())
        self.assertNotIn(TOKEN, (root / "report.json").read_text())
        self.assertNotIn(TOKEN, (root / "observation.json").read_text())

    def test_late_answer_mutation_and_credentials_in_capture_hold_archive(self):
        root, spec = self.prepared()
        def modified(*args):
            result = self.simulate(*args)
            (root / "capture" / "sealed-answer.txt").write_text("late mutation")
            return result
        report = self.run_fake(root, spec, simulate=modified)
        self.assertEqual(report["state"], "held"); self.assertIn("sealed_answer_changed", report["issues"])
        self.assertTrue((root / "config").is_dir())

    def test_entry_prepare_is_offline_and_run_reads_no_auth_argument(self):
        spec = self.spec(); specpath = self.base / "spec.json"; specpath.write_text(json.dumps(spec))
        inputpath = self.base / "input.json"; inputpath.write_bytes(self.input)
        env = {**os.environ, "PYTHONPATH": str(Path(ns.__file__).parents[1])}
        command = [sys.executable, "-m", "agenttime.natural_worker", "prepare", "--root", str(self.base / "entry"),
                   "--spec", str(specpath), "--input", str(inputpath)]
        prepared = subprocess.run(command, capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        self.assertEqual(json.loads(prepared.stdout)["state"], "prepared")
        self.assertFalse((self.base / "entry" / "run-started.json").exists())
        refused = subprocess.run(command, capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(refused.returncode, 1)


if __name__ == "__main__": unittest.main()
