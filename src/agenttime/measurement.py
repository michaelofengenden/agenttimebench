"""Project source journals without inventing native boundaries or clock history."""

from copy import deepcopy
from datetime import datetime
import math
import re

from .evidence import canonical_json


_FIELDS = {
    "schema_version", "attempt_id", "execution_id", "event_id", "sequence",
    "clock_id", "monotonic_ns", "recorded_at", "kind", "payload",
}
_UTC = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
                  r"(?:\.[0-9]+)?(?:Z|\+00:00)")


def _valid_event(event) -> bool:
    if type(event) is not dict or set(event) != _FIELDS:
        return False
    if type(event["schema_version"]) is not int or event["schema_version"] != 1:
        return False
    for name in ("attempt_id", "execution_id", "event_id", "clock_id", "kind"):
        if type(event[name]) is not str or not event[name].strip():
            return False
    for name, minimum in (("sequence", 1), ("monotonic_ns", 0)):
        if type(event[name]) is not int or event[name] < minimum:
            return False
    recorded = event["recorded_at"]
    if type(recorded) is not str or _UTC.fullmatch(recorded) is None:
        return False
    try:
        datetime.fromisoformat(recorded.replace("Z", "+00:00"))
    except ValueError:
        return False
    return type(event["payload"]) is dict


def project_events(events: list[dict]) -> dict:
    """Return timing status, native runtime, stable reason, and a unique terminal.

    Transport order and exactly repeated content are harmless. A journal is
    complete only when sequences 1 through journal_closed are all present.
    The closing record includes itself in payload.final_sequence. Audit UTC,
    process exit, submission capture and grading never determine runtime.
    """
    terminal = None

    def result(status, reason=None, runtime=None):
        return {"timing_status": status, "runtime_seconds": runtime,
                "reason": reason, "terminal": deepcopy(terminal)}

    if type(events) is not list:
        return result("invalid", "invalid_event_schema")
    if not events:
        return result("pending", "empty_history")

    by_id = {}
    by_sequence = {}
    for event in events:
        if not _valid_event(event):
            return result("invalid", "invalid_event_schema")
        try:
            content = canonical_json(event)
        except (TypeError, ValueError, RecursionError):
            return result("invalid", "invalid_event_schema")
        event_id, sequence = event["event_id"], event["sequence"]
        if event_id in by_id:
            if by_id[event_id] != content:
                return result("invalid", "conflicting_event_id")
            continue
        if sequence in by_sequence:
            return result("invalid", "conflicting_sequence")
        by_id[event_id] = content
        by_sequence[sequence] = event

    ordered = [by_sequence[sequence] for sequence in sorted(by_sequence)]
    terminals = [event for event in ordered if event["kind"] == "native_terminal"]
    if len(terminals) > 1:
        return result("invalid", "multiple_native_terminals")
    terminal = terminals[0] if terminals else None
    for field, reason in (("attempt_id", "mixed_attempts"),
                          ("execution_id", "mixed_executions"),
                          ("clock_id", "mixed_clocks")):
        if len({event[field] for event in ordered}) != 1:
            return result("invalid", reason)
    if any(right["monotonic_ns"] < left["monotonic_ns"]
           for left, right in zip(ordered, ordered[1:])):
        return result("invalid", "nonmonotonic_clock")

    prompts = [event for event in ordered if event["kind"] == "prompt_released"]
    closings = [event for event in ordered if event["kind"] == "journal_closed"]
    if len(prompts) > 1:
        return result("invalid", "multiple_prompt_releases")
    if len(closings) > 1:
        return result("invalid", "multiple_journal_closures")
    if closings:
        closing = closings[0]
        final_sequence = closing["payload"].get("final_sequence")
        if type(final_sequence) is not int or final_sequence != closing["sequence"]:
            return result("invalid", "invalid_journal_closure")
        if ordered[-1]["sequence"] > final_sequence:
            return result("invalid", "events_after_journal_closed")
    if prompts and terminal and terminal["sequence"] < prompts[0]["sequence"]:
        return result("invalid", "terminal_before_prompt")
    if any(event["sequence"] != expected for expected, event in enumerate(ordered, 1)):
        return result("pending", "missing_events")
    if not closings:
        return result("pending", "journal_open")
    if not prompts:
        return result("invalid", "missing_prompt_released")
    if terminal is None:
        return result("invalid", "missing_native_terminal")
    try:
        runtime = (terminal["monotonic_ns"] - prompts[0]["monotonic_ns"]) / 1_000_000_000
    except OverflowError:
        return result("invalid", "runtime_out_of_range")
    if not math.isfinite(runtime):
        return result("invalid", "runtime_out_of_range")
    return result("valid", runtime=runtime)
