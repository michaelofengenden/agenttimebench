# Build plan

The first fixture implementation passed its scoped independent review: real PostgreSQL admission, detached workers, source timing and local evidence. The combined PostgreSQL/Harbor worker now passes the [native model-free fault checks](results/2026-10-09-durable-harbor.md). Next is one pinned Claude subscription route with native timing, restoration and recovery, before task qualification and scaling. The [local runner guide](LOCAL_RUNNER.md) explains how to reproduce the fixture.

| Step | Build | Done when |
| --- | --- | --- |
| 1. Prove one run | Connect a disposable task and one agent to Harbor. Record prompt release, agent completion and the preserved submission. | Setup and grading stay outside measured runtime; tool waits count; work after completion cannot change the grade. |
| 2. Prepare the suite | Finalize the task IDs, acquire clean pinned sources, fix or replace bad verifiers, and qualify each agent route. | Every selected task starts correctly, has a trustworthy grader and has the intended native limits. |
| 3. Make it reliable | Add a durable run ledger, resource reservations, verified session archives on the Mac, restoration/forks, session recovery and the one-replacement rule. | Controller restart does not duplicate work; archived session and task state restore on a fresh server; forks preserve the original. |
| 4. Prove capacity | Test disposable workloads at 1, 8, 32, 64, 128 and 200 active attempts, with a separate 220-attempt margin check if retained. | Machines and providers support the target under real tool traffic, with acceptable contention and working recovery. |
| 5. Collect, then compare | Collect separate forecasts, then run natural attempts with fresh state for one agent at a time. Add agents. Review comparable old/new observations, then choose the averages and run short/long attempts. | Each report shows natural runtime, requested duration, final quality and failures separately. |

For the current allocation, each agent contributes **200 natural + 200 short + 200 long = 600 planned runs**. The natural run is the M observation. Infrastructure replacements and disposable pilots are separate counts. The [7 October revision](revisions/2026-10-07-hetzner-runpod.md) refreshed nine forecasts while preserving the historical corrected batch. The revised canonical selection contains 191 retained sessions and nine fresh sessions, with all selected native archives reverified.

The [natural preparation plan](NATURAL_RUN_PREPARATION.md) covers 200 clean contracts, combined durable Harbor execution, native timing, restoration, task/grader checks and capacity. The selected [provider allocation](NATURAL_RUN_PROVIDERS.md) is 192 Hetzner CPU/desktop candidates, including 32 closed-book controller tasks, plus eight RunPod H100 research subjects. PaperBench reproduction uses separate H100 grading. The revised requirements no longer call for Windows, T4 or L4. Native CPU allocations remain task-specific. No resources are provisioned and no natural task is qualified. Forecasts use no GPU provisioning.

The [current Opus natural launch plan](superpowers/plans/2026-10-07-opus-natural-launch.md)
records the selected subscription/API mix through Claude Code, use of the existing
subscription forecasts, provider-specific VM/container constraints and five
readiness gates. Its immediate engineering slice is the combined model-free
Harbor/durable-worker path, which now passes local fixture qualification. The user selected subscriptions for current model qualification; the eventual mixed-route cohort remains a separate freeze. Selected study tasks and provider spending remain held.

## Who does what

- **Harbor:** prepare supported environments, invoke agents, collect native artifacts and run verifiers.
- **AgentTime:** define the experiment, measure the right interval, coordinate global concurrency, preserve evidence, recover sessions and decide which observations belong in analysis.
- **Infrastructure:** a durable database, an evidence store, and workers suited to CPU, GPU or desktop tasks. The local fixture uses an isolated database and filesystem evidence. Production services and native worker pools are not provisioned.

The planner, fixture runner, ledger, timing and evidence modules now live under `src/agenttime/`. The separate boundary probe remains a regression test; the durable Harbor executor is `src/agenttime/harbor_worker.py`. Both remain explicitly excluded from study results. Keep benchmark sources and fixes under `benchmarks/`, and experiments in `configs/`. Avoid copying the old runner wholesale. The [v1 reuse plan](V1_REUSE_PLAN.md) maps its useful components and tests to the new protocol.

## Decisions needed at the relevant step

Before study controls: finish task selection and qualification, choose exact agent routes, verify the resource/cost envelope, and define how external interruptions or operational stops are reported. Natural attempts have no requested duration or experiment time cap.

Before timed runs: decide what constitutes enough comparable natural observations, approve historical-run eligibility and weighting, and record the exact observations and averages used. Nothing fixes M now. Later recalculations get their own recorded revision; they never rewrite durations already requested.

The earlier Opus 5.5 max review approved the architecture for build planning. It did not qualify the runtime. The new name, per-agent accounting and open natural-runtime collection policy are the user's subsequent decisions.

## Session archives

The user requires every session to remain reachable and reusable after worker deletion. Use independent private filesystem storage on the Mac, retaining native session state and workspace/environment checkpoints. Full native restoration and forks are new qualification gates, not established by the existing evidence-store tests. See the [session preservation design](superpowers/specs/2026-10-01-session-preservation-design.md).

The [new natural preparation folder](../runs/natural-opus-5.5-max-preparation-20261007/README.md) must bind each task to its own common record and pristine source state. Keep the earlier preparation intact. Before launch, enforce CORE Hard material filters and Chisel's read-only inputs and network restrictions. PaperBench needs its private unchanged rubric, approved H100 reproduction environment and native seven-day external grading cap. The unresolved `paper-01` data/checkpoint sources and the other [task holds](NATURAL_RUN_PREPARATION.md#known-task-work-to-resolve) remain in force.
