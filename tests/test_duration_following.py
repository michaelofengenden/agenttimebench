"""duration_following: metric definitions and the analysis CLI on small synthetic inputs."""
import csv
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1] / "duration_following"
NAMES = ("metrics", "analyze", "figures")


def _load():
    """Import the experiment's modules without clashing with other experiments' modules of the same name."""
    saved = {k: sys.modules.pop(k) for k in NAMES if k in sys.modules}
    sys.path.insert(0, str(HERE))
    try:
        import analyze
        import figures
        import metrics
    finally:
        sys.path.remove(str(HERE))
        for k in NAMES:
            sys.modules.pop(k, None)
        sys.modules.update(saved)
    return metrics, analyze, figures


M, A, F = _load()
FABLE, SOL, ASTRA = "claude-fable-5-1", "gpt-5.6-sol", "gpt-6-astra"
SHORT = {FABLE: "fable", SOL: "sol", ASTRA: "astra"}


def run(b, t, req, worked, **kw):
    return {"benchmark": b, "task": t, "requested_s": req, "worked_s": worked, **kw}


def test_on_time_band_is_inclusive():
    rs = [run("x", "a", 100, 80), run("x", "b", 100, 125), run("x", "c", 100, 79.9), run("x", "d", 100, 125.1)]
    assert M.shares(rs) == (0.5, 0.25, 0.25)


def test_deviation_weighs_benchmarks_equally():
    rs = [run("big", str(i), 100, 200) for i in range(9)] + [run("small", "0", 100, 100)]
    assert M.deviation(rs) == pytest.approx(2 ** 0.5)  # exp((ln 2 + 0) / 2), not the per-run mean


def test_within_task_slope_removes_task_level():
    # Every task runs exactly as long as asked, but harder tasks got longer requests and ran 3x longer:
    # the within-task slope is 1 while the pooled slope is not.
    rs = [run("x", t, r * k, r * k * (3 if k > 1 else 1)) for k, t in ((1, "easy"), (10, "hard")) for r in (1, 4, 16)]
    assert M.within_slope(rs) == pytest.approx(1.0)
    assert M.pooled_slope(rs) > 1.05


def test_bootstrap_is_seeded():
    rs = [run("x", str(i % 7), 10 * (i % 3 + 1), 10 + i) for i in range(21)]
    assert M.task_bootstrap(rs, M.deviation, 50) == M.task_bootstrap(rs, M.deviation, 50)


def scored(b, t, raws, perf=None, agent=FABLE):
    """Scored shortest/middle/longest runs of one task (middle omitted when raws has two values)."""
    reqs = A.ARMS if len(raws) == 3 else ("shortest", "longest")
    return [{"agent": agent, "benchmark": b, "task": t, "request": k, "scored": True, "score_raw": x,
             "score": perf[i] if perf else x} for i, (k, x) in enumerate(zip(reqs, raws))]


def test_score_change_rules():
    rs = (scored("gpqa-diamond", "q1", [0, 1]) + scored("gpqa-diamond", "q2", [1, 1])     # binary: exact
          + scored("assistantbench", "a1", [0.5, 0.505]) + scored("assistantbench", "a2", [0.5, 0.52])  # 0.01 tie
          + scored("yc-bench", "y1", [1_000_000, 1_000_001])                               # raw funds: exact
          + scored("sakana-ale-bench", "min", [300, 200, 100], perf=[1000, 2000, 3000])    # lower raw is better
          + scored("sakana-ale-bench", "max", [100, 200, 50], perf=[1000, 2000, 500])
          + scored("appworld", "w1", [0, 1])[:1])                                           # no longest run: skipped
    c = A.score_change(rs)
    assert (c[FABLE]["higher"], c[FABLE]["same"], c[FABLE]["lower"]) == (4, 2, 1)
    assert c["minimized_ale_tasks"] == ["min"] and c[SOL]["tasks"] == 0


