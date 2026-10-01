#!/bin/bash
# GPT-5.6 Sol forecast: one fresh, ephemeral, read-only `codex exec` session (Codex CLI >= 0.145.0).
#   forecast_sol.sh <instructions_file> <source> <task_id> <out_dir>
# Writes <out_dir>/<source>__<task_id>.sol.json once and never overwrites it. ANTHROPIC_API_KEY is needed for the
# fallback parser in parse_estimate.mjs.
set -euo pipefail
if [ "$#" -ne 4 ]; then
  echo "usage: forecast_sol.sh <instructions_file> <source> <task_id> <out_dir>" >&2; exit 2
fi
INSTR="$1"; SRC="$2"; TASK="$3"; OUT="$4"
HERE="$(cd "$(dirname "$0")" && pwd -P)"
CODEX_BIN="${CODEX_BIN:-codex}"
mkdir -p "$OUT"
REC="$OUT/$(printf '%s__%s' "$SRC" "$TASK" | sed 's/[^A-Za-z0-9._-]/_/g').sol.json"
if [ -f "$REC" ]; then echo "skip (exists) $REC"; exit 0; fi
if [ -s "$HOME/.codex/AGENTS.md" ]; then echo "FATAL: ~/.codex/AGENTS.md is not empty" >&2; exit 92; fi
VER="$("$CODEX_BIN" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)"
if ! python3 -c 'import sys; v = lambda s: tuple(map(int, s.split("."))); sys.exit(v(sys.argv[1]) < v("0.145.0"))' \
     "${VER:-0.0.0}"; then
  echo "FATAL: codex ${VER:-?} is older than 0.145.0" >&2; exit 91
fi

# Clean room: a fresh empty directory under TMPDIR, outside this repository, with no CLAUDE.md or
# AGENTS.md anywhere on its path to the root.
TMPBASE="$(cd "${TMPDIR:-/tmp}" && pwd -P)"
SCRATCH="$(mktemp -d "$TMPBASE/forecast-sol.XXXXXX")"
CAPTURE="$(mktemp -d "$TMPBASE/forecast-sol-capture.XXXXXX")"
trap 'rm -rf "$SCRATCH" "$CAPTURE"' EXIT
case "$SCRATCH/" in "$(cd "$HERE/.." && pwd -P)"/*) echo "FATAL: clean room is inside the repository" >&2; exit 92;; esac
dir="$SCRATCH"
while :; do
  for name in CLAUDE.md AGENTS.md; do
    if [ -e "$dir/$name" ]; then echo "FATAL: $dir/$name on clean-room walk-up" >&2; exit 92; fi
  done
  if [ "$dir" = / ]; then break; fi
  dir="$(dirname "$dir")"
done

# Built from bytes, so trailing newlines in the instruction file stay in the prompt (as for Fable).
python3 - "$HERE/prompts/forecast.txt" "$INSTR" "$CAPTURE/prompt.txt" <<'PY'
import pathlib, sys
prefix, suffix = pathlib.Path(sys.argv[1]).read_bytes().split(b"{task}")
task = pathlib.Path(sys.argv[2]).read_bytes()
task.decode("utf-8")  # strict: refuse non-UTF-8 instructions
pathlib.Path(sys.argv[3]).write_bytes(prefix + task + suffix)
PY

set +e
"$CODEX_BIN" exec --ephemeral --skip-git-repo-check -s read-only \
  --ignore-user-config -C "$SCRATCH" \
  -c model=gpt-5.6-sol \
  -c model_reasoning_effort=xhigh \
  -c personality=none \
  - < "$CAPTURE/prompt.txt" > "$CAPTURE/stdout.txt" 2> "$CAPTURE/stderr.txt"
CLI_RC=$?
PARSER_RC=0
if [ "$CLI_RC" -eq 0 ] && grep -q '[^[:space:]]' "$CAPTURE/stdout.txt"; then
  node "$HERE/parse_estimate.mjs" < "$CAPTURE/stdout.txt" > "$CAPTURE/parser.json"
  PARSER_RC=$?
fi

python3 - "$REC" "$SRC" "$TASK" "$INSTR" "$VER" "$CLI_RC" "$PARSER_RC" "$CAPTURE" <<'PY'
import datetime, hashlib, json, os, pathlib, sys
rec, src, task, instr, ver, cli_rc, parser_rc, cap = sys.argv[1:]
stdout = pathlib.Path(cap, "stdout.txt").read_bytes()
try:
    parsed = json.loads(pathlib.Path(cap, "parser.json").read_text())
except (OSError, ValueError):
    parsed = {}
estimate = parsed.get("estimate_min")
if int(cli_rc) != 0:
    outcome, error = "infra_fail", f"codex exited {cli_rc}"
elif not stdout.strip():
    outcome, error = "infra_fail", "empty reply"
elif int(parser_rc) != 0 or parsed.get("outcome") not in ("ok", "parse_fail"):
    outcome, error = "infra_fail", f"parser exited {parser_rc}"
elif parsed["outcome"] == "ok" and not (type(estimate) is int and estimate > 0):
    outcome, error = "infra_fail", "parser returned an invalid positive integer"
else:
    outcome, error = parsed["outcome"], parsed.get("parser_error")
record = {
    "source": src, "task_id": task, "model": "gpt-5.6-sol", "effort": "xhigh", "codex_version": ver,
    "outcome": outcome, "estimate_min": estimate if outcome == "ok" else None,
    "parse_method": parsed.get("parse_method", "none"), "raw_text": stdout.decode("utf-8", "replace"),
    "raw_stderr": pathlib.Path(cap, "stderr.txt").read_text(errors="replace"), "exit_code": int(cli_rc),
    "instructions_sha256_16": hashlib.sha256(pathlib.Path(instr).read_bytes()).hexdigest()[:16],
    "ts_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "error": error,
}
with os.fdopen(os.open(rec, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as fh:
    json.dump(record, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
print(f"forecast[sol] {src}/{task}: {outcome}" + (f" {estimate}min" if outcome == "ok" else ""))
sys.exit({"ok": 0, "parse_fail": 3}.get(outcome, 4))
PY
