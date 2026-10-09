"""Offline contract tests; native benchmark imports are optional and isolated."""

import copy
import json
import os
import shutil
import tempfile
from pathlib import Path
import threading
import sys
import time
import unittest
from unittest.mock import patch

from agenttime.automationbench.prompts import adapt_prompt, TURN_BUDGET
from agenttime.automationbench.backend import Backend, BackendError
from agenttime.automationbench.native import NativeBindings

SOURCE_ENV = os.environ.get("AT_AUTOMATIONBENCH_SOURCE") or os.environ.get(
    "AUTOMATIONBENCH_SOURCE"
)
SOURCE = Path(SOURCE_ENV) if SOURCE_ENV else None


class SourcePinTests(unittest.TestCase):
    def test_cold_and_warm_source_have_same_pin_and_corrupt_cache_fails(self):
        if SOURCE is None or not SOURCE.is_dir():
            self.skipTest("Optional pinned source fixture required")
        from agenttime.automationbench.native import source_digest
        from agenttime.automationbench._pin import PIN

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "source"
            root.mkdir()
            shutil.copytree(
                SOURCE / "automationbench",
                root / "automationbench",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            for name in ("pyproject.toml", "uv.lock"):
                shutil.copyfile(SOURCE / name, root / name)
            index = root / "automationbench/tools/api/schemas/index.txt"
            warm = index.read_bytes() if index.exists() else None
            if warm is None:
                schemas = {}
                for path in (index.parent).glob("*.jsonc"):
                    schema = json.loads(
                        "\n".join(
                            line
                            for line in path.read_text().splitlines()
                            if not line.lstrip().startswith("//")
                        )
                    )
                    schemas[schema["api"]] = schema
                lines = []
                for name, schema in sorted(schemas.items()):
                    for endpoint in schema.get("endpoints", []):
                        parts = [endpoint.get("description", "")]
                        for param in endpoint.get("parameters", {}).values():
                            if isinstance(param, dict) and param.get("description"):
                                parts.append(param["description"])
                        lines.append(
                            "\t".join(
                                [
                                    name,
                                    endpoint["id"],
                                    endpoint["method"],
                                    endpoint["path"],
                                    " ".join(filter(None, parts)),
                                ]
                            )
                        )
                warm = ("\n".join(lines) + "\n").encode()
            index.unlink(missing_ok=True)
            cold = source_digest(root)
            self.assertEqual(
                cold, "58b189a8c2a37c9ff085c2ec268cadca846665468e32cbca3e3c98bd1ff914f8"
            )
            index.write_bytes(warm)
            self.assertEqual(source_digest(root), cold)
            self.assertEqual(cold, PIN["runtime_tree_sha256"])
            index.write_bytes(warm + b"corrupted-cache\n")
            with self.assertRaises(ValueError):
                source_digest(root)


class AdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.version_info < (3, 13) or SOURCE is None or not SOURCE.is_dir():
            raise unittest.SkipTest(
                "Optional pinned Python3.13 benchmark runtime required"
            )
        cls.native = NativeBindings(SOURCE)
        from automationbench.domains.finance.tasks import (
            get_fin_invoice_email_extract_task,
        )

        cls.task = get_fin_invoice_email_extract_task()

    def backend(self, attempt="fixture-a", task=None):
        return Backend(self.native, copy.deepcopy(task or self.task), attempt)

    def test_prompt_changes_only_budget_clause(self):
        adapted = adapt_prompt(self.task["prompt"])
        self.assertNotIn(TURN_BUDGET, adapted["text"])
        self.assertEqual(adapted["messages"][1:], self.task["prompt"][1:])
        self.assertEqual(
            adapted["messages"][0]["content"],
            self.task["prompt"][0]["content"].replace(TURN_BUDGET, ""),
        )
        with self.assertRaises(ValueError):
            adapt_prompt(adapted["messages"])
        doubled = copy.deepcopy(self.task["prompt"])
        doubled[0]["content"] += TURN_BUDGET
        with self.assertRaises(ValueError):
            adapt_prompt(doubled)

    def test_exact_native_schema_and_results(self):
        b = self.backend()
        b.release()
        self.assertEqual(set(b.tools), {"api_search", "api_fetch", "base64_encode"})
        self.assertTrue(
            all("world" not in s["inputSchema"]["properties"] for s in b.tool_schemas())
        )
        for name, args in [
            ("base64_encode", {"text": "hello"}),
            ("api_search", {"query": "gmail list messages", "top_k": 1}),
            (
                "api_fetch",
                {
                    "method": "GET",
                    "url": "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                },
            ),
        ]:
            expected = self.native.invoke(
                name, args, self.native.restore_world(b.owner_state()["current"])
            )
            receipt = b.call("session-a", name, name, args)
            self.assertEqual(receipt["result"], expected)

    def test_duplicate_conflict_isolation_and_seal(self):
        b = self.backend()
        other = self.backend("fixture-b")
        b.release()
        other.release()
        initial = other.owner_state()["current"]
        args = {"text": "hello"}
        one = b.call("epoch-a", 1, "base64_encode", args)
        self.assertEqual(one, b.call("epoch-a", 1, "base64_encode", args))
        self.assertEqual(len(b.owner_state()["receipts"]), 1)
        with self.assertRaises(BackendError):
            b.call("epoch-a", 1, "base64_encode", {"text": "different"})
        b.call("epoch-b", 1, "base64_encode", args)
        self.assertEqual(len(b.owner_state()["receipts"]), 2)
        self.assertEqual(other.owner_state()["current"], initial)
        b.seal()
        self.assertEqual(one, b.call("epoch-a", 1, "base64_encode", args))
        with self.assertRaises(BackendError):
            b.call("epoch-c", 1, "base64_encode", args)

    def test_private_arguments_and_nested_injection_rejected(self):
        b = self.backend()
        b.release()
        for name, args in [
            ("grade", {}),
            ("api_fetch", {"method": "GET", "url": "x", "world": {}}),
            (
                "api_fetch",
                {
                    "method": "POST",
                    "url": "https://api.hubapi.com/crm/v3/objects/contacts",
                    "body": json.dumps({"properties": {"__dict__": {}}}),
                },
            ),
        ]:
            with self.assertRaises(BackendError):
                b.call("a", name, name, args)
        self.assertEqual(b.owner_state()["receipts"], [])

    def test_checkpoint_restore_and_tampering(self):
        b = self.backend()
        b.release()
        b.call("a", 1, "base64_encode", {"text": "yes"})
        payload = b.checkpoint()
        restored = Backend.restore(self.native, self.task, payload)
        self.assertEqual(restored.owner_state(), b.owner_state())
        altered = json.loads(payload)
        altered["payload"]["attempt_id"] = "changed"
        with self.assertRaises(BackendError):
            Backend.restore(self.native, self.task, json.dumps(altered).encode())
        wrong = copy.deepcopy(self.task)
        wrong["prompt"][1]["content"] += " wrong"
        with self.assertRaises(BackendError):
            Backend.restore(self.native, wrong, payload)
        b.seal()
        restored = Backend.restore(self.native, self.task, b.checkpoint())
        self.assertEqual(restored.grade(), b.grade())

    def test_partial_mutation_exception_is_fenced_and_receipted(self):
        b = self.backend()
        b.release()
        before = b.owner_state()["current"]

        def fail(name, args, world):
            world.meta.no_same_sender_noise = not world.meta.no_same_sender_noise
            raise RuntimeError("PRIVATE ASSERTION KEY /controller/private/file")

        with patch.object(self.native, "invoke", side_effect=fail) as call:
            receipt = b.call("a", 1, "base64_encode", {"text": "x"})
            self.assertTrue(receipt["is_error"])
            self.assertNotIn("PRIVATE", json.dumps(receipt))
            self.assertEqual(receipt, b.call("a", 1, "base64_encode", {"text": "x"}))
            self.assertEqual(call.call_count, 1)
        self.assertNotEqual(before, b.owner_state()["current"])
        with self.assertRaises(BackendError):
            b.call("a", 2, "base64_encode", {"text": "y"})
        with self.assertRaises(BackendError):
            b.checkpoint()
        b.seal()
        self.assertEqual(b.grade()["status"], "unavailable")
        with self.assertRaises(BackendError):
            Backend.restore(self.native, self.task, b.diagnostic_export())

    def test_seal_waits_for_accepted_call_and_rejects_late_calls(self):
        b = self.backend()
        b.release()
        entered = threading.Event()
        finish = threading.Event()
        sealed = threading.Event()
        original = self.native.invoke

        def slow(*args):
            entered.set()
            finish.wait(3)
            return original(*args)

        with patch.object(self.native, "invoke", side_effect=slow):
            t = threading.Thread(
                target=lambda: b.call("a", 1, "base64_encode", {"text": "x"})
            )
            t.start()
            self.assertTrue(entered.wait(2))
            s = threading.Thread(target=lambda: (b.seal(), sealed.set()))
            s.start()
            time.sleep(0.03)
            self.assertFalse(sealed.is_set())
            finish.set()
            t.join()
            s.join()
        self.assertTrue(sealed.is_set())
        self.assertEqual(len(b.owner_state()["receipts"]), 1)
        with self.assertRaises(BackendError):
            b.call("a", 2, "base64_encode", {"text": "late"})

    def test_native_grading_and_constructed_baseline(self):
        b = self.backend()
        b.release()
        b.seal()
        grade = b.grade()
        self.assertEqual(grade["status"], "available")
        self.assertEqual(grade["baseline_policy"], "constructed_initial_world_v1")
        self.assertEqual(grade["score"], 0)
        self.assertGreater(grade["assertions_total"], 0)
        self.assertTrue(
            all(
                r["exclusion_reason"] in (None, "authored", "initially_satisfied")
                for r in grade["assertions"]
            )
        )
        with patch.object(self.native, "grade", side_effect=ValueError("PRIVATE")):
            self.assertEqual(b.grade()["status"], "unavailable")
            self.assertNotIn("PRIVATE", json.dumps(b.grade()))
        self.assertIn(
            "current_time", b.owner_state()["constructed_initial"]["world"]["meta"]
        )

    def test_unknown_runtime_extras_are_rejected(self):
        b = self.backend()
        object.__setattr__(b._world.google_sheets, "_unknown_mutable", [])
        with self.assertRaises(BackendError):
            b.checkpoint()

    def synthetic_task(self):
        task = copy.deepcopy(self.task)
        task["info"] = {
            "initial_state": {
                "google_sheets": {
                    "spreadsheets": [{"id": "s", "title": "Sheet"}],
                    "worksheets": [
                        {
                            "id": "w",
                            "spreadsheet_id": "s",
                            "title": "Data",
                            "headers": ["value"],
                        }
                    ],
                    "rows": [
                        {
                            "spreadsheet_id": "s",
                            "worksheet_id": "w",
                            "row_id": 1,
                            "cells": {"value": "old"},
                        }
                    ],
                },
                "google_ads": {},
            },
            "zapier_tools": [],
            "assertions": [
                {
                    "type": "google_sheets_row_not_updated",
                    "spreadsheet_id": "s",
                    "worksheet_id": "w",
                    "row_id": 1,
                }
            ],
        }
        return task

    def test_native_sheet_mutation_marker_survives_grade_and_restore(self):
        task = self.synthetic_task()
        b = self.backend(task=task)
        b.release()
        args = {
            "method": "PUT",
            "url": "https://sheets.googleapis.com/v4/spreadsheets/s/values/w/rows/1",
            "body": json.dumps({"cells": {"value": "new"}}),
        }
        baseline = b.owner_state()["constructed_initial"]
        native_world = self.native.restore_world(baseline)
        expected = self.native.invoke("api_fetch", args, native_world)
        result = b.call("a", 1, "api_fetch", args)
        self.assertEqual(result["result"], expected)
        self.assertFalse(result["is_error"])
        self.assertEqual(b.owner_state()["current"], self.native.snapshot(native_world))
        self.assertEqual(
            b.owner_state()["current"]["runtime"]["google_sheets"],
            {"present": True, "value": ["s:w:1"]},
        )
        restored = Backend.restore(self.native, task, b.checkpoint())
        b.seal()
        restored.seal()
        grade = b.grade()
        self.assertEqual(grade, restored.grade())
        self.assertEqual(grade["assertions_total"], 1)
        self.assertEqual(grade["assertions_passed"], 0)
        self.assertFalse(grade["assertions"][0]["excluded"])
        from automationbench.rubric import partial_credit

        native_state = {
            "world": native_world,
            "initial_state": baseline["world"],
            "info": task["info"],
        }
        self.assertEqual(grade["score"], partial_credit(native_state))
        self.assertEqual(
            [
                {k: v for k, v in r.items() if k != "exclusion_reason"}
                for r in grade["assertions"]
            ],
            native_state["_assertion_results"],
        )
        self.assertEqual(b.call("a", 1, "api_fetch", args), result)

    def test_ads_job_remains_usable_after_restore(self):
        task = self.synthetic_task()
        b = self.backend(task=task)
        b.release()
        result = b.call(
            "a",
            1,
            "api_fetch",
            {
                "method": "POST",
                "url": "https://googleads.googleapis.com/v19/customers/123/offlineUserDataJobs:create",
                "body": "{}",
            },
        )
        self.assertFalse(result["is_error"])
        job = json.loads(result["result"])["resourceName"]
        job_id = job.split("/")[-1]
        restored = Backend.restore(self.native, task, b.checkpoint())
        operation = {
            "method": "POST",
            "url": f"https://googleads.googleapis.com/v19/{job}:run",
            "body": "{}",
        }
        left = b.call("a", 2, "api_fetch", operation)
        right = restored.call("a", 2, "api_fetch", operation)
        self.assertEqual(left, right)
        self.assertEqual(
            restored.owner_state()["current"]["runtime"]["google_ads"]["value"][job_id][
                "status"
            ],
            "RUNNING",
        )

    def test_constructed_baseline_avoids_default_factory_drift(self):
        task = self.synthetic_task()
        from automationbench.rubric.registry import AssertionRegistry

        task["info"]["assertions"] = [{"type": "fixture_initial_generated_id"}]
        b = self.backend(task=task)
        initial_id = b.owner_state()["constructed_initial"]["world"]["google_sheets"][
            "rows"
        ][0]["id"]
        with patch.dict(
            AssertionRegistry._handlers,
            {
                "fixture_initial_generated_id": lambda world,
                a: world.google_sheets.rows[0].id == initial_id
            },
        ):
            b.release()
            b.seal()
            grade = b.grade()
            self.assertEqual(grade["assertions_total"], 0)
            self.assertEqual(
                grade["assertions"][0]["exclusion_reason"], "initially_satisfied"
            )
            self.assertEqual(
                Backend.restore(self.native, task, b.checkpoint()).grade(), grade
            )

    def test_noop_negative_guards_follow_native_exclusion_policy(self):
        task = self.synthetic_task()
        task["info"]["assertions"] += [
            dict(task["info"]["assertions"][0], excluded=False),
            dict(task["info"]["assertions"][0], scored=False),
        ]
        b = self.backend(task=task)
        b.release()
        b.seal()
        grade = b.grade()
        self.assertEqual(grade["score"], 1.0)
        self.assertEqual(grade["assertions_total"], 1)
        self.assertEqual(
            [r["exclusion_reason"] for r in grade["assertions"]],
            ["initially_satisfied", None, "authored"],
        )

    def test_restore_rejects_pending_journal_even_with_valid_envelope_hash(self):
        from agenttime.automationbench.prompts import digest

        b = self.backend()
        b.release()
        envelope = json.loads(b.checkpoint())
        state = envelope["payload"]
        state["events"].append(
            {
                "sequence": len(state["events"]) + 1,
                "kind": "tool_started",
                "monotonic_ns": state["events"][-1]["monotonic_ns"] + 1,
                "recorded_at": state["events"][-1]["recorded_at"],
                "request_key": "unresolved",
                "clock_id": state["events"][-1]["clock_id"],
            }
        )
        envelope["sha256"] = digest(state)
        with self.assertRaises(BackendError):
            Backend.restore(self.native, self.task, json.dumps(envelope).encode())

    def test_private_injection_in_nested_json_string_is_rejected(self):
        b = self.backend()
        b.release()
        args = {
            "method": "POST",
            "url": "https://api.hubapi.com/crm/v3/objects/contacts",
            "body": json.dumps({"properties": json.dumps({"__dict__": {}})}),
        }
        with self.assertRaises(BackendError):
            b.call("s", 1, "api_fetch", args)

    def test_checkpoint_refuses_incomplete_seal_request(self):
        b = self.backend()
        b.release()
        b._closing.set()
        with self.assertRaises(BackendError):
            b.checkpoint()

    def test_checkpoint_and_diagnostic_classify_seal_during_capture(self):
        for operation in ("checkpoint", "diagnostic_export"):
            with self.subTest(operation=operation):
                b = self.backend()
                b.release()
                entered = threading.Event()
                result = {}
                original_capture = b.owner_state

                def delayed_capture():
                    entered.set()
                    if not b._closing.wait(3):
                        raise RuntimeError("Seal did not request closure")
                    return original_capture()

                def export():
                    try:
                        result["data"] = getattr(b, operation)()
                    except Exception as exc:
                        result["error"] = exc

                with patch.object(b, "owner_state", side_effect=delayed_capture):
                    capture_thread = threading.Thread(target=export)
                    capture_thread.start()
                    self.assertTrue(entered.wait(3))
                    seal_thread = threading.Thread(target=b.seal)
                    seal_thread.start()
                    capture_thread.join(5)
                    seal_thread.join(5)
                self.assertFalse(capture_thread.is_alive())
                self.assertFalse(seal_thread.is_alive())
                if operation == "checkpoint":
                    self.assertIsInstance(result.get("error"), BackendError)
                    self.assertNotIn("data", result)
                else:
                    self.assertNotIn("error", result)
                    envelope = json.loads(result["data"])
                    self.assertFalse(envelope["resumable"])
                    self.assertTrue(envelope["payload"]["closing"])
                    self.assertIsNone(envelope["payload"]["sealed"])
                restored = Backend.restore(self.native, self.task, b.checkpoint())
                self.assertEqual(restored.grade(), b.grade())

    def test_checkpoint_captured_before_seal_remains_resumable(self):
        b = self.backend()
        b.release()
        original_capture = b.owner_state

        def capture_then_close():
            captured = original_capture()
            b._closing.set()
            return captured

        with patch.object(b, "owner_state", side_effect=capture_then_close):
            checkpoint = b.checkpoint()
        self.assertFalse(json.loads(checkpoint)["payload"]["closing"])
        restored = Backend.restore(self.native, self.task, checkpoint)
        restored.call("new-session", 1, "base64_encode", {"text": "x"})

    def test_restore_rejects_state_receipt_chain_disagreement(self):
        from agenttime.automationbench.prompts import digest

        for calls in (0, 1, 2):
            with self.subTest(calls=calls):
                b = self.backend()
                b.release()
                for i in range(calls):
                    b.call("a", i, "base64_encode", {"text": "x"})
                envelope = json.loads(b.checkpoint())
                state = envelope["payload"]
                state["current"]["world"]["meta"]["no_same_sender_noise"] = not state[
                    "current"
                ]["world"]["meta"]["no_same_sender_noise"]
                envelope["sha256"] = digest(state)
                with self.assertRaises(BackendError):
                    Backend.restore(
                        self.native, self.task, json.dumps(envelope).encode()
                    )
        b = self.backend()
        b.release()
        b.call("a", 1, "base64_encode", {"text": "x"})
        b.call("a", 2, "base64_encode", {"text": "x"})
        envelope = json.loads(b.checkpoint())
        envelope["payload"]["receipts"][1]["before_sha256"] = "0" * 64
        envelope["sha256"] = digest(envelope["payload"])
        with self.assertRaises(BackendError):
            Backend.restore(self.native, self.task, json.dumps(envelope).encode())

    def test_restored_owner_uses_new_monotonic_clock_domain(self):
        b = self.backend()
        b.release()
        b.call("a", 1, "base64_encode", {"text": "x"})
        checkpoint = b.checkpoint()
        with patch(
            "agenttime.automationbench.backend.time.monotonic_ns", return_value=5
        ):
            restored = Backend.restore(self.native, self.task, checkpoint)
            restored.call("new-transport", 2, "base64_encode", {"text": "y"})
            restored.seal()
        events = restored.owner_state()["events"]
        self.assertEqual(len({e["clock_id"] for e in events}), 2)
        self.assertLess(events[-1]["monotonic_ns"], events[0]["monotonic_ns"])
        self.assertEqual(
            Backend.restore(self.native, self.task, restored.checkpoint()).grade(),
            restored.grade(),
        )

    def test_restore_rejects_unknown_or_pre_clock_owner_schema(self):
        from agenttime.automationbench.prompts import digest

        b = self.backend()
        b.release()
        for schema in (999, 1, 2.0, "2", True):
            with self.subTest(schema=schema):
                envelope = json.loads(b.checkpoint())
                envelope["payload"]["schema"] = schema
                envelope["sha256"] = digest(envelope["payload"])
                with self.assertRaises(BackendError):
                    Backend.restore(
                        self.native, self.task, json.dumps(envelope).encode()
                    )


if __name__ == "__main__":
    unittest.main()
