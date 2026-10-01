#!/bin/bash
# No-request run of GPT-5.6 Sol in the Codex CLI: the task text, no requested duration, no cap.
#   run_sol.sh [--legacy-wrapper] <instructions_file> <source> <task_id> [rep=1] [workspace_seed]
# Model and effort come from codex_sol.toml, installed as config.toml in a fresh CODEX_HOME per run
# (auth.json is linked from CODEX_AUTH, default ~/.codex/auth.json).
# Writes $ART_ROOT/gpt-5.6-sol/<source>__<task_id>/r<rep>/ (ART_ROOT defaults to ./runs).
set -uo pipefail
LEGACY=0
if [ "${1:-}" = --legacy-wrapper ]; then LEGACY=1; shift; fi   # runs before 27 July 2026
if [ "$#" -lt 3 ]; then
  echo "usage: run_sol.sh [--legacy-wrapper] <instructions_file> <source> <task_id> [rep] [workspace_seed]" >&2; exit 2
fi
INSTR="$1"; SRC="$2"; TASK="$3"; REP="${4:-1}"; WSSEED="${5:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
CAP_MIN="${EXEC_CAP_MIN:-0}"   # 0 = no cap (alarm 0 sets no timer)
CODEX_BIN="${CODEX_BIN:-codex}"
VER="$("$CODEX_BIN" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
if [ "$(printf '%s\n%s\n' 0.145.0 "$VER" | sort -V | head -1)" != 0.145.0 ]; then
  echo "FATAL: codex ${VER:-?} is older than 0.145.0, which gpt-5.6-sol requires" >&2; exit 91
fi
SAFE=$(printf '%s__%s' "$SRC" "$TASK" | sed 's/[^A-Za-z0-9._-]/_/g')
ART="${ART_ROOT:-runs}/gpt-5.6-sol/$SAFE/r$REP"
if [ -f "$ART/meta.json" ]; then echo "refusing to overwrite $ART" >&2; exit 6; fi
mkdir -p "$ART"; ART="$(cd "$ART" && pwd)"
WORK="$ART/workspace"; rm -rf "$WORK"; mkdir -p "$WORK"
if [ -n "$WSSEED" ] && [ -d "$WSSEED" ]; then cp -R "$WSSEED/." "$WORK/"; fi
# Web search stays disabled except for open-web tasks.
case "$SAFE" in AssistantBench__*|Agents_Last_Exam__business_finance_ar_full_*) EXEC_WEB="${EXEC_WEB-1}";; esac
export CODEX_HOME; CODEX_HOME="$(mktemp -d)"
trap 'rm -rf "$CODEX_HOME"' EXIT
cp "$HERE/codex_sol.toml" "$CODEX_HOME/config.toml"
ln -s "${CODEX_AUTH:-$HOME/.codex/auth.json}" "$CODEX_HOME/auth.json"

INSTRUCTIONS=$(cat "$INSTR")   # drops trailing newlines
PROMPT="$INSTRUCTIONS"
if [ "$LEGACY" = 1 ]; then
  TEMPLATE=$(cat "$HERE/prompts/legacy_wrapper_sol.txt")
  PROMPT="${TEMPLATE%%"{task}"*}${INSTRUCTIONS}${TEMPLATE#*"{task}"}"
fi
printf '%s' "$PROMPT" > "$ART/prompt.txt"
rm -f "$ART/final_output.txt"
BEFORE="$(docker ps -q 2>/dev/null | tr '\n' ' ')"
T0=$(date +%s)
perl -e 'alarm shift; exec @ARGV' $((CAP_MIN*60)) \
  "$CODEX_BIN" exec --json --skip-git-repo-check -s workspace-write --dangerously-bypass-approvals-and-sandbox \
    ${EXEC_WEB:+} ${EXEC_WEB:---disable web_search} \
    -C "$WORK" --output-last-message "$ART/final_output.txt" "$PROMPT" > "$ART/events.jsonl" 2>"$ART/stderr.log"
RC=$?
# Containers the agent started and left running are task work: the clock stops when they exit.
BG_T0=$(date +%s); MINE=""
for cid in $(docker ps -q 2>/dev/null); do
  case " $BEFORE " in *" $cid "*) ;; *) MINE="$MINE $cid";; esac
done
while [ -n "$MINE" ]; do
  still=""
  for cid in $MINE; do docker ps -q --no-trunc 2>/dev/null | grep -q "^$cid" && still="$still $cid"; done
  MINE="$still"
  if [ -n "$MINE" ]; then sleep 15; fi
done
T1=$(date +%s)
SESSION="$(find "$CODEX_HOME/sessions" -name '*.jsonl' -type f 2>/dev/null | head -1)"
if [ -n "$SESSION" ]; then cp "$SESSION" "$ART/session.jsonl"; fi
( cd "$ART" && tar czf workspace.tar.gz workspace 2>/dev/null && rm -rf workspace )

python3 - "$ART" "$SRC" "$TASK" "$REP" "$VER" "$LEGACY" "$T0" "$BG_T0" "$T1" "$RC" <<'PY'
import json, pathlib, re, sys
art, src, task, rep, ver, legacy, t0, bg_t0, t1, rc = sys.argv[1:]
A = pathlib.Path(art)
TOOL = {"command_execution", "exec_command", "tool_call", "function_call", "local_shell_call", "mcp_tool_call",
        "file_change", "patch_apply", "web_search"}
n_tool = 0
for line in (A / "events.jsonl").read_text(errors="replace").splitlines():
    try:
        event = json.loads(line)
    except ValueError:
        continue
    if isinstance(event, dict) and event.get("type") == "item.completed":
        item = event.get("item") or {}
        n_tool += (item.get("item_type") or item.get("type") or "") in TOOL
out = A / "final_output.txt"
text = out.read_text(errors="replace") if out.exists() else ""
lines = [l for l in text.splitlines() if "final answer" in l.lower()]
answer = re.sub(r"(?i).*FINAL ANSWER:\s*", "", lines[-1])[:200] if lines else ""
answer = answer or text[:200].rstrip("\n")
if not answer and n_tool:  # artifact tasks may end with no text: the workspace is the deliverable
    answer = f"[artifact task: {n_tool} tool calls, deliverable is the workspace]"
# Answered only if codex exited cleanly with output; rc 142 is the SIGALRM of a wall-clock cap.
meta = {"source": src, "task_id": task, "model": "gpt-5.6-sol", "codex_version": ver, "rep": int(rep),
        "legacy_wrapper": legacy == "1", "actual_min": round((int(t1) - int(t0)) / 60, 1),
        "bg_wait_min": round((int(t1) - int(bg_t0)) / 60, 1), "censored": rc == "142",
        "answered": bool(answer.strip()) and rc == "0", "return_code": int(rc), "n_tool_calls": n_tool,
        "final_answer": answer[:300] or None, "started_epoch": int(t0)}
(A / "meta.json").write_text(json.dumps(meta, indent=1))
print(f"run[sol] {src}/{task} r{rep}: actual={meta['actual_min']}min rc={rc} answered={meta['answered']}"
      + (" CENSORED" if meta["censored"] else ""))
PY
