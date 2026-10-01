#!/usr/bin/env python3
"""Run one forecast or one execution row.

  execute.py forecast <task_id> <cell> <text|docs|probe> <row_dir> <home_template>   -> a forecasts.csv row (JSON)
  execute.py execution <task_id> <cell> <row_dir> <home_template>                    -> a runs.csv row (JSON)

Each row starts from a fresh copy of a logged-in CLI home. An execution is staged as an ordinary
ProgramBench arena (./pb, PROMPT.md, tasks/<id>/workspace) and timed with a monotonic stopwatch that
starts just before the CLI is launched and stops when the CLI process exits. Run as a non-root user.
"""
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path

from invocations import CELLS, expand
from questions import extract_task_statement, parse_minutes, prospective_question

HERE = Path(__file__).resolve().parent
PROMPT = HERE / "PROMPT.md"
PB = HERE / "pb"
# Administrative censor: 20 x the slowest run seen before the study (3,173.878 s), fixed in advance.
CENSOR_SECONDS = 63477.564
FORECAST_TIMEOUT = 1800  # forecasts ran under `timeout 1800`


def fresh_home(template, row_dir):
    home = Path(row_dir) / "home"
    shutil.copytree(template, home, symlinks=True)
    return home


def environment(cell, home, row_dir, **extra):
    """Allowlisted host variables, the fresh home, the login token and the runner's TIMEABLATIONS_* row
    variables (verbatim: the run exported these to the CLI too, so the agent could see them)."""
    spec = CELLS[cell]
    env = {name: os.environ[name] for name in spec["env"] if name in os.environ}
    env.update(HOME=str(home), **{spec["home_env"]: str(home)}, TIMEABLATIONS_ROW_HOME=str(home),
               TIMEABLATIONS_ROW_ID=Path(row_dir).name, **extra)
    if cell == "opus-claude":
        env["CLAUDE_CODE_OAUTH_TOKEN"] = Path(os.environ["CLAUDE_TOKEN_FILE"]).read_text().strip()
    return env


def stage(task_id, arena):
    """An arena holding only ./pb, PROMPT.md and this task's workspace, from the pinned image."""
    (arena / "tasks").mkdir(parents=True)
    shutil.copy2(PB, arena / "pb")
    shutil.copy2(PROMPT, arena / "PROMPT.md")
    subprocess.run([str(arena / "pb"), "add", task_id], cwd=arena, check=True, capture_output=True)
    pinned = json.loads((HERE / "tasks.json").read_text())["image_digests"][task_id]
    found = subprocess.run(["docker", "image", "inspect", "--format", "{{json .RepoDigests}}",
                            pinned.split("@")[0] + ":task_cleanroom_v6"],
                           capture_output=True, text=True, check=True).stdout
    if pinned not in json.loads(found):
        raise RuntimeError(f"image digest mismatch for {task_id}")
    (arena / "tasks" / task_id / "IMAGE").write_text(pinned + "\n")
    if {p.name for p in arena.iterdir()} != {"pb", "PROMPT.md", "tasks"}:
        raise RuntimeError("arena is not isolated")
    return arena / "tasks" / task_id / "workspace"


