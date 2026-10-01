# Build plan

The foundation exists. Next, build and prove the smallest complete run before scaling it.

| Step | Build | Done when |
| --- | --- | --- |
| 1. Prove one run | Connect a disposable task and one agent to Harbor. Record prompt release, agent completion and the preserved submission. | Setup and grading stay outside measured runtime; tool waits count; work after completion cannot change the grade. |
| 2. Prepare the suite | Finalize the task IDs, acquire clean pinned sources, fix or replace bad verifiers, and qualify each agent route. | Every selected task starts correctly, has a trustworthy grader and has the intended native limits. |
| 3. Make it reliable | Add a durable run ledger, resource reservations, evidence uploads, session recovery and the one-replacement rule. | Restarting the controller does not duplicate work or lose attempt history. |
| 4. Prove capacity | Test disposable workloads at 1, 8, 32, 64, 128 and 220 active attempts. | Machines and providers support the target under real tool traffic, with acceptable contention and working recovery. |
| 5. Collect, then compare | Run natural attempts for one agent at a time. Add agents. Review comparable old/new observations, then choose the averages and run short/long attempts. | Each report shows natural runtime, requested duration, final quality and failures separately. |

For the current allocation, each agent contributes **220 natural + 220 short + 220 long = 660 planned runs**. The natural run is the M observation. There is no additional timed M run. Infrastructure replacements and disposable pilots are separate counts.

## Who does what

- **Harbor:** prepare supported environments, invoke agents, collect native artifacts and run verifiers.
- **AgentTime:** define the experiment, measure the right interval, coordinate global concurrency, preserve evidence, recover sessions and decide which observations belong in analysis.
- **Infrastructure:** a durable database, an evidence store, and workers suited to CPU, GPU or desktop tasks. These are planned components, not services installed by this scaffold.

Implement the runner, adapters, ledger and reports under `src/agenttime/` as these steps are built. Keep benchmark sources and fixes under `benchmarks/`, and experiments in `configs/`. Avoid copying the old runner wholesale.

## Decisions needed at the relevant step

Before study controls: finish task selection and qualification, choose exact agent routes, verify the resource/cost envelope, and define how external interruptions or operational stops are reported. Natural attempts have no requested duration or experiment time cap.

Before timed runs: decide what constitutes enough comparable natural observations, approve historical-run eligibility and weighting, and record the exact observations and averages used. Nothing fixes M now. Later recalculations get their own recorded revision; they never rewrite durations already requested.

The earlier Opus 5.5 max review approved the architecture for build planning. It did not qualify the runtime. The new name, per-agent accounting and open natural-runtime collection policy are the user's subsequent decisions.
