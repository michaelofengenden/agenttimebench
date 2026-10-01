"""Referent ablation: prompt bytes, anchor stripping, design, runners and analysis rules (synthetic data only)."""
import hashlib
import importlib
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

DIR = Path(__file__).resolve().parents[1] / "preliminary" / "referent_ablation"


def _import(*names):
    """Import this folder's modules without leaving their generic names in sys.modules."""
    saved = {n: sys.modules.pop(n) for n in names if n in sys.modules}
    sys.path.insert(0, str(DIR))
    try:
        return [importlib.import_module(n) for n in names]
    finally:
        sys.path.remove(str(DIR))
        for n in names:
            sys.modules.pop(n, None)
        sys.modules.update(saved)


strip, design, analyze = _import("strip_anchors", "design", "analyze")
PROMPTS = json.loads((DIR / "prompts.json").read_text(encoding="utf-8"))
TASKS = design.load_tasks()
SUB50 = design.subset50(TASKS)
IN, OUT = SUB50[0][:2], next(t[:2] for t in TASKS if t not in SUB50)
# sha256 of each arm prefix as sent in every session
ARM_SHA256 = {
    "you": "882f3837b1964ba7010e7339b7b850ef89347b57b33a3fda6e031ca3d708999b",
    "human": "cdb94efeea24117f22d37c98880c476d3ace40a45db4632b407818c3504a4b0f",
    "frontier": "b5f0e021551372527bf470aaca30abafadd5bd8bb7be74d78c7fcc337542217b",
    "placebo": "ed7e6cff55c9124e132aeb9f856c029d5d4564425a1ba3043fc8e87140776105",
    "expert": "31ddd2554198632fe3eb344cbc00b31f8283e71ddac68a69fadfe210d9e51ded",
}
# sha256 of the session orders that were run, one JSON object per line (as design.py prints them)
ORDER_SHA256 = {
    "main": "b0724136855ae7c456d5134b2a94bfdc8e27eee3a103167624f189cbe4614682",
    "ext": "b19e6f22a7cc72b4902c5afcc9e7a60d7272ceb8dee5af0d659fda18f5faac24",
    "expert": "29c6dadbb0cf71fb7a7e1d94887b006e7a46f9edf21d1a1507388bf9244975c8",
}


def test_prompt_bytes():
    assert {a: hashlib.sha256(t.encode()).hexdigest() for a, t in PROMPTS["arms"].items()} == ARM_SHA256
    assert (PROMPTS["fence_prefix"], PROMPTS["fence_suffix"]) == ('\n\nThe task:\n"""\n', '\n"""')
    for (a, b), (x, y) in zip([("you", "human"), ("you", "frontier"), ("you", "placebo"), ("human", "expert")],
                              PROMPTS["referent_substrings"].values()):
        assert PROMPTS["arms"][a].replace(x, y, 1) == PROMPTS["arms"][b]


def test_strip_anchors():
    text = "Reproduce the figures with reproduce.sh, for a maximum runtime of 7 days.\n\n\n\nGood luck."
    assert strip.strip_anchors(text, "PaperBench") == "Reproduce the figures with reproduce.sh.\n\nGood luck."
    text = "Train the model.\nSubmit answer.json at least every ~2 hours.\nReport the loss."
    assert strip.strip_anchors(text, "METR RE-Bench") == "Train the model.\n\nReport the loss."
    budget = "Fix the bug.\n3. You have a maximum of 8 hours. Plan accordingly.\n"
    assert strip.strip_anchors(budget, "DeepSWE") == "Fix the bug.\n\n"
    assert strip.strip_anchors(budget, "Agents Last Exam") == budget
    assert strip.safe_id("Humanity's Last Exam", "a:b/c") == "Humanity_s_Last_Exam__a_b_c"