def run_timed(argv, cwd, env, out_dir, censor=None, timeout=None):
    """Launch the CLI in its own process group; return (returncode, wall_seconds, censored)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    limit = censor or timeout
    with open(out_dir / "stdout.txt", "wb") as out, open(out_dir / "stderr.txt", "wb") as err:
        start = time.monotonic()
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=out, stderr=err, start_new_session=True)
        while proc.poll() is None:
            if limit is not None and time.monotonic() - start >= limit:
                os.killpg(proc.pid, 15)
                proc.wait()
                return proc.returncode, time.monotonic() - start, True
            time.sleep(0.02)
        wall = time.monotonic() - start
    # an exit observed at or after the threshold is censored, never completed
    return proc.returncode, wall, censor is not None and wall >= censor


def execution(task_id, cell, row_dir, home_template):
    row_dir = Path(row_dir)
    home = fresh_home(home_template, row_dir)
    arena = row_dir / "arena"
    workspace = stage(task_id, arena)
    argv = expand(CELLS[cell]["run"], {"prompt": PROMPT.read_text(encoding="utf-8")})
    env = environment(cell, home, row_dir, TIMEABLATIONS_ARENA_ROOT=str(arena),
                      TIMEABLATIONS_WORKSPACE=str(workspace), TIMEABLATIONS_TASK_ID=task_id)
    code, wall, censored = run_timed(argv, arena, env, row_dir / "live", censor=CENSOR_SECONDS)
    result = {"task_id": task_id, "cell": cell, "wall_seconds": wall,
              "terminal_state": "admin_censor" if censored else "model_failure" if code else "completed"}
    if result["terminal_state"] == "completed":
        # package the workspace for `programbench eval`; a submission without compile.sh cannot be scored
        subprocess.run([str(arena / "pb"), "submit", task_id, row_dir.name], cwd=arena, check=True,
                       capture_output=True)
        archive = arena / "runs" / row_dir.name / task_id / "submission.tar.gz"
        with tarfile.open(archive) as tar:
            if "compile.sh" not in {Path(n).name for n in tar.getnames()}:
                result["terminal_state"] = "model_failure"
    return result


def probe_wrapper(backend_pb, task_id):
    """The probe surface's ./pb: only `pb probe <args>` is available."""
    return ("#!/bin/sh\nset -eu\n"
            'if [ "$#" -lt 1 ] || [ "$1" != "probe" ]; then\n'
            '  echo "only pb probe is available" >&2\n  exit 2\nfi\nshift\n'
            f'exec {shlex.quote(str(backend_pb))} probe {shlex.quote(task_id)} "$@"\n')


def build_surface(task_id, surface, row_dir):
    """Read-only directory the forecaster starts in: TASK.md (+ docs/) (+ a probe-only ./pb)."""
    root = Path(row_dir) / "surface"
    root.mkdir(parents=True)
    (root / "TASK.md").write_text(extract_task_statement(PROMPT, task_id), encoding="utf-8")
    if surface != "text":
        backend = Path(row_dir) / "probe-backend"
        workspace = stage(task_id, backend)
        shutil.copytree(workspace, root / "docs", ignore=shutil.ignore_patterns(".git"))
        if surface == "docs":
            shutil.rmtree(backend)
        else:  # keep only the backend ./pb and the task image reference
            shutil.rmtree(workspace)
            (backend / "PROMPT.md").unlink()
            (root / "pb").write_text(probe_wrapper(backend / "pb", task_id))
            (root / "pb").chmod(0o555)
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True) + [root]:
        path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
    return root


def forecast(task_id, cell, surface, row_dir, home_template):
    row_dir = Path(row_dir)
    home = fresh_home(home_template, row_dir)
    root = build_surface(task_id, surface, row_dir)
    question = prospective_question(surface, extract_task_statement(PROMPT, task_id))
    env = environment(cell, home, row_dir, PATH=f"{root}{os.pathsep}{os.environ.get('PATH', os.defpath)}",
                      TIMEABLATIONS_SURFACE_ROOT=str(root), TIMEABLATIONS_TASK_ID=task_id)
    # tools stay enabled: the surface directory, not tool availability, bounds the evidence
    code, _, _ = run_timed(expand(CELLS[cell]["run"], {"prompt": question}), root, env, row_dir / "live",
                           timeout=FORECAST_TIMEOUT)
    answer = (row_dir / "live" / "stdout.txt").read_text(errors="replace")
    minutes = parse_minutes(answer) if code == 0 else None
    status = "model_failure" if code else "completed" if minutes else "parse_invalid"
    return {"task_id": task_id, "cell": cell, "surface": surface, "status": status, "minutes": minutes}


if __name__ == "__main__":
    kind, *args = sys.argv[1:]
    print(json.dumps({"forecast": forecast, "execution": execution}[kind](*args)))
