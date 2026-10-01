"""The run timer: the parent process of the agent CLI inside the task container.

Usage: python3 supervisor.py CONFIG.json

CONFIG holds "command" (the exact native CLI command), "backstop_sec", "receipt_path"
and optionally "cleanup_grace_sec" (default 5) and "process_policy". The supervisor
records a monotonic start just before it spawns the command, waits until the CLI and
every process it started have exited, and records the end: the runtime is
elapsed_ms = owned_end - root_start. At the hidden backstop it sends SIGTERM to every
owned process for the grace period, then SIGKILL for another grace period. With
"terminal_protocol" ("claude-code" or "codex") and "log_path" (the CLI's tee'd output;
Claude Code also needs "expected_session_id" and "expected_model"), a natural end also
requires the CLI's stream to end in one successful final event.

Standard library only, for the task image's own python3 (3.9+). Process ownership uses
Linux /proc and PR_SET_CHILD_SUBREAPER; the `ps` fallback only lets the tests run on macOS.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

# Natural end requires root exit 0 and no failed descendant (default; all GPQA/HLE runs).
STRICT = "strict-descendants-v1"
# Natural end requires root exit 0 and waitpid reporting no children left; descendant
# failures are only recorded (most coding-task runs).
QUIESCENCE = "owned-quiescence-v1"


def _process_table() -> dict[int, tuple[int, object, str, int]]:
    """pid -> (parent pid, start identity, state, process group)."""
    table = {}
    if sys.platform == "linux":
        for entry in os.scandir("/proc"):
            if not entry.name.isdigit():
                continue
            try:
                stat = Path(f"/proc/{entry.name}/stat").read_text()
            except (FileNotFoundError, ProcessLookupError):
                continue
            fields = stat[stat.rfind(")") + 2:].split()
            table[int(entry.name)] = (int(fields[1]), int(fields[19]), fields[0], int(fields[2]))
        return table
    ps = subprocess.Popen(["ps", "-A", "-o", "pid=,ppid=,pgid=,stat=,lstart="],
                          stdout=subprocess.PIPE, text=True)
    output, _ = ps.communicate()
    for line in output.splitlines():
        pid, ppid, pgid, state, start = line.split(None, 4)
        if int(pid) != ps.pid:
            table[int(pid)] = (int(ppid), start, state[0], int(pgid))
    return table


def _owned(root_pid: int | None) -> dict[int, tuple[int, object, str, int]]:
    """Every descendant of this supervisor, including orphans adopted as subreaper."""
    table = _process_table()
    family = {os.getpid()}
    if sys.platform != "linux" and root_pid is not None:
        # No subreaper outside Linux: find orphans through the root's process group.
        family |= {pid for pid, row in table.items() if row[3] == root_pid}
    while True:
        grown = family | {pid for pid, row in table.items() if row[0] in family}
        if grown == family:
            break
        family = grown
    return {pid: table[pid] for pid in family if pid != os.getpid() and pid in table}


def _events(path: Path) -> list[dict]:
    """JSON events of the CLI log; plain diagnostic lines merged from stderr are skipped."""
    events = []
    with path.open(encoding="utf-8") as stream:  # not str.splitlines(): JSON text may hold U+2028
        for line in stream:
            text = line.strip()
            if not text:
                continue
            try:
                event = json.loads(text)
            except ValueError:
                if text.startswith(("{", "[")):
                    raise
                continue
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                raise ValueError("malformed native event")
            events.append(event)
    return events


def terminal_ok(config: dict) -> bool:
    """Claude Code: exactly one system init and one `result`, the last event, with subtype
    success and is_error false, for the expected session and model. Codex: exactly one
    thread.started < turn.started < turn.completed, the last event, and no failure event."""
    try:
        events = _events(Path(config["log_path"]))
    except (OSError, ValueError):
        return False
    kinds = [event["type"] for event in events]
    if config["terminal_protocol"] == "codex":
        if (any(kinds.count(kind) != 1 for kind in ("thread.started", "turn.started", "turn.completed"))
                or "turn.failed" in kinds or "error" in kinds):
            return False
        start = kinds.index("thread.started")
        return (start < kinds.index("turn.started") and kinds[-1] == "turn.completed"
                and isinstance(events[start].get("thread_id"), str) and bool(events[start]["thread_id"]))
    session, model = config["expected_session_id"], config["expected_model"]
    inits = [event for event in events if event["type"] == "system" and event.get("subtype") == "init"]
    result = events[-1] if events else {}
    return (len(inits) == 1 and kinds.count("result") == 1 and result.get("type") == "result"
            and inits[0].get("model") == model
            and all(event.get("session_id") in (None, session) for event in events)
            and inits[0].get("session_id") == session and result.get("session_id") == session
            and result.get("subtype") == "success" and result.get("is_error") is False)


def _write_receipt(path: Path, receipt: dict) -> None:
    """Replace the receipt atomically, so a reader never sees a partial one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(receipt, sort_keys=True, allow_nan=False) + "\n")
    os.replace(partial, path)