def test_design_reproduces_session_orders():
    assert len(TASKS) == 232 and len({t[0] for t in TASKS}) == 18 and len(SUB50) == 50
    for batch, n in (("main", 2256), ("ext", 1456), ("expert", 400)):
        order = design.session_order(batch, TASKS)
        assert len(order) == n
        assert hashlib.sha256("\n".join(json.dumps(s) for s in order).encode()).hexdigest() == ORDER_SHA256[batch]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_parser_regex_stage():
    script = ("const {parseEstimateRegex: p} = await import(process.argv[1]);"
              "console.log(JSON.stringify(JSON.parse(process.argv[2]).map((t) => [p(t).outcome, p(t).estimate_min])))")
    cases = ["MINUTES=5\nREASON=x", "  MINUTES = 12.75  \nREASON=y", "MINUTES=2.5 and REASON=y",
             "MINUTES=.5", "MINUTES=0", "MINUTES=3\nMINUTES=4"]
    out = subprocess.run(["node", "--input-type=module", "-e", script, (DIR / "parse_estimate.mjs").as_uri(),
                          json.dumps(cases)], capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == [["ok", 5], ["ok", 12.75], ["parse_fail", None], ["parse_fail", None],
                               ["parse_fail", None], ["parse_fail", None]]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_codex_runner_with_fake_cli(tmp_path):
    fake = tmp_path / "codex"
    fake.write_text('#!/bin/bash\n[ "$1" = --version ] && { echo "codex-cli 0.146.0"; exit 0; }\n'
                    'echo "$@" > "$(dirname "$0")/argv"; cat > "$(dirname "$0")/stdin"\n'
                    'printf "MINUTES=12.5\\nREASON=Write and test it.\\n"; exit "${FAKE_RC:-0}"\n')
    fake.chmod(0o755)
    (tmp_path / "task.txt").write_text("Compute the thing.")
    env = {**os.environ, "CODEX_BIN": str(fake), "HOME": str(tmp_path), "TMPDIR": str(tmp_path)}
    run = lambda rep, rc: subprocess.run([sys.executable, "-B", DIR / "elicit_codex.py", tmp_path / "task.txt", "Demo",
                                          "t/1", tmp_path / "out", "human", str(rep)], env={**env, "FAKE_RC": rc})
    assert run(1, "0").returncode == 0
    rec = json.loads((tmp_path / "out" / "Demo__t_1.human.sol.r1.a1.json").read_text())
    prompt = (PROMPTS["arms"]["human"] + PROMPTS["fence_prefix"] + "Compute the thing." + PROMPTS["fence_suffix"]).encode()
    assert (tmp_path / "stdin").read_bytes() == prompt and rec["prompt_sha256"] == hashlib.sha256(prompt).hexdigest()
    assert (rec["outcome"], rec["estimate_min"], rec["reason_text"]) == ("ok", 12.5, "Write and test it.")
    assert (tmp_path / "argv").read_text().split()[:6] == ["exec", "--ephemeral", "--skip-git-repo-check", "-s",
                                                             "read-only", "--ignore-user-config"]
    assert run(2, "1").returncode == 4
    assert json.loads((tmp_path / "out" / "Demo__t_1.human.sol.r2.a1.json").read_text())["outcome"] == "infra_fail"


def test_retry_runs_only_infra_failures(tmp_path):
    run = _import("strip_anchors", "design", "run")[-1]
    write = lambda name, outcome: (tmp_path / name).write_text(json.dumps({"outcome": outcome}))
    sol, fable = ({"safe_id": "X__1", "arm": "you", "subject": s, "rep": 1} for s in ("sol", "fable"))
    assert run.pending(sol, tmp_path, 1) and not run.pending(sol, tmp_path, 2)
    write("X__1.you.sol.r1.a1.json", "infra_fail")
    write("X__1.you.fable.r1.a1.json", "refusal")
    assert not run.pending(sol, tmp_path, 1) and run.pending(sol, tmp_path, 2) and not run.pending(fable, tmp_path, 2)
    write("X__1.you.sol.r1.a2.json", "ok")
    assert not run.pending(sol, tmp_path, 2) and not run.pending(sol, tmp_path, 3)


def receipt(subject, arm, rep, task, estimate, outcome="ok", attempt=1):
    return {"source": task[0], "task_id": task[1], "subject": subject, "arm": arm, "rep": rep, "attempt": attempt,
            "outcome": outcome, "estimate_min": estimate if outcome == "ok" else None}


def write_receipts(folder, rows):
    folder.mkdir()
    for r in rows:
        name = f"{strip.safe_id(r['source'], r['task_id'])}.{r['arm']}.{r['subject']}.r{r['rep']}.a{r['attempt']}.json"
        (folder / name).write_text(json.dumps(r))
    return analyze.latest_ok(analyze.load_receipts(folder))


def test_contrast_rules(tmp_path):
    records = write_receipts(tmp_path / "r", [
        receipt("fable", "you", 1, IN, 10), receipt("fable", "you", 2, IN, 40),          # task value 20
        receipt("fable", "human", 1, IN, 100), receipt("fable", "frontier", 1, IN, 25),
        receipt("fable", "placebo", 1, IN, 40),
        receipt("fable", "expert", 1, IN, 50), receipt("fable", "human", 5, IN, 100),   # expert batch only
        receipt("fable", "you", 1, OUT, 5), receipt("fable", "you", 2, OUT, None, "refusal"),
        receipt("fable", "human", 1, OUT, None, "infra_fail"), receipt("fable", "human", 1, OUT, 20, attempt=2),
        receipt("fable", "frontier", 1, OUT, 8), receipt("fable", "you", 3, OUT, 4),    # extension batch
    ])
    c = analyze.contrasts(records, "fable")
    assert c["you/human"] == pytest.approx((math.sqrt(0.2 * 0.25), 2))
    assert c["you/placebo (finish)"] == pytest.approx((0.5, 1))
    assert c["you/frontier"] == pytest.approx((math.sqrt(0.8 * 0.5), 2))
    assert c["expert/professional"] == pytest.approx((0.5, 1))
    assert math.isnan(analyze.contrasts(records, "sol")["you/human"][0])


def test_descriptive_rules(tmp_path):
    tasks = [t[:2] for t in TASKS[:4]]
    humans, yous = (1, 100, 1e4, 1e6), (1, 10, 100, 1000)
    records = write_receipts(tmp_path / "r", [receipt("sol", arm, 1, t, v) for t, h, y in zip(tasks, humans, yous)
                                              for arm, v in (("human", h), ("you", y))])
    assert analyze.arm_geomeans(records)["sol", "you"] == pytest.approx((10 ** 1.5, 4))
    slope, bands = analyze.length_dependence(analyze.scatter_points(records), "sol")
    assert slope == pytest.approx(-0.5)
    assert [x for r, n in bands.values() for x in (r, n)] == pytest.approx([1, 1, 0.1, 1, 10 ** -2.5, 2])
    actuals = tmp_path / "actuals.csv"
    actuals.write_text("source,task_id,subject,actual_min\n"
                       + "".join(f'"{s}","{t}",sol,{a}\n' for (s, t), a in zip(tasks, (2, 20, 50, 4000))))
    q = analyze.quartiles(records, actuals)
    assert [r["q"] for r in q] == ["Q1", "Q2", "Q3", "Q4"]
    assert [x for r in q for x in (r["n"], r["median_actual"], r["ratio"])] == pytest.approx(
        [1, 2, 2, 1, 20, 2, 1, 50, 0.5, 1, 4000, 4])
    codes = tmp_path / "codes.jsonl"
    flags = lambda h: {"explicit_human_referent": h, "explicit_capability_claim": False, "work_step_narration": True}
    codes.write_text("\n".join(json.dumps({"key": k, **v}) for k, v in (
        ("X__a.b.you.fable.r1.a1.json", flags(True)), ("Y.you.fable.r2.a1.json", flags(False)),
        ("Y.human.fable.r1.a1.json", {"coder_error": "timeout"}))))
    assert analyze.rationale_cues(codes) == {("fable", "you"): {
        "n": 2, "explicit_human_referent": 50, "explicit_capability_claim": 0, "work_step_narration": 100}}


def test_analyze_and_figures_run_end_to_end(tmp_path, capsys):
    pytest.importorskip("matplotlib")
    figures = _import("strip_anchors", "design", "analyze", "figures")[-1]
    chosen = SUB50[:8] + [t for t in TASKS if t not in SUB50][:12] + [t for t in TASKS if t[0] == "OSWorld 2.0"][:2]
    rows = []
    for i, t in enumerate(chosen):
        cells = [("you", 1), ("human", 1), ("frontier", 1)]
        cells += [("placebo", 1), ("expert", 1), ("human", 5)] if t in SUB50 else [("you", 3)]
        rows += [receipt(s, arm, rep, t[:2], (i + 1) * (10 if arm in ("human", "expert") else 1 + k))
                 for k, s in enumerate(analyze.SUBJECTS) for arm, rep in cells]
    write_receipts(tmp_path / "receipts", rows)
    (tmp_path / "codes.jsonl").write_text("\n".join(json.dumps({"key": f"T.{arm}.fable.r1.a1.json",
        "explicit_human_referent": True, "explicit_capability_claim": False, "work_step_narration": True})
        for arm in ("you", "frontier", "human")))
    (tmp_path / "actuals.csv").write_text("source,task_id,subject,actual_min\n" + "".join(
        f'"{t[0]}","{t[1]}",{s},{i + 1}\n' for i, t in enumerate(chosen) for s in analyze.SUBJECTS))
    args = [tmp_path / "receipts", tmp_path / "codes.jsonl", tmp_path / "actuals.csv"]
    analyze.main(*args)
    assert f"{len(rows)} first-attempt sessions" in capsys.readouterr().out
    figures.main(*args, tmp_path / "figures")
    assert len(list((tmp_path / "figures").glob("*.pdf"))) == 4