def test_cutoff_is_twice_the_tasks_longest_request():
    rs = [{"agent": FABLE, "benchmark": "x", "task": "a", "requested_s": 60, "worked_s": 1920},     # 2 x 960
          {"agent": SOL, "benchmark": "x", "task": "a", "requested_s": 960, "worked_s": 1919.9},
          {"agent": ASTRA, "benchmark": "x", "task": "a", "requested_s": 240, "worked_s": 2000},
          {"agent": ASTRA, "benchmark": "x", "task": "b", "requested_s": 60, "worked_s": 119}]
    assert A.cutoff_counts(rs) == {FABLE: 1, SOL: 0, ASTRA: 1}


# ---- the CLI on synthetic input files ----------------------------------------------------------------

REQUESTS = {"shortest": 60, "middle": 240, "longest": 960}
FACTOR = {FABLE: {"shortest": 2.0, "middle": 1.0, "longest": 0.25},   # worked / requested
          SOL: {"shortest": 1.5, "middle": 1.0, "longest": 1.0},
          ASTRA: {"shortest": 1.3, "middle": 1.0, "longest": 1.0}}
RAW = {("gpqa-diamond", FABLE): {"shortest": 0.0, "middle": 1.0, "longest": 1.0},
       ("gpqa-diamond", SOL): {"shortest": 1.0, "middle": 1.0, "longest": 1.0},
       ("gpqa-diamond", ASTRA): {"shortest": 1.0, "middle": 1.0, "longest": 1.0},
       ("assistantbench", FABLE): {"shortest": 0.5, "middle": 0.5, "longest": 0.5},
       ("assistantbench", SOL): {"shortest": 0.5, "middle": 0.5, "longest": 0.505},
       ("assistantbench", ASTRA): {"shortest": 0.2, "middle": 0.5, "longest": 0.8}}
LABELS = {("assistantbench", "t1", "shortest", FABLE): "WORKED_THROUGH",   # late by the clock, whatever the label
          ("assistantbench", "t1", "middle", FABLE): "FINISHED_THEN_RECHECKED",
          ("assistantbench", "t1", "middle", SOL): "FINISHED_THEN_SLEPT",
          ("assistantbench", "t1", "middle", ASTRA): "MIXED_OR_UNCLEAR",
          ("assistantbench", "t2", "middle", ASTRA): "",
          ("assistantbench", "t1", "longest", ASTRA): "WAITED_ON_JOB"}


def write_csv(path, rows):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return str(path)


@pytest.fixture
def inputs(tmp_path):
    """63 runs: 3 agents x 7 tasks x 3 requests. PaperBench is ungraded, as is one Astra GPQA run; one Fable run left
    no output."""
    runs, scores, labels = [], [], []
    for b, tasks in (("gpqa-diamond", ("t1", "t2", "t3")), ("assistantbench", ("t1", "t2", "t3")), ("paperbench", ("p1",))):
        for t in tasks:
            for a in (FABLE, SOL, ASTRA):
                for k, req in REQUESTS.items():
                    rid = f"{SHORT[a]}-{b[:4]}-{t}-{k}"
                    runs.append({"run_id": rid, "agent": a, "benchmark": b, "task": t, "request": k,
                                 "requested_s": req, "worked_s": req * FACTOR[a][k]})
                    raw, no_output = RAW.get((b, a), {}).get(k), False
                    if (b, t, k, a) == ("gpqa-diamond", "t3", "middle", ASTRA):
                        raw = None
                    if (b, t, k, a) == ("assistantbench", "t3", "middle", FABLE):
                        raw, no_output = 0.0, True
                    scores.append({"run_id": rid, "score": "" if raw is None else 100 * raw,
                                   "score_raw": "" if raw is None else raw, "no_output": str(no_output).lower()})
                    if (b, t, k, a) in LABELS:
                        labels.append({"run_id": rid, "label": LABELS[(b, t, k, a)]})
    scores.reverse()  # SCORES has its own row order
    return (write_csv(tmp_path / "runs.csv", runs), write_csv(tmp_path / "scores.csv", scores),
            write_csv(tmp_path / "labels.csv", labels))


def test_load_scores_keeps_its_own_order_and_joins_runs(inputs):
    runs = A.load_runs(inputs[0])
    scores = A.load_scores(inputs[1], runs)
    assert [s["run_id"] for s in scores] == [r["run_id"] for r in reversed(runs)]
    s = next(s for s in scores if s["run_id"] == "astra-gpqa-t3-middle")
    assert (s["agent"], s["worked_s"], s["scored"], s["score"], s["score_raw"]) == (ASTRA, 240.0, False, None, None)


