#!/usr/bin/env bash
# Run one agent on ONE ProgramBench task with the same-turn prompt.  Usage: ./run.sh <claude|codex> <instance_id>
# Each agent works in its own arena, ./arena-<agent>, holding ./pb and tasks/<id>/workspace (`pb add` keeps an
# existing workspace, so a shared arena would start the second agent from the first agent's work). Claude's arena
# also held ProgramBench's PROMPT.md and the arena README (claude_arena/, verbatim; copied in as README.md); Codex's
# held neither.
# Quota rule: if the agent did no work because of a rate/usage limit, wait and retry the SAME task.
# The runtime is the wall time of the attempt that did the work (t0..t1 around the CLI only).
set -uo pipefail
agent="$1"; id="$2"
here="$(cd "$(dirname "$0")" && pwd)"
arena="$here/arena-$agent"
STALL="${STALL_SECONDS:-900}"          # wait between rate-limit retries (15 min)
MAXTRY="${MAX_STALL_TRIES:-96}"        # give up after ~24h of stalling
case "$agent" in
  claude) limit='"?429"?|rate.?limit|usage limit|too many requests|quota|exhausted|limit reached' ;;
  codex)  limit='rate.?limit|usage limit|too many requests|"?429"?|quota|exceeded|try again later' ;;
  *) echo "usage: $0 <claude|codex> <instance_id>"; exit 2 ;;
esac
mkdir -p "$arena" "$here/records" "$here/logs"
[ -e "$arena/pb" ] || cp "$here/pb" "$arena/pb"
if [ "$agent" = claude ]; then
  [ -e "$arena/PROMPT.md" ] || cp "$here/claude_arena/PROMPT.md" "$arena/PROMPT.md"
  [ -e "$arena/README.md" ] || cp "$here/claude_arena/ARENA_README.md" "$arena/README.md"
fi
cd "$arena"
log="$here/logs/$id.$agent.log"
rec="$here/records/$id.$agent.json"
[ -f "$rec" ] && { echo "skip $id (done)"; exit 0; }
./pb add "$id" >/dev/null 2>&1 || true
[ -d "tasks/$id/workspace" ] || { echo "no workspace for $id"; exit 0; }

if [ "$agent" = claude ]; then
  prompt="$(sed "s/{{ID}}/$id/g" "$here/prompts/task.txt")"
else  # Codex has no --append-system-prompt, so the rules go first on stdin
  prompt="$(cat "$here/prompts/system.md"; printf '\n\n'; sed "s/{{ID}}/$id/g" "$here/prompts/task.txt")"
fi
run_agent() {
  if [ "$agent" = claude ]; then
    # 10 h bash tool timeout (long compiles); the CLI login is used, not an API key
    printf '%s' "$prompt" | BASH_MAX_TIMEOUT_MS="${BASH_MAX_TIMEOUT_MS:-36000000}" ANTHROPIC_API_KEY="" \
      claude --print --model "${MODEL:-claude-opus-4-8}" \
      --append-system-prompt "$(cat "$here/prompts/system.md")" \
      --disallowedTools "WebFetch,WebSearch" \
      --dangerously-skip-permissions \
      --effort "${EFFORT:-max}" \
      --output-format stream-json --verbose
  else
    printf '%s' "$prompt" | codex exec --json \
      --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check \
      -m "${MODEL:-gpt-5.5}" -c model_reasoning_effort="${EFFORT:-xhigh}"
  fi
}

attempt=0; ran=0
while :; do
  attempt=$((attempt+1))
  t0=$(python3 -c 'import time;print(time.time())')
  run_agent >"$log" 2>&1
  rc=$?
  if grep -q 'ESTIMATE_MINUTES' "$log"; then ran=1; break; fi   # it ran: it printed its first-line forecast
  if grep -qiE "$limit" "$log" && [ "$attempt" -lt "$MAXTRY" ]; then
    cp "$log" "$here/logs/$id.$agent.ratelimit.$attempt.log" 2>/dev/null || true
    echo "[$id] rate-limited, no work (try $attempt); stalling ${STALL}s"
    sleep "$STALL"; continue
  fi
  echo "[$id] giving up (try $attempt, rc=$rc)"; break
done
[ "$ran" -eq 1 ] || exit "${rc:-1}"

t1=$(python3 -c 'import time;print(time.time())')
dur=$(python3 -c "print($t1-$t0)")
./pb submit "$id" "${agent}_run" >/dev/null 2>&1 || true
if [ "$agent" = codex ] && [ ! -f "runs/codex_run/$id/submission.tar.gz" ]; then  # only the Codex runner checked
  echo "[$id] submit failed; not recording"; exit 1
fi
python3 "$here/extract.py" record "$agent" "$id" "$dur" "$log" > "$rec.tmp" && mv "$rec.tmp" "$rec"
echo "done $id (${dur%.*}s, try=$attempt, rc=$rc)"
