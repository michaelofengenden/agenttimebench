"""Private, standalone owner API. This module is not a campaign executor."""

from copy import deepcopy
from datetime import datetime, timezone
import json
import inspect
import threading
import time
from uuid import uuid4

from . import SOURCE_COMMIT, VARIANT
from .prompts import adapt_prompt, canonical, digest


OWNER_STATE_SCHEMA = 2


class BackendError(ValueError):
    """Safe, non-sensitive adapter error."""


def _reject_private(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if (
                not isinstance(key, str)
                or key.startswith("_")
                or key in {"model_config", "model_fields", "model_computed_fields"}
            ):
                raise BackendError("Private or unsupported argument")
            _reject_private(child)
    elif isinstance(value, list):
        for child in value:
            _reject_private(child)
    elif isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            return
        _reject_private(decoded)


class Backend:
    def __init__(self, native, task, attempt_id):
        if not isinstance(attempt_id, str) or not attempt_id.strip():
            raise BackendError("Attempt identity required")
        self.native = native
        self._task = deepcopy(task)
        self.attempt_id = attempt_id
        self.prompt = adapt_prompt(task["prompt"])
        self.tools = dict(native.functions)
        self._normalized, self._world = native.initial(task)
        self._constructed = native.snapshot(self._world)
        self._contract = native.contract(
            example_id=task["example_id"], prompt=task["prompt"], info=task["info"]
        )
        self._lock = threading.RLock()
        self._closing = threading.Event()
        self._released = False
        self._sealed = None
        self._tainted = False
        self._receipts = []
        self._index = {}
        self._events = []
        self._clock_id = str(uuid4())
        self._failures = []
        self._event("prepared")

    def _event(self, kind, **data):
        self._events.append(
            {
                "sequence": len(self._events) + 1,
                "kind": kind,
                "clock_id": self._clock_id,
                "monotonic_ns": time.monotonic_ns(),
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                **data,
            }
        )

    def tool_schemas(self):
        return deepcopy(self.native.schemas)

    def release(self):
        with self._lock:
            if self._released or self._closing.is_set():
                raise BackendError("Backend cannot be released")
            self._released = True
            self._event("model_free_owner_release", native_timing_qualified=False)

    def _validate(self, name, args):
        if name not in self.tools or type(args) is not dict:
            raise BackendError("Unknown tool or invalid arguments")
        _reject_private(args)
        schema = next(
            s["inputSchema"] for s in self.native.schemas if s["name"] == name
        )
        if set(args) - set(schema["properties"]):
            raise BackendError("Unsupported tool argument")
        # Match native optional {} normalization before checking the native schema.
        normalized = {
            k: v for k, v in args.items() if not (isinstance(v, dict) and not v)
        }
        validation = deepcopy(normalized)
        for key, param in inspect.signature(
            self.native.functions[name]
        ).parameters.items():
            if (
                key != "world"
                and key not in validation
                and param.default is not inspect.Parameter.empty
            ):
                validation[key] = param.default
        try:
            self.native.validate(validation, schema)
        except Exception:
            raise BackendError("Invalid tool arguments") from None
        if name == "api_fetch":
            for key in ("params", "body"):
                value = normalized.get(key)
                if isinstance(value, str):
                    try:
                        parsed = json.loads(value)
                    except (ValueError, TypeError):
                        continue  # Preserve native errors and QuickBooks plain-query support.
                    _reject_private(parsed)
        return normalized

    def call(self, epoch, request_id, name, args):
        if type(epoch) is not str or not epoch or type(request_id) not in (int, str):
            raise BackendError("Transport identity required")
        key = canonical([self.attempt_id, epoch, request_id]).decode()
        try:
            payload = digest({"name": name, "arguments": args})
        except (ValueError, TypeError):
            raise BackendError("Invalid tool arguments") from None
        with self._lock:
            if key in self._index:
                receipt = self._receipts[self._index[key]]
                if receipt["payload_sha256"] != payload:
                    raise BackendError("Conflicting request identity")
                return deepcopy(receipt)
            if not self._released or self._closing.is_set() or self._tainted:
                raise BackendError("Backend is not accepting new calls")
            normalized = self._validate(name, args)
            try:
                before = self.native.snapshot(self._world)
            except Exception:
                self._tainted = True
                raise BackendError("Runtime state is unavailable") from None
            self._event("tool_started", request_key=key)
            error = False
            try:
                result = self.native.invoke(name, normalized, self._world)
                if type(result) is not str:
                    raise TypeError("Native result must be text")
                after = self.native.snapshot(self._world)
            except Exception as exc:
                self._tainted = True
                error = True
                self._failures.append(
                    {
                        "request_key": key,
                        "exception_type": type(exc).__name__,
                        "exception_text": str(exc),
                    }
                )
                try:
                    after = self.native.snapshot(self._world)
                except Exception:
                    after = None
                result = "Native operation failed; this attempt requires controller reconciliation."
            receipt = {
                "attempt_id": self.attempt_id,
                "epoch": epoch,
                "request_id": request_id,
                "tool": name,
                "payload_sha256": payload,
                "sequence": len(self._receipts) + 1,
                "result": result,
                "is_error": error,
                "before_sha256": digest(before),
                "after_sha256": digest(after) if after is not None else None,
            }
            self._index[key] = len(self._receipts)
            self._receipts.append(receipt)
            self._event(
                "tool_committed" if not error else "tool_failed",
                receipt_sequence=receipt["sequence"],
                state_sha256=receipt["after_sha256"],
            )
            return deepcopy(receipt)

    def seal(self):
        self._closing.set()  # Close admission before waiting on an accepted native operation.
        with self._lock:
            if self._sealed is None:
                try:
                    self._sealed = self.native.snapshot(self._world)
                except Exception:
                    self._tainted = True
                    self._sealed = {"unavailable": True}
                self._event(
                    "owner_sealed",
                    native_terminal_capture_qualified=False,
                    committed_calls=len(self._receipts),
                    outstanding_calls=0,
                    tainted=self._tainted,
                )
            return deepcopy(self._sealed)

    def grade(self):
        with self._lock:
            if self._sealed is None or self._tainted:
                return {
                    "status": "unavailable",
                    "reason": "unsealed_or_unresolved_attempt",
                }
            try:
                return self.native.grade(self._task, self._sealed, self._constructed)
            except Exception as exc:
                self._failures.append(
                    {
                        "exception_type": type(exc).__name__,
                        "exception_text": str(exc),
                        "phase": "grade",
                    }
                )
                return {"status": "unavailable", "reason": "native_grader_failed"}

    def owner_state(self):
        with self._lock:
            try:
                current = self.native.snapshot(self._world)
            except Exception as exc:
                raise BackendError("Runtime state cannot be checkpointed") from exc
            return {
                "schema": OWNER_STATE_SCHEMA,
                "source_commit": SOURCE_COMMIT,
                "source_tree_sha256": self.native.source_sha256,
                "variant": VARIANT,
                "task_contract_sha256": self._contract,
                "attempt_id": self.attempt_id,
                "raw_task": deepcopy(self._task),
                "normalized_initial": deepcopy(self._normalized),
                "constructed_initial": deepcopy(self._constructed),
                "current": current,
                "prompt": deepcopy(self.prompt),
                "released": self._released,
                "closing": self._closing.is_set(),
                "sealed": deepcopy(self._sealed),
                "tainted": self._tainted,
                "receipts": deepcopy(self._receipts),
                "events": deepcopy(self._events),
                "failures": deepcopy(self._failures),
                "qualification": {
                    "backend_only": True,
                    "study_launch_ready": False,
                    "native_harness_qualified": False,
                    "production_controller_qualified": False,
                    "archive_qualified": False,
                },
            }

    def _export(self, *, require_resumable):
        with self._lock:
            state = self.owner_state()
            # seal() closes admission outside this lock. Classify this exact
            # snapshot, rather than a live flag checked before or after capture.
            seal_event = state["events"][-1]["kind"] == "owner_sealed"
            if state["closing"]:
                consistent = (
                    state["sealed"] is not None
                    and state["sealed"] == state["current"]
                    and seal_event
                )
            else:
                consistent = state["sealed"] is None and not seal_event
            resumable = not state["tainted"] and consistent
            if require_resumable and not resumable:
                raise BackendError("Captured state is not resumable")
            return canonical(
                {
                    "payload": state,
                    "sha256": digest(state),
                    "resumable": resumable,
                }
            )

    def diagnostic_export(self):
        return self._export(require_resumable=False)

    def checkpoint(self):
        return self._export(require_resumable=True)

    @classmethod
    def restore(cls, native, task, checkpoint):
        try:
            envelope = json.loads(checkpoint)
            if set(envelope) != {"payload", "sha256", "resumable"}:
                raise ValueError("shape")
            state = envelope["payload"]
            if (
                type(state.get("schema")) is not int
                or state["schema"] != OWNER_STATE_SCHEMA
            ):
                raise ValueError("unsupported owner-state schema")
            if (
                envelope["resumable"] is not True
                or state["tainted"] is not False
                or digest(state) != envelope["sha256"]
            ):
                raise ValueError("integrity")
            obj = cls(native, task, state["attempt_id"])
            if (
                state["source_commit"] != SOURCE_COMMIT
                or state["source_tree_sha256"] != native.source_sha256
                or state["variant"] != VARIANT
                or state["task_contract_sha256"] != obj._contract
                or state["raw_task"] != task
                or state["prompt"] != obj.prompt
                or state["normalized_initial"] != obj._normalized
            ):
                raise ValueError("identity")
            native.restore_world(state["constructed_initial"])
            obj._constructed = deepcopy(state["constructed_initial"])
            obj._world = native.restore_world(state["current"])
            obj._receipts = deepcopy(state["receipts"])
            obj._index = {}
            revision = digest(obj._constructed)
            for i, r in enumerate(obj._receipts):
                if (
                    r["sequence"] != i + 1
                    or r["attempt_id"] != obj.attempt_id
                    or r["is_error"]
                ):
                    raise ValueError("receipt")
                if r["before_sha256"] != revision:
                    raise ValueError("receipt state chain")
                revision = r["after_sha256"]
                key = canonical([obj.attempt_id, r["epoch"], r["request_id"]]).decode()
                if key in obj._index:
                    raise ValueError("duplicate receipt")
                obj._index[key] = i
            if revision != digest(state["current"]):
                raise ValueError("current state disagrees with receipt chain")
            if (
                type(state["released"]) is not bool
                or type(state["closing"]) is not bool
            ):
                raise ValueError("lifecycle")
            pending = set()
            committed = set()
            released = False
            sealed = False
            previous_by_clock = {}
            closed_clocks = set()
            current_clock = None
            for sequence, event in enumerate(state["events"], 1):
                clock = event["clock_id"]
                if (
                    type(clock) is not str
                    or not clock
                    or type(event["monotonic_ns"]) is not int
                ):
                    raise ValueError("clock identity")
                if current_clock != clock:
                    if clock in closed_clocks:
                        raise ValueError("closed owner clock reused")
                    if current_clock is not None:
                        closed_clocks.add(current_clock)
                    current_clock = clock
                if event["sequence"] != sequence or event[
                    "monotonic_ns"
                ] < previous_by_clock.get(clock, -1):
                    raise ValueError("journal order")
                previous_by_clock[clock] = event["monotonic_ns"]
                kind = event["kind"]
                if sealed:
                    raise ValueError("event after seal")
                if kind == "prepared":
                    if sequence != 1:
                        raise ValueError("prepare order")
                elif kind == "model_free_owner_release":
                    if released:
                        raise ValueError("duplicate release")
                    released = True
                elif kind == "tool_started":
                    if not released or event["request_key"] in pending:
                        raise ValueError("start order")
                    pending.add(event["request_key"])
                elif kind == "tool_committed":
                    index = event["receipt_sequence"] - 1
                    r = obj._receipts[index]
                    key = canonical(
                        [obj.attempt_id, r["epoch"], r["request_id"]]
                    ).decode()
                    if (
                        key not in pending
                        or index in committed
                        or event["state_sha256"] != r["after_sha256"]
                    ):
                        raise ValueError("commit identity")
                    pending.remove(key)
                    committed.add(index)
                elif kind == "owner_sealed":
                    if pending or event["tainted"] or event["outstanding_calls"] != 0:
                        raise ValueError("unresolved seal")
                    sealed = True
                else:
                    raise ValueError("unexpected journal event")
            if (
                pending
                or committed != set(range(len(obj._receipts)))
                or released != state["released"]
                or sealed != state["closing"]
            ):
                raise ValueError("incomplete journal")
            obj._released = state["released"]
            obj._events = deepcopy(state["events"])
            obj._failures = deepcopy(state["failures"])
            if state["closing"]:
                if state["sealed"] != state["current"]:
                    raise ValueError("seal mismatch")
                native.restore_world(state["sealed"])
                obj._sealed = deepcopy(state["sealed"])
                obj._closing.set()
            elif state["sealed"] is not None:
                raise ValueError("seal state")
            return obj
        except Exception:
            raise BackendError("Checkpoint integrity or identity rejected") from None
