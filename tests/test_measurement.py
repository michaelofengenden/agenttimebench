"""Timing comes only from a complete, consistent source journal."""

from copy import deepcopy
import unittest

from agenttime.measurement import project_events


def event(sequence, kind, monotonic_ns, payload=None):
    return {
        "schema_version": 1,
        "attempt_id": "attempt-1",
        "execution_id": "execution-1",
        "event_id": f"event-{sequence}",
        "sequence": sequence,
        "clock_id": "worker-boot-1",
        "monotonic_ns": monotonic_ns,
        "recorded_at": "2026-10-01T12:00:00+00:00",
        "kind": kind,
        "payload": {} if payload is None else payload,
    }


def history():
    return [
        event(1, "prepared", 10),
        event(2, "prompt_released", 1_000_000_000),
        event(3, "native_terminal", 3_500_000_000,
              {"submission_b64": "NDI=", "submission_sha256": "fixture-digest"}),
        event(4, "grade_failed", 100_000_000_000),
        event(5, "journal_closed", 101_000_000_000, {"final_sequence": 5}),
    ]


class MeasurementTests(unittest.TestCase):
    def assert_unavailable(self, events, status, reason):
        result = project_events(events)
        self.assertEqual(result["timing_status"], status)
        self.assertEqual(result["reason"], reason)
        self.assertIsNone(result["runtime_seconds"])
        return result

    def test_only_native_boundaries_determine_runtime(self):
        events = history()
        original = deepcopy(events)
        self.assertEqual(project_events(events), {
            "timing_status": "valid", "runtime_seconds": 2.5,
            "reason": None, "terminal": events[2],
        })
        self.assertEqual(events, original)

    def test_transport_order_and_exact_repeated_delivery_do_not_change_timing(self):
        events = history()
        duplicate = dict(reversed(list(events[2].items())))
        result = project_events([events[4], duplicate, events[0], events[3],
                                 events[1], deepcopy(events[2]), events[4]])
        self.assertEqual(result["timing_status"], "valid")
        self.assertEqual(result["runtime_seconds"], 2.5)

    def test_terminal_delivered_first_stays_pending_until_earlier_records_arrive(self):
        events = history()
        result = self.assert_unavailable([events[2], events[4]], "pending", "missing_events")
        self.assertEqual(result["terminal"], events[2])
        self.assertEqual(project_events(events[::-1])["runtime_seconds"], 2.5)

    def test_complete_boundaries_without_closure_remain_pending(self):
        self.assert_unavailable(history()[:-1], "pending", "journal_open")

    def test_empty_journal_is_pending(self):
        result = self.assert_unavailable([], "pending", "empty_history")
        self.assertIsNone(result["terminal"])

    def test_sparse_sequence_cannot_be_admitted(self):
        events = history()
        for omitted in range(4):
            with self.subTest(omitted=omitted):
                self.assert_unavailable(events[:omitted] + events[omitted + 1:],
                                        "pending", "missing_events")

    def test_missing_boundaries_in_closed_history_are_invalid(self):
        for index, reason in [(1, "missing_prompt_released"), (2, "missing_native_terminal")]:
            with self.subTest(reason=reason):
                events = history()
                events[index]["kind"] = "process_exited"
                self.assert_unavailable(events, "invalid", reason)

    def test_multiple_native_terminals_are_invalid_even_when_payloads_match(self):
        events = history()
        events[3]["kind"] = "native_terminal"
        events[3]["payload"] = deepcopy(events[2]["payload"])
        result = self.assert_unavailable(events, "invalid", "multiple_native_terminals")
        self.assertIsNone(result["terminal"])

    def test_multiple_prompt_releases_are_invalid(self):
        events = history()
        events[0]["kind"] = "prompt_released"
        self.assert_unavailable(events, "invalid", "multiple_prompt_releases")

    def test_conflicting_event_id_is_not_an_idempotent_delivery(self):
        events = history()
        changed = deepcopy(events[2])
        changed["monotonic_ns"] += 1
        result = self.assert_unavailable(events + [changed], "invalid", "conflicting_event_id")
        self.assertIsNone(result["terminal"])

    def test_json_boolean_and_number_payloads_are_not_exact_duplicates(self):
        events = history()
        events[2]["payload"] = {"value": 1}
        changed = deepcopy(events[2])
        changed["payload"] = {"value": True}
        self.assert_unavailable(events + [changed], "invalid", "conflicting_event_id")

    def test_conflicting_sequence_is_invalid(self):
        events = history()
        changed = deepcopy(events[2])
        changed["event_id"] = "second-event-at-sequence-3"
        self.assert_unavailable(events + [changed], "invalid", "conflicting_sequence")

    def test_attempt_execution_and_clock_identity_must_each_be_uniform(self):
        for field, reason in [("attempt_id", "mixed_attempts"),
                              ("execution_id", "mixed_executions"),
                              ("clock_id", "mixed_clocks")]:
            with self.subTest(field=field):
                events = history()
                events[2][field] = "other"
                self.assert_unavailable(events, "invalid", reason)

    def test_monotonic_clock_cannot_move_backwards_in_source_order(self):
        events = history()
        events[3]["monotonic_ns"] = 2_000_000_000
        self.assert_unavailable(events[::-1], "invalid", "nonmonotonic_clock")

    def test_terminal_cannot_precede_prompt_in_source_order(self):
        events = history()
        events[1]["kind"], events[2]["kind"] = "native_terminal", "prompt_released"
        self.assert_unavailable(events, "invalid", "terminal_before_prompt")

    def test_equal_boundary_readings_are_a_valid_zero_interval(self):
        events = history()
        events[2]["monotonic_ns"] = events[1]["monotonic_ns"]
        result = project_events(events)
        self.assertEqual(result["timing_status"], "valid")
        self.assertEqual(result["runtime_seconds"], 0.0)

    def test_audit_clock_skew_does_not_repair_or_change_monotonic_timing(self):
        events = history()
        events[2]["recorded_at"] = "2025-01-01T00:00:00Z"
        self.assertEqual(project_events(events)["runtime_seconds"], 2.5)

    def test_submission_capture_validation_is_independent_of_timing(self):
        events = history()
        events[2]["payload"] = {"submission_b64": "not-base64", "submission_sha256": "bad"}
        self.assertEqual(project_events(events)["timing_status"], "valid")

    def test_closure_must_declare_its_own_sequence_as_final(self):
        for final in [None, True, 0, 4, 6, 5.0, "5"]:
            with self.subTest(final=final):
                events = history()
                events[-1]["payload"] = {"final_sequence": final}
                self.assert_unavailable(events, "invalid", "invalid_journal_closure")
        events = history()
        events[-1]["payload"] = {}
        self.assert_unavailable(events, "invalid", "invalid_journal_closure")

    def test_multiple_closing_records_are_invalid(self):
        events = history()
        events[3] = event(4, "journal_closed", 100_000_000_000, {"final_sequence": 4})
        self.assert_unavailable(events, "invalid", "multiple_journal_closures")

    def test_events_after_closure_are_invalid(self):
        events = history() + [event(6, "late_write", 102_000_000_000)]
        self.assert_unavailable(events, "invalid", "events_after_journal_closed")

    def test_all_required_fields_are_validated(self):
        for field in history()[0]:
            with self.subTest(missing=field):
                events = history()
                del events[0][field]
                self.assert_unavailable(events, "invalid", "invalid_event_schema")
        invalid = {
            "schema_version": [True, 2, "1"],
            "attempt_id": ["", " ", None, 1],
            "execution_id": ["", []], "event_id": ["", False], "clock_id": ["", {}],
            "sequence": [True, False, 0, -1, 1.5, "1"],
            "monotonic_ns": [True, -1, 10.0, "10"],
            "recorded_at": [None, "", "2026-10-01", "2026-10-01T12:00:00",
                            "2026-10-01T12:00:00+01:00", "2026-02-30T00:00:00Z"],
            "kind": [None, "", " "],
            "payload": [None, [], {1: "coerced-key"}, {"nan": float("nan")},
                        {"infinity": float("inf")}, {"tuple": (1, 2)}],
        }
        for field, values in invalid.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    events = history()
                    events[0][field] = value
                    self.assert_unavailable(events, "invalid", "invalid_event_schema")

    def test_unexpected_fields_and_non_events_are_rejected(self):
        events = history()
        events[0]["controller_finish_time"] = 10
        self.assert_unavailable(events, "invalid", "invalid_event_schema")
        for malformed in [None, {}, (), "events", [None], [True], [[]]]:
            with self.subTest(malformed=malformed):
                self.assert_unavailable(malformed, "invalid", "invalid_event_schema")

    def test_unrepresentable_runtime_is_unavailable_not_infinity(self):
        events = history()
        for index in [2, 3, 4]:
            events[index]["monotonic_ns"] = 10 ** 400 + index
        self.assert_unavailable(events, "invalid", "runtime_out_of_range")


if __name__ == "__main__":
    unittest.main()