def supervise(config: dict) -> dict:
    if sys.platform == "linux":
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            raise OSError(ctypes.get_errno(), "cannot become child subreaper")
    receipt_path = Path(config["receipt_path"])
    backstop_sec = float(config["backstop_sec"])
    grace_sec = float(config.get("cleanup_grace_sec", 5.0))
    policy = config.get("process_policy", STRICT)
    if (backstop_sec <= 0 or not 0 < grace_sec <= 15 or policy not in (STRICT, QUIESCENCE)
            or config.get("terminal_protocol", "codex") not in ("claude-code", "codex")):
        raise ValueError("invalid supervisor config")
    receipt = {
        "backstop_sec": backstop_sec, "process_policy": policy, "root_pid": None,
        "root_start_monotonic_ns": None, "root_end_monotonic_ns": None, "owned_end_monotonic_ns": None,
        "elapsed_ms": None, "root_exit_code": None, "timed_out": False, "interrupted": False,
        "natural_quiescence": False, "descendant_failure": False, "cleanup_complete": False,
        "term_signal_count": 0, "kill_signal_count": 0, "error_code": None,
    }
    stopped = []
    old_handlers = {sig: signal.signal(sig, lambda signum, frame: stopped.append(signum))
                    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    process = None
    quiescence = policy == QUIESCENCE

    def reap() -> bool:
        """Collect exit statuses; True once waitpid reports no children at all."""
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return True
            if not pid:
                return False
            reaped_at = time.monotonic_ns()
            code = os.waitstatus_to_exitcode(status)
            if process is not None and pid == process.pid:
                process.returncode = code
                receipt["root_exit_code"] = code
                receipt["root_end_monotonic_ns"] = reaped_at
            elif code != 0:
                receipt["descendant_failure"] = True

    def refresh() -> dict:
        return _owned(process.pid if process is not None else None)

    def send(sig) -> None:
        owned = refresh()
        current = _process_table()
        for pid, row in owned.items():
            now = current.get(pid)
            # Skip exited, zombie and reused pids.
            if now is None or now[1] != row[1] or now[2] == "Z":
                continue
            try:
                os.kill(pid, sig)
                receipt["term_signal_count" if sig == signal.SIGTERM else "kill_signal_count"] += 1
            except OSError:
                pass

    try:
        _write_receipt(receipt_path, receipt)
        receipt["root_start_monotonic_ns"] = time.monotonic_ns()
        deadline = receipt["root_start_monotonic_ns"] / 1e9 + backstop_sec
        process = subprocess.Popen(["/bin/bash", "-o", "pipefail", "-c", config["command"]],
                                   stdin=subprocess.DEVNULL, start_new_session=True)
        receipt["root_pid"] = process.pid
        _write_receipt(receipt_path, receipt)
        while True:
            refresh()  # census before reaping, as in the run code; keeps its poll cadence
            no_children = reap()
            remaining = refresh()
            if stopped:
                receipt["interrupted"] = True
                receipt["error_code"] = "supervisor_interrupted"
                break
            if time.monotonic() >= deadline:
                receipt["timed_out"] = True
                receipt["error_code"] = "duration_backstop_exceeded"
                break
            if process.returncode is not None and not remaining and (not quiescence or no_children):
                receipt["owned_end_monotonic_ns"] = time.monotonic_ns()
                receipt["cleanup_complete"] = True
                receipt["natural_quiescence"] = True
                break
            time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))
        if receipt["natural_quiescence"] and (
                receipt["root_exit_code"] != 0 or (policy == STRICT and receipt["descendant_failure"])):
            receipt["error_code"] = "native_process_failure"
        elif receipt["natural_quiescence"] and "terminal_protocol" in config and not terminal_ok(config):
            receipt["error_code"] = "native_terminal_invalid"
    except BaseException:
        receipt["error_code"] = receipt["error_code"] or "supervisor_failure"
    finally:
        if not receipt["cleanup_complete"]:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                until = time.monotonic() + grace_sec
                while refresh() and time.monotonic() < until:
                    send(sig)
                    reap()
                    time.sleep(0.02)
            reap()
            receipt["cleanup_complete"] = not refresh()
            if receipt["cleanup_complete"]:
                receipt["owned_end_monotonic_ns"] = time.monotonic_ns()
            else:
                receipt["error_code"] = "owned_process_cleanup_incomplete"
        if receipt["owned_end_monotonic_ns"] and receipt["root_start_monotonic_ns"]:
            receipt["elapsed_ms"] = (receipt["owned_end_monotonic_ns"]
                                     - receipt["root_start_monotonic_ns"]) / 1e6
        _write_receipt(receipt_path, receipt)
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
    return receipt


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    receipt = supervise(json.loads(Path(argv[1]).read_text()))
    return 0 if receipt["error_code"] is None else 70


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
