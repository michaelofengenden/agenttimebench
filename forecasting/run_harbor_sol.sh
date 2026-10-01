#!/bin/bash
# No-request GPT-5.6 Sol runs on Harbor tasks (TUA-Bench, Frontier-Bench): one trial per task directory.
#   run_harbor_sol.sh <task_dir>...      (env: HARBOR, TRIALS; Codex subscription auth from ~/.codex/auth.json)
# Runtime is Harbor's agent-execution interval; environment setup and verification are outside it.
# Appends one JSON line per trial to $TRIALS/runtimes.jsonl.
# umask 000: Harbor creates trial dirs under this umask, and the container's non-root agent writes its logs there.
umask 000
set -uo pipefail
HARBOR="${HARBOR:-harbor}"
TRIALS="${TRIALS:-trials}"
AGENT_TIMEOUT="${AGENT_TIMEOUT:-86400}"   # a 24 h runaway guard, far above the native limits: no task cap
SETUP_TIMEOUT="${SETUP_TIMEOUT:-3600}"
export CODEX_FORCE_AUTH_JSON=1
mkdir -p "$TRIALS"; chmod 777 "$TRIALS"

for t in "$@"; do
  # Frontier-Bench instructions end "You have N seconds to complete this task."; that sentence and the whitespace
  # after it are removed from instruction.md in place (the other tasks do not contain it), then checked.
  python3 - "$t/instruction.md" <<'PY'
import re, sys
CAP = re.compile(r'You have\s+[\d,]+\s*seconds?\s+to complete this task\.\s*')
text = open(sys.argv[1], errors="replace").read()
if CAP.search(text):
    open(sys.argv[1], "w").write(CAP.sub('', text))
PY
  if grep -qE 'You have +[0-9,]+ *seconds? +to complete this task' "$t/instruction.md"; then
    echo "FATAL: $t/instruction.md still states a time cap" >&2; exit 92
  fi
  "$HARBOR" trial start \
    -p "$t" \
    --trials-dir "$TRIALS" \
    -a codex -m openai/gpt-5.6-sol \
    --agent-kwarg reasoning_effort=xhigh \
    --agent-timeout "$AGENT_TIMEOUT" \
    --agent-setup-timeout "$SETUP_TIMEOUT" \
    --environment-build-timeout-multiplier 3.0
done

python3 - "$TRIALS" "$@" <<'PY' >> "$TRIALS/runtimes.jsonl"
import datetime, glob, json, os, sys
trials, tasks = sys.argv[1], [os.path.basename(t.rstrip("/")) for t in sys.argv[2:]]
stamp = lambda s: datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
for task in tasks:
    for result in sorted(glob.glob(os.path.join(trials, f"{task}__*", "result.json"))):
        r = json.load(open(result))
        secs, execution = r.get("agent_execution_time_sec"), r.get("agent_execution") or {}
        if secs is None and execution.get("started_at") and execution.get("finished_at"):
            secs = (stamp(execution["finished_at"]) - stamp(execution["started_at"])).total_seconds()
        exc = (r.get("exception_info") or {}).get("exception_type")
        print(json.dumps({"task_id": task, "trial": os.path.basename(os.path.dirname(result)),
                          "actual_min": None if secs is None else round(float(secs) / 60, 1),
                          "censored": bool(r.get("timed_out") or r.get("agent_timed_out") or "Timeout" in str(exc)),
                          "exception_type": exc}))
PY
