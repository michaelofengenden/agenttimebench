"""Real PostgreSQL admission races, with no native or provider execution."""
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import importlib
import json
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from db_support import DatabaseTestCase


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def manifest(count=3, capacity=2, agent="opus-subscription", cohort="cohort-a"):
    return {"schema_version": 1, "executor": "claude-natural-v1", "cohort_id": cohort,
            "agent": {"id": agent, "model": "claude-opus-5-5[1m]", "effort": "max", "route": "subscription"},
            "arm": "natural", "capacity": capacity, "automatic_replacement": False,
            "runtime_pins": {"cli_sha256": "1" * 64, "worker_sha256": "2" * 64, "controller_sha256": "3" * 64},
            "qualification_proof_sha256": "4" * 64,
            "tasks": [{"id": f"task-{i}", "contract_sha256": digest({"contract": i}), "input_sha256": digest({"input": i}),
                       "runtime_pins": {"image_sha256": "5" * 64}} for i in range(count)]}


def identity(worker="worker-a"):
    return {"worker_id": worker, "session_id": str(uuid4()), "host_id": "test-host",
            "daemon_id": "test-daemon", "docker_endpoint": "unix:///var/run/docker.sock",
            "container_id": hashlib.sha256(worker.encode()).hexdigest()}


class NaturalLedgerTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        with psycopg.connect(self.dsn) as conn:
            conn.execute("DROP SCHEMA IF EXISTS agenttime_natural CASCADE")
        try: self.module = importlib.import_module("agenttime.natural_ledger")
        except ModuleNotFoundError: self.fail("Natural PostgreSQL admission is not implemented")
        self.Ledger = self.module.NaturalLedger
        self.ledger = self.Ledger(self.dsn)
        self.ledger.initialize(50)
        self.manifest = manifest(count=8)
        self.ledger.create_campaign("a", self.manifest)

    def claimed(self, worker="worker-a"):
        row = self.ledger.reserve("a"); owner = identity(worker)
        self.assertTrue(self.ledger.claim(row["id"], owner))
        return row, owner

    def baseline(self, row, owner):
        return {"schema_version": "agenttime.natural-baseline-ack.v1", "attempt_id": row["id"],
                "identity_sha256": digest(owner), "session_id": owner["session_id"],
                "contract_sha256": row["contract_sha256"], "input_sha256": row["input_sha256"],
                "inventory_sha256": "6" * 64, "archive_manifest_sha256": "7" * 64,
                "archive_verified": True, "independent_archive": True, "archive_location": "mac:private-test-archive"}

    def release_event(self, row, owner):
        return {"kind": "prompt_released", "attempt_id": row["id"], "identity_sha256": digest(owner),
                "input_sha256": row["input_sha256"], "clock_id": "test-boot-monotonic", "monotonic_ns": 123456,
                "event_sha256": "8" * 64}

    def released(self, worker="worker-a"):
        row, owner = self.claimed(worker)
        self.ledger.acknowledge_baseline(row["id"], owner, self.baseline(row, owner))
        self.assertTrue(self.ledger.authorize_release(row["id"], owner))
        self.assertTrue(self.ledger.mark_prompt_released(row["id"], owner, self.release_event(row, owner)))
        return row, owner

    def report(self, row, owner, outcome="completed"):
        value = {"attempt_id": row["id"], "identity_sha256": digest(owner),
                 "contract_sha256": row["contract_sha256"], "input_sha256": row["input_sha256"],
                 "outcome": outcome, "timing_status": "valid" if outcome == "completed" else "invalid",
                 "stop_evidence": {"identity_sha256": digest(owner), "stop_verified": True,
                                   "owned_work_drained": True, "proof_sha256": "9" * 64}}
        if outcome == "infrastructure_failure":
            value.update(failure_class="external_infrastructure", failure_evidence_sha256="a" * 64,
                         continuation_status="impossible", continuation_evidence_sha256="b" * 64)
        return value

    def authorization(self, row):
        return {"kind": "explicit_operator_request", "request_id": "approved-test-request", "original_attempt_id": row["id"],
                "failure_evidence_sha256": "a" * 64, "continuation_evidence_sha256": "b" * 64}

    def test_fifty_concurrent_reservations_are_unique_and_obey_global_capacity(self):
        self.ledger.create_campaign("fifty", manifest(count=80, capacity=50, cohort="cohort-fifty"))
        with ThreadPoolExecutor(max_workers=50) as pool:
            rows = list(pool.map(lambda _: self.Ledger(self.dsn).reserve("fifty"), range(80)))
        accepted = [row for row in rows if row]
        self.assertEqual(len(accepted), 50)
        self.assertEqual(len({row["id"] for row in accepted}), 50)
        self.assertEqual(len({row["task_id"] for row in accepted}), 50)
        self.assertEqual(self.ledger.admission_block_reason("fifty"), "capacity")
        self.assertEqual(sum(row["holds_capacity"] for row in self.ledger.attempts("fifty")), 50)

    def test_claim_is_permanent_and_owner_tuple_cannot_change(self):
        row = self.ledger.reserve("a"); owner = identity()
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(lambda _: self.Ledger(self.dsn).claim(row["id"], owner), range(8)))
        self.assertEqual(claims.count(True), 1)
        self.assertFalse(self.Ledger(self.dsn).claim(row["id"], identity("new-worker")))
        self.assertEqual(self.ledger.attempt(row["id"])["identity"], owner)

    def test_native_session_and_container_cannot_be_reused_after_completion(self):
        row, owner = self.released()
        self.ledger.finish(row["id"], owner, self.report(row, owner))
        pending = self.ledger.reserve("a")
        for bad in ({**identity("second"), "session_id": owner["session_id"]},
                    {**identity("second"), "container_id": owner["container_id"]}):
            with self.subTest(identity=bad), self.assertRaises(ValueError):
                self.ledger.claim(pending["id"], bad)
        self.assertTrue(self.ledger.claim(pending["id"], identity("fresh")))

    def test_claim_detects_corruption_in_other_unresolved_attempt(self):
        row, owner = self.claimed()
        pending = self.ledger.reserve("a")
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE agenttime_natural.attempts SET identity=%s WHERE id=%s",
                         (Jsonb({**owner, "worker_id": "changed"}), row["id"]))
        with self.assertRaises(ValueError): self.ledger.claim(pending["id"], identity("second"))
        self.assertIsNotNone(self.ledger.gate()["paused_reason"])

    def test_quarantine_keeps_capacity_on_restart_without_lease_expiry(self):
        first, owner = self.claimed()
        self.ledger.quarantine(first["id"], "lost worker")
        self.assertIsNotNone(self.Ledger(self.dsn).reserve("a"))
        self.assertIsNone(self.Ledger(self.dsn).reserve("a"))
        self.assertTrue(self.ledger.attempt(first["id"])["holds_capacity"])
        self.assertFalse(self.ledger.claim(first["id"], owner))
        self.assertFalse(self.ledger.authorize_release(first["id"], owner))

    def test_release_requires_matching_verified_independent_baseline(self):
        row, owner = self.claimed()
        self.assertFalse(self.ledger.authorize_release(row["id"], owner))
        ack = self.baseline(row, owner)
        for key, bad in (("session_id", str(uuid4())), ("input_sha256", "0" * 64),
                         ("archive_verified", False), ("independent_archive", False), ("identity_sha256", "0" * 64)):
            changed = {**ack, key: bad}
            with self.subTest(key=key), self.assertRaises(ValueError): self.ledger.acknowledge_baseline(row["id"], owner, changed)
        self.ledger.acknowledge_baseline(row["id"], owner, ack)
        self.ledger.acknowledge_baseline(row["id"], owner, ack)
        with self.assertRaises(ValueError): self.ledger.acknowledge_baseline(row["id"], owner, {**ack, "inventory_sha256": "0" * 64})
        self.assertTrue(self.ledger.authorize_release(row["id"], owner))
        self.assertFalse(self.ledger.authorize_release(row["id"], owner))
        self.assertIsNone(self.ledger.attempt(row["id"])["prompt_release"])

    def test_prompt_release_is_recorded_once_after_authorization(self):
        row, owner = self.claimed(); event = self.release_event(row, owner)
        with self.assertRaises(ValueError): self.ledger.mark_prompt_released(row["id"], owner, event)
        self.ledger.acknowledge_baseline(row["id"], owner, self.baseline(row, owner))
        self.ledger.authorize_release(row["id"], owner)
        with ThreadPoolExecutor(max_workers=8) as pool:
            recorded = list(pool.map(lambda _: self.Ledger(self.dsn).mark_prompt_released(row["id"], owner, event), range(8)))
        self.assertEqual(recorded.count(True), 1)
        with self.assertRaises(ValueError): self.ledger.mark_prompt_released(row["id"], owner, {**event, "monotonic_ns": 999})

    def test_shared_pause_stops_new_claim_and_release_but_preserves_resources(self):
        row, owner = self.claimed(); self.ledger.acknowledge_baseline(row["id"], owner, self.baseline(row, owner))
        pending = self.ledger.reserve("a")
        self.ledger.pause("source integrity")
        self.assertFalse(self.ledger.authorize_release(row["id"], owner))
        self.assertFalse(self.ledger.claim(pending["id"], identity("second")))
        self.assertIsNone(self.ledger.reserve("a"))
        self.assertTrue(self.ledger.attempt(row["id"])["holds_capacity"])

    def test_different_agent_cannot_overlap_unresolved_work(self):
        self.ledger.create_campaign("other", manifest(agent="other-agent", cohort="other-cohort"))
        row, owner = self.released(); self.ledger.quarantine(row["id"], "unknown execution")
        self.assertEqual(self.ledger.admission_block_reason("other"), "another_agent")
        self.assertIsNone(self.ledger.reserve("other"))
        self.ledger.finish(row["id"], owner, self.report(row, owner))
        self.assertIsNotNone(self.ledger.reserve("other"))

    def test_full_configuration_not_display_name_defines_agent_overlap(self):
        self.ledger.reserve("a")
        changed = manifest(cohort="different"); changed["agent"]["model"] = "claude-other-model"
        self.ledger.create_campaign("changed", changed)
        self.assertEqual(self.ledger.admission_block_reason("changed"), "another_agent")

    def test_finish_requires_exact_owner_and_verified_stop_and_drain(self):
        row, owner = self.released(); report = self.report(row, owner)
        for field in ("worker_id", "session_id", "host_id", "daemon_id", "docker_endpoint", "container_id"):
            impostor = {**owner, field: "0" * 64 if field == "container_id" else str(uuid4())}
            with self.subTest(field=field), self.assertRaises(ValueError): self.ledger.finish(row["id"], impostor, report)
        for field in ("stop_verified", "owned_work_drained"):
            bad = copy.deepcopy(report); bad["stop_evidence"][field] = False
            with self.subTest(field=field), self.assertRaises(ValueError): self.ledger.finish(row["id"], owner, bad)
        self.assertTrue(self.ledger.attempt(row["id"])["holds_capacity"])
        self.ledger.finish(row["id"], owner, report)
        self.assertFalse(self.ledger.attempt(row["id"])["holds_capacity"])

    def test_report_is_immutable_and_idempotent_without_allowing_replay(self):
        row, owner = self.released(); report = self.report(row, owner)
        self.ledger.finish(row["id"], owner, report); self.ledger.finish(row["id"], owner, report)
        with self.assertRaises(ValueError): self.ledger.finish(row["id"], owner, {**report, "timing_status": "invalid"})
        self.assertFalse(self.ledger.claim(row["id"], owner))
        self.assertFalse(self.ledger.authorize_release(row["id"], owner))

    def test_valid_natural_timing_cannot_be_claimed_without_observed_release(self):
        row, owner = self.claimed()
        with self.assertRaises(ValueError): self.ledger.finish(row["id"], owner, self.report(row, owner))

    def test_manual_replacement_requires_stop_infrastructure_and_impossible_continuation(self):
        row, owner = self.released(); report = self.report(row, owner, "infrastructure_failure")
        with self.assertRaises(ValueError): self.ledger.reserve_replacement(row["id"], self.authorization(row))
        self.ledger.finish(row["id"], owner, report)
        with self.assertRaises(ValueError): self.ledger.reserve_replacement(row["id"], {**self.authorization(row), "kind": "automatic"})
        replacement = self.ledger.reserve_replacement(row["id"], self.authorization(row))
        self.assertEqual(replacement["ordinal"], 1)
        self.assertEqual(replacement["original_id"], row["id"])
        self.assertEqual(replacement["contract_sha256"], row["contract_sha256"])
        self.assertIsNone(replacement["identity"])
        self.assertIsNone(replacement["baseline_ack"])

    def test_competing_replacement_requests_cannot_create_a_third_attempt(self):
        row, owner = self.released(); self.ledger.finish(row["id"], owner, self.report(row, owner, "infrastructure_failure"))
        with ThreadPoolExecutor(max_workers=12) as pool:
            rows = list(pool.map(lambda _: self.Ledger(self.dsn).reserve_replacement(row["id"], self.authorization(row)), range(12)))
        replacements = [r for r in rows if r]
        self.assertEqual(len(replacements), 1)
        replacement = replacements[0]
        with self.assertRaises(ValueError): self.ledger.reserve_replacement(replacement["id"], self.authorization(replacement))
        self.assertEqual(len(self.ledger.attempts("a")), 2)

    def test_replacement_authorization_remains_immutable_after_restart(self):
        row, owner = self.released()
        self.ledger.finish(row["id"], owner, self.report(row, owner, "infrastructure_failure"))
        replacement = self.ledger.reserve_replacement(row["id"], self.authorization(row))
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE agenttime_natural.attempts SET replacement_authorization=%s WHERE id=%s",
                         (Jsonb({**self.authorization(row), "request_id": "different-request"}), replacement["id"]))
        with self.assertRaises(ValueError): self.Ledger(self.dsn).attempt(replacement["id"])
        self.assertIsNotNone(self.ledger.gate()["paused_reason"])

    def test_replacement_cannot_change_original_lineage(self):
        first, owner = self.released()
        self.ledger.finish(first["id"], owner, self.report(first, owner, "infrastructure_failure"))
        second, other = self.released("second")
        self.ledger.finish(second["id"], other, self.report(second, other, "infrastructure_failure"))
        replacement = self.ledger.reserve_replacement(first["id"], self.authorization(first))
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE agenttime_natural.attempts SET original_id=%s WHERE id=%s", (second["id"], replacement["id"]))
        with self.assertRaises(ValueError): self.Ledger(self.dsn).attempt(replacement["id"])
        self.assertIsNotNone(self.ledger.gate()["paused_reason"])

    def test_wrong_answers_refusals_early_finishes_and_unknowns_never_get_replaced(self):
        for outcome in ("wrong_answer", "refusal", "voluntary_early_finish", "unknown_failure", "agent_resource_exhaustion"):
            with self.subTest(outcome=outcome):
                row, owner = self.released("worker-" + outcome)
                self.ledger.finish(row["id"], owner, self.report(row, owner, outcome))
                with self.assertRaises(ValueError): self.ledger.reserve_replacement(row["id"], self.authorization(row))

    def test_continuation_possible_unknown_or_unproven_blocks_replacement(self):
        row, owner = self.released(); report = self.report(row, owner, "infrastructure_failure")
        report["continuation_status"] = "possible"
        self.ledger.finish(row["id"], owner, report)
        with self.assertRaises(ValueError): self.ledger.reserve_replacement(row["id"], self.authorization(row))

    def test_new_campaign_cannot_reset_logical_slot_or_change_its_pins(self):
        row = self.ledger.reserve("a")
        self.ledger.create_campaign("duplicate-campaign", self.manifest)
        other = self.ledger.reserve("duplicate-campaign")
        self.assertNotEqual(other["task_id"], row["task_id"])
        changed = copy.deepcopy(self.manifest); changed["tasks"][0]["contract_sha256"] = "0" * 64
        with self.assertRaises(ValueError): self.ledger.create_campaign("changed-pins", changed)
        changed = copy.deepcopy(self.manifest); changed["agent"]["id"] = "renamed"
        with self.assertRaises(ValueError): self.ledger.create_campaign("reset-agent", changed)

    def test_manifest_runtime_capacity_and_natural_scope_are_immutable(self):
        self.ledger.create_campaign("a", self.manifest)
        for field, value in (("capacity", True), ("capacity", 221), ("arm", "short"), ("executor", "fixture-v1"),
                             ("automatic_replacement", True), ("runtime_pins", {"cli_sha256": "bad"})):
            changed = copy.deepcopy(self.manifest); changed[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): self.ledger.create_campaign("bad", changed)
        with self.assertRaises(ValueError): self.ledger.initialize(51)
        with self.assertRaises(ValueError): self.ledger.create_campaign("a", manifest(count=4))

    def test_persisted_manifest_or_owner_corruption_pauses_all_admission(self):
        row, owner = self.claimed()
        with psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE agenttime_natural.attempts SET identity=%s WHERE id=%s", (Jsonb({**owner, "worker_id": "changed"}), row["id"]))
        with self.assertRaises(ValueError): self.ledger.authorize_release(row["id"], owner)
        self.assertIsNotNone(self.ledger.gate()["paused_reason"])
        self.assertTrue(self.ledger.attempts("a")[0]["holds_capacity"])

    def test_fixture_schema_stays_separate_and_rejects_natural_executor(self):
        from agenttime.ledger import Ledger
        fixture = Ledger(self.dsn); fixture.initialize(2)
        with self.assertRaises(ValueError): fixture.create_campaign("study", self.manifest)
        self.assertIsNotNone(self.ledger.reserve("a"))
        self.assertEqual(fixture.gate()["capacity"], 2)


if __name__ == "__main__":
    import unittest
    unittest.main()
