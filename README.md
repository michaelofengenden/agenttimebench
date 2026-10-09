# AgentTime v1.1

Measure how long agents work naturally, then how they respond when asked to work for less or more time.

**The unit is one agent: 200 tasks × 3 runs = 600 planned runs.** Add as many agents as needed. The total follows from the roster; it is not a fixed study size.

| Run for each task | What the agent receives | When |
| --- | --- | --- |
| Natural, or M | Task only, with no duration request or experiment time cap | First |
| Short | The task plus a request to work for one quarter of the eventual average natural runtime | Later |
| Long | The task plus a request to work for four times that average | Later |

The selected suite has 200 tasks across 18 families. The [7 October revision](docs/revisions/2026-10-07-hetzner-runpod.md) replaces five tasks on fresh slots and gives PaperBench a new reproduction-grader contract. Molecular geometry and the retained Terminal-Bench CAD task provide 3D work; the retired Gaussian-splatting task's visual-media creation coverage is removed.

The revised canonical selection contains 191 retained Opus 5.5 sessions and nine fresh sessions. All 200 selected native archives have been reverified. The original corrected 200-attempt batch and its format flags remain immutable. The [results record](docs/results/OPUS_5_5_FORECASTS.md) and [canonical registry](configs/canonical-results.json) record the evidence status. Natural runs must start from pristine state and satisfy their frozen targets; forecast answers cannot affect prompts, resources, order or timing.

The [natural preparation plan](docs/NATURAL_RUN_PREPARATION.md) uses the selected [Hetzner and RunPod allocation](docs/NATURAL_RUN_PROVIDERS.md): 192 CPU/desktop candidates on Hetzner, including 32 closed-book controller tasks, and eight H100 research subjects on RunPod. PaperBench reproduction uses separate H100 grading. This remains an intended allocation. Native workers, provider capacity and restoration are unqualified, and no natural attempt or cloud provisioning has started.

**No M values or timed durations are set yet.** M is the observed natural runtime of an attempt. After enough agents have run, use comparable natural measurements from the old and new versions to decide the per-task average for the timed requests. Old duration-prompted runs and externally stopped runs do not qualify as natural finishes. The natural run is also the calibration observation; there is no fourth run.

The current plan runs **200 task attempts concurrently, using one evaluated agent configuration at a time**. The configured capacity ceiling remains 220. Qualification and provider capacity still need to be demonstrated. Any allowed infrastructure replacement is recorded separately from the 600 planned study slots.

## What is here

The repository now has a working planning command and an autonomous fixture runner: a PostgreSQL ledger, detached workers, timing validation and local evidence storage. The fixtures make no model calls. The durable worker now also runs a direct pinned Harbor trial in local Docker, with native fault tests and content-addressed evidence. See the [9 October qualification](docs/results/2026-10-09-durable-harbor.md). Qualifying natural agent routes, session restoration, task environments/graders and the fleet remains unfinished.

```text
configs/             Task mix, agents, experiment rules and calibration state
benchmarks/          Exact candidates, replacements, then source locks and patches
src/agenttime/       Planner, ledger, fixture worker, clocks and evidence
tests/               Configuration, real PostgreSQL and crash-recovery checks
qualification/       Disposable native Harbor subjects and verifier
docs/                Build plan, protocol, reviews, v1 lessons and provenance
runs/                Local run outputs, excluded from Git
```

Start with the [five-step build plan](docs/BUILD_PLAN.md), the [selected task mix](docs/TASK_MIX.md), or the [experiment rules](docs/PROTOCOL.md).

The [v1 reuse plan](docs/V1_REUSE_PLAN.md) explains which parts of the ongoing refactor to carry over and which historical behaviors to replace.

## Try it

Use Python 3.12 for the reproducible local fixture. Install the locked dependencies once, then run:

```sh
uv sync --locked --group test
uv run --locked --group test agenttime plan --agent opus-5.5-max
uv run --locked --group test python -m unittest discover -s tests -v
uv run --locked --group test agenttime fixture demo --root runs/first-demo
```

The demo runs three disposable tasks against an isolated local PostgreSQL instance, stops that database, and keeps the report and evidence. Use a new output directory each time. See [local runner instructions](docs/LOCAL_RUNNER.md) for persistent fixture campaigns and restarting a controller.

The offline planner still works with only Python: `PYTHONPATH=src python3 -m agenttime plan --json`. It does not launch experiments.

Add an entry to [agents.json](configs/agents.json) to include another agent in planning. An agent is a model plus its runner, settings and tools, so changed configurations get distinct IDs. The initial Opus, Sol and MiMo entries are candidates whose execution routes still need qualification.

Earlier architecture documents used the working name “v2”. This repository is **AgentTime v1.1**. The [source record](docs/PROVENANCE.md) preserves that history and explains which decisions have since changed.

## What is still held

Study launch is disabled. Harbor 0.23.0 passed the combined local PostgreSQL/Docker fixture checks. This qualifies the disposable local worker path, not real agents or production workloads. Session archives on the Mac, native restoration and forks, lost-worker continuation, the one permitted infrastructure replacement and 200-way capacity (plus the optional 220-place margin) still need implementation or native tests. No M values or timed durations have been chosen.

The [local verification record](docs/reviews/2026-10-01-local-verification.md) records the passing checks and remaining work. The [Opus design review](docs/reviews/2026-10-01-opus-design.md), [review response](docs/reviews/2026-10-01-review-response.md) and [v1 incident audit](docs/V1_INCIDENT_AUDIT.md) explain the design and lessons behind it.

The [200-task preparation inventory](benchmarks/inventory/README.md) shows the files now present, draft task IDs and remaining work. [Open the searchable review](benchmarks/inventory/index.html).

## Archived AutomationBench preparation

The [AutomationBench audit](benchmarks/inventory/automationbench-audit.html) records the user decision to remove this family. The active suite has 200 tasks. The three earlier recommendations are retired, the six proposed prompt adaptations were not adopted, and no replacements are requested. Downloaded source, audit evidence and the experimental adapter remain available as history.
