"""Offline boundaries for the qualification-only native Claude adapter."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

try:
    from agenttime import native_session as ns
except ImportError:
    ns = None


SESSION = "2535f4d2-daa6-47b7-ad93-32fbf7f07a04"
TOKEN = "synthetic-test-secret-never-a-real-credential"


class NativeSessionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(ns, "qualification-only native session adapter is not implemented")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.contract = ns.qualification_contract("synthetic-boundary-test", SESSION)

    def prepared(self, case="closed_book"):
        contract = ns.qualification_contract("synthetic-boundary-test", SESSION, case=case)
        root = self.base / (case + "-session")
        ns.prepare(root, contract)
        return root, contract

    def acknowledgement(self, root):
        baseline = json.loads((root / "baseline.json").read_text())
        return {
            "schema_version": "agenttime.native-baseline-ack.v1",
            "session_id": SESSION,
            "contract_sha256": baseline["contract_sha256"],
            "input_sha256": baseline["input_sha256"],
            "baseline_sha256": hashlib.sha256((root / "baseline.json").read_bytes()).hexdigest(),
            "inventory_sha256": baseline["inventory_sha256"],
            "archive_verified": True,
            "archive_location": "mac:private-disposable-test-archive",
        }

    def test_qualification_rejects_study_identity_or_payload(self):
        for field, value in (("purpose", "study"), ("qualification_id", "gpqa-01"),
                             ("input", {"type": "user", "message": {"role": "user", "content": "private study task"}})):
            bad = copy.deepcopy(self.contract)
            bad[field] = value
            with self.subTest(field=field), self.assertRaises(ns.NativeSessionError):
                ns.validate_contract(bad)

    def test_qualification_rejects_unpinned_model_effort_and_tools(self):
        for field, value in (("model", "another-model"), ("effort", "low"),
                             ("tools", ["Bash"]), ("cli_sha256", "0" * 64),
                             ("session_id", "not-a-uuid")):
            bad = copy.deepcopy(self.contract); bad[field] = value
            with self.subTest(field=field), self.assertRaises(ns.NativeSessionError):
                ns.validate_contract(bad)

    def test_image_payload_must_match_the_fixed_synthetic_image(self):
        value = ns.qualification_contract("synthetic-image-test", SESSION, case="image")
        ns.validate_contract(value)
        value["input"]["message"]["content"][1]["source"]["data"] = base64.b64encode(b"not an image").decode()
        with self.assertRaises(ns.NativeSessionError): ns.validate_contract(value)

    def test_source_pin_drift_rejects_a_contract(self):
        value = copy.deepcopy(self.contract); value["source_sha256"] = "0" * 64
        with self.assertRaises(ns.NativeSessionError): ns.validate_contract(value)

    def test_prepare_refuses_dirty_or_reused_state(self):
        root = self.base / "occupied"; root.mkdir(); (root / "memory.txt").write_text("old")
        with self.assertRaises(ns.NativeSessionError): ns.prepare(root, self.contract)
        self.assertEqual((root / "memory.txt").read_text(), "old")
        root, _ = self.prepared()
        with self.assertRaises(ns.NativeSessionError): ns.prepare(root, self.contract)

    def test_baseline_ack_binds_input_session_and_inventory(self):
        root, contract = self.prepared(); ack = self.acknowledgement(root)
        ns.verify_baseline(root, contract, ack)
        for key, value in (("session_id", "7fd87535-7905-44e5-9b53-12740782aa97"),
                           ("input_sha256", "0" * 64), ("inventory_sha256", "0" * 64),
                           ("baseline_sha256", "0" * 64), ("archive_verified", False)):
            bad = dict(ack); bad[key] = value
            with self.subTest(key=key), self.assertRaises(ns.NativeSessionError): ns.verify_baseline(root, contract, bad)
        (root / "work" / "injected.txt").write_text("unexpected material")
        with self.assertRaises(ns.NativeSessionError): ns.verify_baseline(root, contract, ack)

    def test_clean_configuration_never_inherits_api_key_or_personal_state(self):
        root, contract = self.prepared()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "host-api", "CLAUDE_CONFIG_DIR": "/personal", "ANTHROPIC_BASE_URL": "https://untrusted.example"}):
            conf = ns.configuration(root, "/runtime/claude", contract, TOKEN)
        env = conf["environment"]; cmd = conf["command"]
        self.assertNotIn("ANTHROPIC_API_KEY", env); self.assertNotIn("ANTHROPIC_BASE_URL", env)
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(root / "config"))
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], TOKEN)
        self.assertNotIn(TOKEN, json.dumps(cmd)); self.assertNotIn("/personal", json.dumps(conf))
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertNotIn("--max-turns", cmd); self.assertNotIn("--max-budget-usd", cmd)
        for p in root.rglob("*"):
            if p.is_file(): self.assertNotIn(TOKEN.encode(), p.read_bytes())

    def test_web_configuration_cannot_gain_arbitrary_shell_tools(self):
        root, contract = self.prepared("web_tools")
        conf = ns.configuration(root, "/runtime/claude", contract, TOKEN)
        cmd = conf["command"]
        self.assertEqual(set(cmd[cmd.index("--tools") + 1].split(",")), {"WebSearch", "WebFetch"})
        self.assertFalse(conf["readiness"]["study_ready"])
        self.assertFalse(conf["readiness"]["native_tools_qualified"])

    def test_offline_url_requires_declared_loopback_qualification(self):
        root, contract = self.prepared()
        for url in ("http://127.0.0.1:1234", "https://api.example.com", "http://127.0.0.1:1234/path"):
            with self.subTest(url=url), self.assertRaises(ns.NativeSessionError):
                ns.configuration(root, "/runtime/claude", contract, TOKEN, offline_base_url=url)
        offline = ns.qualification_contract("synthetic-offline", SESSION, offline=True)
        conf = ns.configuration(root, "/runtime/claude", offline, TOKEN, offline_base_url="http://127.0.0.1:1234")
        self.assertEqual(conf["environment"]["ANTHROPIC_BASE_URL"], "http://127.0.0.1:1234")
        for url in ("http://127.0.0.1.evil:1234", "http://localhost:1234", "http://127.0.0.1:0"):
            with self.assertRaises(ns.NativeSessionError): ns.configuration(root, "/runtime/claude", offline, TOKEN, offline_base_url=url)

    def test_binary_pin_is_checked_before_execution(self):
        binary = self.base / "claude"; binary.write_bytes(b"untrusted executable")
        with self.assertRaises(ns.NativeSessionError): ns.verify_binary(binary)

    def test_entry_prepares_without_model_calls_and_does_not_overwrite_state(self):
        source_root = Path(ns.__file__).resolve().parents[2]
        entry = source_root / "qualification" / "native_session" / "entry.py"
        contract = self.base / "synthetic.json"; contract.write_text(json.dumps(self.contract))
        root = self.base / "entry-session"
        env = {**os.environ, "PYTHONPATH": str(source_root / "src")}
        command = [sys.executable, str(entry), "prepare", "--root", str(root), "--contract", str(contract)]
        prepared = subprocess.run(command, capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        self.assertFalse(json.loads(prepared.stdout)["readiness"]["study_ready"])
        self.assertFalse((root / "run-started.json").exists())
        refused = subprocess.run(command, capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(refused.returncode, 1)
        self.assertEqual(json.loads(refused.stdout)["error_code"], "state_already_exists")

    def test_existing_onset_intent_cannot_be_replayed_even_with_a_clean_baseline(self):
        root, contract = self.prepared(); ack_path = root / "acknowledgement.json"
        ack_path.write_text(json.dumps(self.acknowledgement(root)))
        intent = root / "run-started.json"; intent.write_text('{"original":"never replace"}')
        # Pin verification is a separate boundary; bypass only its binary I/O so
        # this offline test reaches the durable, pre-execution replay refusal.
        with patch.object(ns, "verify_binary", return_value=None), self.assertRaisesRegex(ns.NativeSessionError, "qualification_already_claimed"):
            ns.run_prepared(root, ack_path, "/not-an-executable", TOKEN)
        self.assertEqual(intent.read_text(), '{"original":"never replace"}')
        self.assertFalse((root / "capture" / "stream.jsonl").exists())

    def test_delivery_uses_verified_bytes_when_input_changes_after_baseline_check(self):
        root, contract = self.prepared(); ack_path = root / "acknowledgement.json"
        ack_path.write_text(json.dumps(self.acknowledgement(root)))
        verified_input = (root / "input.json").read_bytes()
        verified_contract_hash = hashlib.sha256((root / "contract.json").read_bytes()).hexdigest()
        original_verify = ns.verify_baseline
        observed = []

        def mutate_after_verification(state, expected, acknowledgement):
            original_verify(state, expected, acknowledgement)
            (root / "input.json").write_text("PRIVATE_STUDY_INJECTION")
            (root / "contract.json").write_text('{"attacker":"changed metadata"}')

        def intercept_delivery(command, environment, cwd, input_bytes, boundary):
            observed.append(input_bytes)
            raise ns.NativeSessionError("synthetic_intercept_before_process_launch")

        with patch.object(ns, "verify_binary", return_value=None), \
                patch.object(ns, "verify_baseline", side_effect=mutate_after_verification), \
                patch.object(ns, "supervise", side_effect=intercept_delivery):
            ns.run_prepared(root, ack_path, "/not-an-executable", TOKEN)
        self.assertEqual(observed, [verified_input])
        self.assertNotIn(b"PRIVATE_STUDY_INJECTION", observed[0])
        intent = json.loads((root / "run-started.json").read_text())
        self.assertEqual(intent["input_sha256"], hashlib.sha256(observed[0]).hexdigest())
        self.assertEqual(intent["contract_sha256"], verified_contract_hash)
        self.assertEqual((root / "capture" / "delivered-input.json").read_bytes(), observed[0])

    def init_event(self, **changes):
        value = {"type": "system", "subtype": "init", "session_id": SESSION,
                 "model": "claude-opus-5-5[1m]", "claude_code_version": "2.1.280",
                 "tools": [], "mcp_servers": [], "skills": [], "plugins": []}
        value.update(changes); return value

    def result_event(self, **changes):
        value = {"type": "result", "subtype": "success", "is_error": False,
                 "session_id": SESSION, "result": "SYNTHETIC_NATIVE_OK"}
        value.update(changes); return value

    def test_pinned_cli_reports_task_but_model_calls_agent(self):
        root, contract = self.prepared("web_subagent")
        self.assertIn("Agent", contract["tools"])
        boundary = ns.NativeBoundary(root / "capture", contract)
        boundary.release(100)
        boundary.observe(self.init_event(tools=["Task", "WebSearch", "WebFetch"]), 110)
        boundary.observe({"type":"assistant", "session_id":SESSION, "message":{"model":"claude-opus-5-5", "content":[{"type":"tool_use", "name":"Agent"}]}}, 120)
        self.assertEqual(boundary.issues, [])

    def test_result_sealed_once_and_timing_waits_for_owned_drain(self):
        root, contract = self.prepared(); boundary = ns.NativeBoundary(root / "capture", contract)
        boundary.release(100)
        boundary.observe(self.init_event(), 110)
        boundary.observe(self.result_event(), 150)
        self.assertEqual((root / "capture" / "sealed-answer.txt").read_text(), "SYNTHETIC_NATIVE_OK")
        self.assertIsNone(boundary.metadata()["native_terminal_monotonic_ns"])
        boundary.root_exit(0, 170)
        boundary.drain(210, census_empty=True, waitpid_echild=True)
        summary = boundary.metadata()
        self.assertEqual(summary["result_monotonic_ns"], 150)
        self.assertEqual(summary["native_terminal_monotonic_ns"], 210)
        self.assertEqual(summary["runtime_seconds"], 110 / 1e9)
        self.assertNotIn("SYNTHETIC_NATIVE_OK", json.dumps(summary))
        with self.assertRaises(ns.NativeSessionError): boundary.observe(self.result_event(result="replacement"), 230)
        self.assertEqual((root / "capture" / "sealed-answer.txt").read_text(), "SYNTHETIC_NATIVE_OK")

    def test_wrong_identity_or_capabilities_prevents_terminal(self):
        for change in ({"model": "another"}, {"tools": ["Bash"]}, {"plugins": ["extra"]}, {"session_id": "wrong"}):
            with self.subTest(change=change):
                root = self.base / ("bad-" + str(len(list(self.base.iterdir())))); ns.prepare(root, self.contract)
                boundary = ns.NativeBoundary(root / "capture", self.contract); boundary.release(100)
                with self.assertRaises(ns.NativeSessionError): boundary.observe(self.init_event(**change), 110)
                boundary.root_exit(0, 140); boundary.drain(150, census_empty=True, waitpid_echild=True)
                self.assertFalse(boundary.metadata()["timing_valid"])

    def test_error_result_or_unknown_descendants_is_not_natural_completion(self):
        root, contract = self.prepared(); boundary = ns.NativeBoundary(root / "capture", contract)
        boundary.release(100); boundary.observe(self.init_event(), 110)
        with self.assertRaises(ns.NativeSessionError): boundary.observe(self.result_event(is_error=True), 120)
        boundary.root_exit(1, 140); boundary.drain(150, census_empty=False, waitpid_echild=False)
        self.assertFalse(boundary.metadata()["timing_valid"])
        self.assertIsNone(boundary.metadata()["native_terminal_monotonic_ns"])

    def test_paid_overage_or_rejected_usage_is_visible_without_fallback(self):
        root, contract = self.prepared(); boundary = ns.NativeBoundary(root / "capture", contract)
        boundary.release(100)
        with self.assertRaises(ns.NativeSessionError):
            boundary.observe({"type": "rate_limit_event", "rate_limit_info": {"isUsingOverage": True}}, 110)
        self.assertIn("unexpected_paid_overage", boundary.metadata()["issues"])

    def test_missing_or_ambiguous_paid_usage_evidence_does_not_verify_subscription(self):
        for rows in ([], [{"status": "allowed"}], [{"status": "allowed", "isUsingOverage": False}],
                     [{"status": "allowed", "isUsingOverage": False, "overageStatus": "allowed"}],
                     [{"status": "allowed", "isUsingOverage": False, "overageStatus": "rejected", "hasExtraUsage": True}]):
            with self.subTest(rows=rows): self.assertFalse(ns.subscription_allowance_verified(rows))
        self.assertTrue(ns.subscription_allowance_verified([
            {"status": "allowed", "isUsingOverage": False, "overageStatus": "rejected"}]))

    def test_native_capability_sets_do_not_depend_on_tool_order(self):
        root, contract = self.prepared("web_tools")
        boundary = ns.NativeBoundary(root / "capture", contract); boundary.release(100)
        boundary.observe(self.init_event(tools=["WebFetch", "WebSearch"]), 120)
        boundary.observe(self.result_event(), 150); boundary.root_exit(0, 160)
        boundary.drain(170, census_empty=True, waitpid_echild=True)
        self.assertTrue(boundary.metadata()["timing_valid"])

    def test_rewound_clock_never_produces_valid_runtime(self):
        root, contract = self.prepared(); boundary = ns.NativeBoundary(root / "capture", contract)
        boundary.release(100)
        with self.assertRaises(ns.NativeSessionError): boundary.observe(self.init_event(), 90)
        self.assertFalse(boundary.metadata()["timing_valid"])

    def test_metadata_does_not_expose_thinking_or_tool_contents(self):
        event = {"type": "assistant", "session_id": SESSION, "message": {"model": "claude-opus-5-5", "content": [
            {"type": "thinking", "thinking": "PRIVATE_THOUGHT"},
            {"type": "tool_use", "name": "WebFetch", "input": {"url": "PRIVATE_URL"}}]}}
        out = ns.event_metadata(event)
        self.assertEqual(out["tool_names"], ["WebFetch"])
        self.assertNotIn("PRIVATE", json.dumps(out))

    def test_archive_keeps_native_store_and_workspace_and_rejects_credentials(self):
        root, _ = self.prepared()
        p = root / "config" / "projects" / "task"; p.mkdir(parents=True)
        (p / (SESSION + ".jsonl")).write_text('{"type":"user","sessionId":"' + SESSION + '"}\n')
        (root / "work" / "answer.txt").write_text("local result")
        archive = ns.capture_archive(root, TOKEN)
        self.assertTrue((root / "archive" / "config" / "projects" / "task" / (SESSION + ".jsonl")).is_file())
        self.assertEqual((root / "archive" / "work" / "answer.txt").read_text(), "local result")
        self.assertFalse(archive["native_restore_qualified"])
        other = self.base / "secret-session"; ns.prepare(other, self.contract)
        (other / "work" / "accidental.txt").write_text(TOKEN)
        with self.assertRaises(ns.NativeSessionError): ns.capture_archive(other, TOKEN)
        self.assertFalse((other / "archive-receipt.json").exists())

    def test_archive_rejects_external_links_and_auth_store(self):
        root, _ = self.prepared(); (root / "work" / "escape").symlink_to(self.base / "private-outside")
        with self.assertRaises(ns.NativeSessionError): ns.capture_archive(root, TOKEN)
        (root / "work" / "escape").unlink()
        (root / "config" / ".credentials.json").write_text('{"accessToken":"some-private-auth"}')
        with self.assertRaises(ns.NativeSessionError): ns.capture_archive(root, TOKEN)

    def test_archive_preserves_directory_modes_under_private_umask(self):
        root, _ = self.prepared()
        outer = root / "work" / "browsergym"; inner = outer / "nested"
        inner.mkdir(parents=True); outer.chmod(0o755); inner.chmod(0o711)
        (inner / "state.json").write_text("synthetic browser state")
        before = ns.inventory(root, secret=TOKEN)
        previous = os.umask(0o077)
        try: receipt = ns.capture_archive(root, TOKEN)
        finally: os.umask(previous)
        self.assertTrue(receipt["verified"])
        self.assertEqual(ns.inventory(root / "archive", secret=TOKEN), before)
        self.assertEqual(ns.inventory(root, secret=TOKEN), before)
        self.assertEqual((root / "archive").stat().st_mode & 0o777, 0o700)

    def test_archive_copies_nested_readonly_directories_before_restoring_modes(self):
        root, _ = self.prepared()
        outer = root / "work" / "readonly"; inner = outer / "nested"
        inner.mkdir(parents=True)
        payload = inner / "state.json"; payload.write_text("synthetic readonly state"); payload.chmod(0o444)
        inner.chmod(0o555); outer.chmod(0o555); (root / "work").chmod(0o555)
        (root / "capture" / "latest").symlink_to(payload.resolve())
        before = ns.inventory(root, secret=TOKEN)
        previous = os.umask(0o077)
        try:
            receipt = ns.capture_archive(root, TOKEN)
            self.assertTrue(receipt["verified"])
            self.assertEqual(ns.inventory(root / "archive", secret=TOKEN), before)
            self.assertEqual(ns.inventory(root, secret=TOKEN), before)
            self.assertEqual((root / "archive" / "capture" / "latest").resolve(),
                             root.resolve() / "archive" / "work" / "readonly" / "nested" / "state.json")
        finally:
            os.umask(previous)
            for base in (root, root / "archive"):
                for relative in ("work", "work/readonly", "work/readonly/nested"):
                    path = base / relative
                    if path.is_dir(): path.chmod(0o700)

    def test_archive_directory_mode_repair_does_not_hide_source_changes(self):
        root, _ = self.prepared(); folder = root / "work" / "browsergym"
        folder.mkdir(); folder.chmod(0o755); source = folder / "state.json"; source.write_text("before")
        copy_file = ns.shutil.copy2
        def mutate_after_copy(original, target, *args, **kwargs):
            result = copy_file(original, target, *args, **kwargs)
            if Path(original) == source: source.write_text("changed during capture")
            return result
        previous = os.umask(0o077)
        try:
            with patch.object(ns.shutil, "copy2", side_effect=mutate_after_copy):
                with self.assertRaisesRegex(ns.NativeSessionError, "archive_changed_during_capture"):
                    ns.capture_archive(root, TOKEN)
        finally: os.umask(previous)
        self.assertFalse((root / "archive-receipt.json").exists())

    def transport_fixture(self, root, contract):
        raw = root / "capture" / "raw-bodies"; raw.mkdir()
        body = {"model": "claude-opus-5-5", "output_config": {"effort": "max"},
                "tools": [{"name": name} for name in contract["tools"]], "messages": [copy.deepcopy(contract["input"]["message"])]}
        record = {"request_file": "first.request.json", "request_id": "req_synthetic", "session_id": SESSION,
                  "model": "claude-opus-5-5", "response_file": "first.response.json", "message_id": "msg_synthetic"}
        response = {"type": "message", "id": "msg_synthetic", "model": "claude-opus-5-5", "stop_reason": "end_turn", "content": [{"type": "text", "text": "SYNTHETIC_NATIVE_OK"}]}
        (raw / "first.request.json").write_text(json.dumps(body))
        (raw / "first.response.json").write_text(json.dumps(response))
        (raw / "index.jsonl").write_text(json.dumps(record) + "\n")
        return raw, body, record, response

    def test_transport_rejects_hidden_fallback_wrong_effort_and_child_tool_escalation(self):
        root, contract = self.prepared(); raw, body, _, _ = self.transport_fixture(root, contract)
        self.assertEqual(ns.inspect_transport(raw, contract)["issues"], [])
        for changes, code in (({"fallbacks": ["other-model"]}, "fallback_configured"),
                              ({"output_config": {"effort": "low"}}, "effort_mismatch"),
                              ({"tools": [{"name": "Bash"}]}, "request_capability_mismatch")):
            bad = {**body, **changes}; (raw / "first.request.json").write_text(json.dumps(bad))
            self.assertIn(code, ns.inspect_transport(raw, contract)["issues"])

    def test_transport_missing_response_or_other_session_is_unqualified(self):
        root, contract = self.prepared(); raw, body, record, response = self.transport_fixture(root, contract)
        response["id"] = "different-message"; (raw / "first.response.json").write_text(json.dumps(response))
        self.assertIn("transport_response_mismatch", ns.inspect_transport(raw, contract)["issues"])
        (raw / "first.response.json").unlink()
        self.assertIn("transport_response_unavailable", ns.inspect_transport(raw, contract)["issues"])

    def test_final_optional_response_log_requires_matching_complete_native_transcript(self):
        root, contract = self.prepared(); raw, _, record, response = self.transport_fixture(root, contract)
        (raw / "first.response.json").write_bytes(b"")
        native = {"type":"assistant", "session_id":SESSION, "message":response}
        ending = self.result_event()
        stream = root / "capture" / "stream.jsonl"
        stream.write_text(json.dumps(native)+"\n"+json.dumps(ending)+"\n")
        proof = ns.inspect_transport(raw, contract)
        self.assertEqual(proof["issues"], [])
        self.assertEqual(proof["native_stream_response_ids"], [response["id"]])
        response["id"] = "not-the-transport-message"
        stream.write_text(json.dumps(native)+"\n"+json.dumps(ending)+"\n")
        self.assertIn("transport_response_unavailable", ns.inspect_transport(raw, contract)["issues"])
        response["id"] = record["message_id"]
        stream.write_text(json.dumps(native)+"\n")
        self.assertIn("transport_response_unavailable", ns.inspect_transport(raw, contract)["issues"])

    def test_transport_cannot_omit_the_promised_web_tools(self):
        root, contract = self.prepared("web_tools"); raw, body, _, _ = self.transport_fixture(root, contract)
        body["tools"] = []; (raw / "first.request.json").write_text(json.dumps(body))
        self.assertIn("initial_request_capability_mismatch", ns.inspect_transport(raw, contract)["issues"])


    def test_native_image_annotation_must_bind_unchanged_image_and_session_path(self):
        root, contract = self.prepared("image")
        raw, body, _, _ = self.transport_fixture(root, contract)
        slug = str(root / "work").replace("/", "-")
        annotation = {"type": "text", "text": f"[Image: source: {root}/tmp/claude-10001/{slug}/{SESSION}/images/1.png]"}
        body["messages"][0]["content"].append(annotation)
        (raw / "first.request.json").write_text(json.dumps(body))
        self.assertEqual(ns.inspect_transport(raw, contract)["issues"], [])
        for changed in ("injected instruction", annotation["text"].replace(SESSION, "another-session")):
            body["messages"][0]["content"][-1] = {"type": "text", "text": changed}
            (raw / "first.request.json").write_text(json.dumps(body))
            self.assertIn("delivered_input_mismatch", ns.inspect_transport(raw, contract)["issues"])
        body["messages"][0]["content"][-1] = annotation
        body["messages"][0]["content"][1]["source"]["data"] = "changed-image"
        (raw / "first.request.json").write_text(json.dumps(body))
        self.assertIn("delivered_input_mismatch", ns.inspect_transport(raw, contract)["issues"])

    def test_native_web_search_internal_call_is_bounded_by_parent_tool_policy(self):
        root, contract = self.prepared("web_tools")
        raw, body, record, response = self.transport_fixture(root, contract)
        internal = dict(body, tools=[{"name":"web_search", "type":"web_search_20250305", "max_uses":8}])
        second = dict(record, request_file="search.request.json", response_file="search.response.json", query_source="web_search_tool")
        (raw / "search.request.json").write_text(json.dumps(internal))
        (raw / "search.response.json").write_text(json.dumps(response))
        (raw / "index.jsonl").write_text(json.dumps(record)+"\n"+json.dumps(second)+"\n")
        self.assertEqual(ns.inspect_transport(raw, contract)["issues"], [])
        internal["tools"].append({"name":"Bash"})
        (raw / "search.request.json").write_text(json.dumps(internal))
        self.assertIn("request_capability_mismatch", ns.inspect_transport(raw, contract)["issues"])
        internal["tools"] = [{"name":"web_search", "type":"web_search_20250305", "max_uses":8}]
        (raw / "search.request.json").write_text(json.dumps(internal))
        second["query_source"] = "sdk"
        (raw / "index.jsonl").write_text(json.dumps(record)+"\n"+json.dumps(second)+"\n")
        self.assertIn("request_capability_mismatch", ns.inspect_transport(raw, contract)["issues"])

@unittest.skipUnless(sys.platform == "linux", "Linux subreaper qualification runs inside the owned Docker canary")
class NativeLinuxDrainTests(unittest.TestCase):
    def test_root_exit_does_not_drop_adopted_background_writer(self):
        self.assertIsNotNone(ns)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "attempt"
            contract = ns.qualification_contract("synthetic-drain", SESSION)
            ns.prepare(root, contract)
            child = Path(temp) / "fake_native.py"
            child.write_text('''import json,os,sys,time
sys.stdin.read()
print(json.dumps({"type":"system","subtype":"init","session_id":"''' + SESSION + '''","model":"claude-opus-5-5[1m]","claude_code_version":"2.1.280","tools":[],"mcp_servers":[],"skills":[],"plugins":[]}),flush=True)
print(json.dumps({"type":"result","subtype":"success","is_error":False,"session_id":"''' + SESSION + '''","result":"SYNTHETIC_NATIVE_OK"}),flush=True)
if os.fork()==0:
 time.sleep(.2)
 open("late.txt","w").write("late background write")
 os._exit(0)
''')
            # The wrapper is a separate Linux process so its subreaper owns only this test.
            wrapper = Path(temp) / "wrapper.py"
            wrapper.write_text('''import json,sys
from pathlib import Path
from agenttime.native_session import NativeBoundary,supervise
root=Path(sys.argv[1]);contract=json.loads((root/"contract.json").read_text())
boundary=NativeBoundary(root/"capture",contract)
summary=supervise([sys.executable,sys.argv[2]],{},root/"work",(root/"input.json").read_bytes(),boundary)
print(json.dumps(summary))
''')
            result = subprocess.run([sys.executable, str(wrapper), str(root), str(child)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertTrue(summary["timing_valid"])
            self.assertGreater(summary["owned_work_drained_monotonic_ns"], summary["root_exit_monotonic_ns"])
            self.assertGreater(summary["runtime_seconds"], .15)
            self.assertTrue((root / "work" / "late.txt").is_file())
            self.assertEqual((root / "capture" / "sealed-answer.txt").read_text(), "SYNTHETIC_NATIVE_OK")


if __name__ == "__main__": unittest.main()