def test_analyze_cli(inputs, tmp_path, capsys):
    out = tmp_path / "numbers.json"
    A.main([*inputs, "--json", str(out)])
    assert "Table 3 (upper block): 63 runs" in capsys.readouterr().out
    n = json.loads(out.read_text())
    t = n["duration_following"]
    assert [round(x, 6) for x in (t[FABLE]["on_time"], t[FABLE]["early"], t[FABLE]["late"])] == [0.333333] * 3
    assert t[FABLE]["deviation"] == pytest.approx(2.0) and t[FABLE]["deviation_ci"] == pytest.approx([2.0, 2.0])
    assert t[SOL]["deviation"] == pytest.approx(1.5 ** (1 / 3)) and t[ASTRA]["late"] == pytest.approx(1 / 3)
    assert n["details"]["cutoff"] == {FABLE: 0, SOL: 0, ASTRA: 0}
    assert n["details"]["astra_misses"] == {"n": 7, "late": 7, "early": 0, "late_shortest": 7,
                                            "median_late_shortest": pytest.approx(1.3)}
    b = n["transcripts"]
    assert (b[FABLE]["n"], b[FABLE]["counts"]["late"], b[FABLE]["counts"]["rechecked"]) == (2, 1, 1)
    assert b[SOL]["on_time_agentic"] == {"working": 0, "rechecked": 0, "slept": 1, "unclear": 0}
    assert (b[ASTRA]["n"], b[ASTRA]["unclear"], b[ASTRA]["unread"], b[ASTRA]["counts"]["working"]) == (1, 1, 1, 1)
    c = n["score_change"]
    assert [(c[a]["tasks"], c[a]["higher"], c[a]["same"], c[a]["lower"]) for a in (FABLE, SOL, ASTRA)] == [
        (6, 3, 3, 0), (6, 0, 6, 0), (6, 3, 3, 0)]
    s = n["native_scores"]
    assert s["benchmarks"]["gpqa-diamond"][ASTRA] == {"mean": 100.0, "scored": 8, "runs": 9}
    assert s["benchmarks"]["assistantbench"][FABLE]["mean"] == pytest.approx(round(100 * 4 / 9, 1))
    assert (s["suite_benchmarks"], s["suite_cells"]) == (["assistantbench", "gpqa-diamond"], 17)
    assert s["suite"][SOL] == {"mean": 75.1, "ci": [75.1, 75.1]}  # (100.0 + 50.2) / 2, every task alike
    assert s["benchmarks"]["paperbench"][FABLE] == {"mean": None, "scored": 0, "runs": 3}
    assert s["graded"] == {"runs": 63, "scored": 53, "no_output": 1, "no_output_by_benchmark": {"assistantbench": 1}}


def test_suite_needs_all_three_agents_and_three_cells():
    cells = [{"agent": a, "benchmark": b, "task": t, "request": "shortest", "scored": True, "no_output": False,
              "score": 10.0 * i, "score_raw": 0.1 * i}
             for i, a in enumerate((FABLE, SOL, ASTRA), 1) for b, ts in (("x", "abc"), ("y", "ab"), ("yc-bench", "abc"))
             for t in ts]
    cells += [{**cells[0], "task": "d", "agent": FABLE}]  # scored for one agent only: not a matched cell
    s = A.native_scores(cells)
    assert s["suite_benchmarks"] == ["x"] and s["suite_cells"] == 3
    assert [s["suite"][a]["mean"] for a in (FABLE, SOL, ASTRA)] == [10.0, 20.0, 30.0]


def test_figures_cli_writes_three_pdfs(inputs, tmp_path):
    F.main([*inputs, "--out", str(tmp_path / "figs")])
    pdfs = sorted(p.name for p in (tmp_path / "figs").glob("*.pdf"))
    assert pdfs == ["agenttime_duration_following_hero_equal_axes.pdf", "agenttime_duration_following_three_agents.pdf",
                    "duration_following_breakdown.pdf"]
    assert all((tmp_path / "figs" / p).stat().st_size > 1000 for p in pdfs)
