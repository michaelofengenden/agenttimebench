# Opus natural-runtime launch plan

**Status:** planning for the revised 200-task suite. No natural run, provider allocation, model request or paid service is launched by this plan.

**Goal:** collect one fresh natural Opus 5.5 max attempt for each task, using Hetzner and RunPod, and preserve its runtime, final submission, grade and restorable session independently.

**Architecture:** reuse the PostgreSQL ledger, timing projection and evidence store. Connect the existing pinned Harbor probe to a durable worker, qualify Claude Code on both access routes, and use task-specific execution and capture adapters. Archive before disposing of the last state copy; grade an immutable completed submission outside the measured interval.

**Design sources:** [protocol](../../PROTOCOL.md), [natural preparation](../../NATURAL_RUN_PREPARATION.md), [session preservation design](../specs/2026-10-01-session-preservation-design.md), and the [current canonical forecast selection](../../../runs/canonical-opus-5.5-max-20261007/README.md).

This is the cohort launch plan. Its engineering work is divided into independently reviewed slices below. It does not claim that a production launch command exists yet. Preserve the current fixture-only admission boundary until the real executor has its own tested contracts.

## Agreed experiment

- 200 natural attempts first, one task per attempt. Short and long arms stay unset.
- Opus 5.5 max through Claude Code, using a subscription/API mix. The two routes require separate qualification and distinct configuration records within one explicitly declared Opus cohort.
- Keep the existing 200 subscription forecasts. No additional API forecasts. API natural observations carry a route-difference flag when paired with those forecasts; retain route-specific summaries. Sharing the Claude Code harness does not establish identical service latency or rate-limit behavior.
- The route-mix decision is a narrow amendment to the earlier one-configuration cohort rule and per-configuration forecast rule. It permits these two recorded Opus routes, not an unrecorded model change or mid-attempt route switch. Sol, MiMo and future models still run in separate later cohorts.
- Freeze a route assignment before launch. Recommend balancing each benchmark across routes using a recorded seed, subject to independently measured route capacity and the approved budget. The subscription/API task counts and seed are not chosen yet. Do not use forecast values, observed answers or task outcomes for assignment.
- Match model revision, max effort, CLI build, context setting, tools, subagents, resources and native task messages where qualification proves support. The forecast CLI is 2.1.280; retain its exact pin as the starting candidate. A necessary configuration change gets its own recorded identity and pairing difference.
- Each natural attempt starts from pristine task state, fresh home/configuration/memory, browser profile and simulator state. No forecast files, replies, session state or controller metadata enter its subject environment.
- Native subagents are permitted only where the benchmark permits them. The 32 closed-book tasks have no tools or delegation.
- No duration request, experiment time cap, success-score stopping rule, countdown or time-remaining message. Keep native task-defining endpoints. Never prolong a task merely to keep 200 attempts active.

## What is already present

| Item | Current evidence |
| --- | --- |
| Roster | 200 exact task identities across 18 families; five replacements recorded |
| Forecasts | 191 retained plus nine fresh; 200 canonical rows with original format flags preserved |
| Natural inputs | 200 controller-only contracts with frozen common-record provenance |
| Durable controller | Local model-free fixture with PostgreSQL claims, detached workers and evidence |
| Harbor | Combined local PostgreSQL/Harbor model-free worker qualified on 9 October; real agents remain unqualified |
| Production readiness | No native study task is qualified; no natural attempt has started |

The source hashes used for this plan are in the [planning snapshot](../../reviews/2026-10-07-natural-launch-planning.json). Existing fixture evidence is historical; this planning pass did not rerun or extend its runtime qualification.

## Compute plan

| Pool | Task places | Deployment approach to qualify |
| --- | ---: | --- |
| Hetzner | 192 | Ordinary Linux/container workers and closed-book controllers. Use suitable dedicated physical hosts for OSWorld's 18 native Ubuntu x86 guests. Separate tasks and enforce each resource allocation. |
| RunPod | 8 | Four PaperBench and four PostTrainBench subjects, each with the frozen H100 allocation. Qualify a direct provider/container path without assuming an inner Docker daemon. |
| External graders | Outside the 200 | CPU grading plus PaperBench's separate H100 reproduction contract. Queue grades on compatible freed resources or separate allocations without taking resources away from active subjects. |

Hetzner explicitly says its Cloud servers do not support nested virtualization. Dedicated-resource Cloud instances are still Cloud VMs; they are not the dedicated physical host proposed for the desktop guests. Verify KVM, guest boot, native GUI observations and per-guest resource enforcement on the selected physical host. [Hetzner FAQ](https://docs.hetzner.com/cloud/servers/faq/#can-i-run-virtual-machines-on-cloud-servers-or-rather-is-nested-virtualization-possible)

RunPod's GPU Pod overview says Docker Compose and starting one's own Docker instance are unsupported. GPU and CPU product capabilities must not be assumed interchangeable. Qualify the exact GPU execution route with the pinned task images, native grader, resource allocation and archive capture before buying the full fleet. [RunPod Pod limitations](https://docs.runpod.io/pods/overview#limitations)

The existing subject profiles total 695 declared CPU units, 3,088 GiB RAM and eight H100s across 168 compute allocations, plus 32 closed-book controller tasks. Grading, controllers, VM overhead, archives and spares are additional. These are declared requirements, not a server quote. Keep CPU-thread/core semantics explicit and avoid silent oversubscription.

## Five gates before the cohort

### 1. Prove the combined runner locally

**Engineering scope:** `src/agenttime/fixture.py`, `harbor_fixture.py`, `ledger.py`, `measurement.py` and `evidence.py`, with a small separate executor module and focused tests. Read the existing implementations before choosing the exact executor interface; do not weaken their fixture validation or treat arbitrary shell commands as admitted study tasks.

- [x] Connect one direct Harbor Trial to one permanent attempt claim and one worker reservation. Disable the Job retry layer.
- [x] Generate prompt-release, native-terminal, owned-work and capture events from the real execution path. Preserve both AgentTime and Harbor phase timings.
- [x] Use disposable model-free tasks to test controller restart, worker crash after release, ambiguous launch, late writes, grader failure, corrupt evidence and failed archive publication.
- [x] Prove exactly one subject invocation per claim. Preserve uncertain reservations and isolate a task failure; stop new admissions for shared timing/evidence corruption.

**Acceptance:** fresh test receipts for the combined PostgreSQL/Harbor path show one invocation, correct timing, immutable submission capture and no replay across each fault case. The previous fixture and standalone Harbor tests remain regression checks; their old results alone do not satisfy this gate.

9 October evidence: [combined local qualification](../../results/2026-10-09-durable-harbor.md). The fixture captures immutable submission bytes at its native terminal event. This gate is local/model-free only; subsequent gates remain open. Current model qualification uses the selected subscription.

### 2. Qualify Claude, both routes, and session preservation

**Engineering scope:** one pinned Claude Code adapter, explicit route configuration, task tool policies, and the independent archive/restore path from the existing preservation design.

- [ ] In disposable sessions, verify the actual model, effort, CLI hash, context, allowed tools and native subagent behavior on each route. Pin the supported observation and terminal events.
- [ ] Create a clean environment with exactly the intended authentication source. Verify the effective route instead of assuming the credential used. Keep credentials out of manifests and archives.
- [ ] Exercise tool waits, background work, final response with an owned child still active, native completion, rate-limit waits and account interruption. Record waits during released work in elapsed runtime; keep pre-release setup and queueing separate.
- [ ] Obtain the independent baseline checkpoint and Mac acknowledgment before release. Test streaming collection, offline Mac, interrupted transfer and capacity exhaustion using disposable data.
- [ ] Restore native session plus workspace/environment on a fresh compatible worker; fork a new identity while leaving the parent unchanged. Qualify each distinct task capture type, not just a transcript import.
- [ ] Fault-test recovery before study launch: typed external-failure evidence and independent execution-stop proof; fencing of the prior segment; same-session continuation with no replayed tool effects or new task prompt; and preserved clock continuity including interruption time. Archive restoration alone does not qualify scientific continuation.
- [ ] When continuation is impossible, prove that the ledger permits at most one fresh replacement across controller restarts and concurrent claims. Bind it to the same frozen task, arm and access route. Key the allowance to the original logical cohort/task/arm so creating a route/configuration record cannot reset it. Inject wrong answers, refusals, unknown failures and post-completion grader/archive loss to prove they cannot trigger a subject replacement.
- [ ] Test an unreachable original: it keeps its reservation and prevents switching model cohorts until execution-stop evidence exists, while unrelated work can continue. An unreliable timing continuation is flagged or held rather than labelled a valid natural finish.

**Acceptance:** both routes have separate qualification receipts, and each admitted adapter proves timing, capture, restore, fork and recovery behavior. Typed stop/failure evidence, same-session continuation and the durable one-replacement limit must pass their fault tests before any selected task is released. A tool-disabled forecast canary is insufficient evidence for the natural tool path.

Claude Code supports both subscription authentication and API access. Its documented credential precedence makes API environment variables capable of overriding subscription selection; use a minimal explicit environment and inspect actual route evidence. Check behavior in the pinned executable rather than assuming current documentation describes every detail of that build. [Claude Code authentication](https://code.claude.com/docs/en/authentication#authentication-precedence)

### 3. Qualify every task and grader

**Engineering scope:** a per-task readiness record, pinned environment/material preparation, task-specific capture and grading adapters, and source separation checks. The existing 200 preparation contracts are the inputs, not admission records.

- [ ] Boot the pinned environment, hydrate only allowed pristine files, verify their hashes and native paths, then record actual CPU/RAM/GPU/software and effective limits.
- [ ] Verify native endpoints and approved removal of work caps through prompt, wrapper, backend and healthcheck layers. Preserve task-defining limits such as YC's simulated year and PaperBench's separate seven-day reproduction maximum.
- [ ] Test known-good, wrong, malformed, empty and missing submissions against each grader. Capture missing quality separately from zero. Keep partial credit and reference material private.
- [ ] Complete task-specific holds in the [preparation checklist](../../NATURAL_RUN_PREPARATION.md#known-task-work-to-resolve), particularly `paper-01` data/checkpoint access, OSWorld task 029 setup, CORE Hard file exclusions, Chisel read-only/network enforcement, DeepSWE image pins and ProgramBench executable placement.
- [ ] Run any agent-bearing qualification on disposable tasks outside the selected cohort. If a selected task receives an accidental subject attempt, preserve it and stop to resolve its study disposition; do not call a rerun the first attempt.

**Acceptance:** 200 task records bind source, environment, effective limits, target profile, native interface, capture, grader and archive capability. A remaining hold blocks a claimed full-200 cohort. Any task removal or substitution returns to the user and requires a new forecast pairing decision.

### 4. Prove capacity and freeze the launch manifest

**Engineering scope:** provider reservations, model-route permits, readiness aggregation and a prepared release barrier. Costs and account limits are independent checks.

- [ ] Obtain actual Hetzner host and RunPod H100 capacity, permitted model throughput, archive headroom and measured transfer overhead. Begin with quotes and approved disposable pilots, then scale 1, 8, 32, 64, 128 and 200 workloads. Test the 220 ceiling separately if retaining it as a demonstrated recovery margin.
- [ ] Freeze the route counts and per-task route assignment after capacity checks, before any selected natural task begins. Balance within benchmarks where feasible; document unavoidable imbalance and keep route-specific analysis.
- [ ] Quote CPU/desktop fleet, eight H100 subjects, grading, API usage, persistent storage and transfer separately. Show time-based cost scenarios independent of forecast values. Choose an operational spending policy before renting the fleet.
- [ ] Prepare all task workers and baseline archives before prompt release. Perform controller integrity checks and record a release policy, clock sources and measured release spread from the full-scale disposable test.
- [ ] Seal task/configuration/route/profile/source hashes, readiness receipts, provider reservations, archive destination and recovery rules in a new launch manifest. Recheck them at dispatch.

**Acceptance:** genuine native tool traffic and provider behavior pass at the planned scale, without resource oversubscription or duplicate onset. Fifty concurrent forecasts do not establish capacity for 200 natural agents. The model API and subscription pools are measured separately. No exact simultaneous-onset claim is allowed without measured evidence.

### 5. Launch once, grade completed work automatically, and report

- [ ] Release the prepared 200-task cohort under the approved manifest. Track actual active count and release spread; naturally short tasks may finish before the last release.
- [ ] Measure from native prompt release to voluntary completion or the declared native endpoint. Include owned tool/subagent waits once. Do not use batch job duration or a final perfect score as the timestamp of first success.
- [ ] Preserve the completed submission using its qualified capture contract, then enqueue an independent grader. Grading workers may run while other tasks continue; their results do not feed back into those tasks or stop them at 100%.
- [ ] Keep outcomes, timing validity, submission availability, grade status, archive status and scientific eligibility separate in the status view. Automatically retry a safe transfer or grader operation on the same immutable submission where qualified; never rerun a subject because grading failed.
- [ ] Prefer qualified continuation after infrastructure failure. Allow one fresh replacement only when continuation is impossible and the original is proven stopped. Keep the original, linked replacement and all interruption time. Refusal, wrong answer, voluntary finish and unknown failure are not retry reasons.
- [ ] An unreachable execution remains reserved and blocks switching to another model. An archive receipt alone does not prove execution stopped. An operational spending stop is a censored outcome, never a natural finish.
- [ ] Produce per-task natural runtime, final quality, route, resources, termination reason, request/session identity, archive/fork capability and forecast-pairing status. Preserve unsuccessful outcomes. Later decide how comparable natural observations contribute to short/long durations.

**Acceptance:** every selected task has a terminal outcome or an explicit unresolved/censored record, independently verified evidence, and a discoverable session archive. Completeness must not be fabricated by retrying or hiding failures. API/subscription route effects cannot be identified from one disjoint task split alone; route summaries describe the assigned cohort, not a causal comparison.

## Decisions still needed, at the point they matter

| Decision | Proposed approach | When needed |
| --- | --- | --- |
| Subscription/API task counts | Freeze after throughput/cost qualification; distribute both routes across benchmark families | Before final route assignment |
| API account/endpoint and subscriptions | Direct Anthropic API is the starting candidate for the same Claude Code executable; identify actual account handles without pasting secrets | Before model-bearing route pilots |
| Qualification and study spending | Separate pilot allowance, forecast-independent hourly fleet scenarios, and policy for continuing or censoring at the budget boundary | Before paid actions |
| Exact hardware and regions | Hetzner physical desktop hosts plus suitable Linux workers; RunPod exact H100 subject/reproducer allocations | Before provider pilots and reservations |
| Mac archive path and reserved capacity | Dedicated private archive volume with capacity and transfer guards | Before independent checkpoint tests |
| Release acceptance threshold | Derive from the disposable full-scale timing test and approve before the cohort | Before launch manifest sealing |

The already-decided choices are the provider pair, 200-task roster, Opus first, the subscription/API mix, keeping the existing forecasts, uncapped natural work, native subagents where permitted, Mac archives and one conditional infrastructure replacement. Do not ask for those decisions again.

Gate 1 now passes its local model-free tests. The next engineering slice is Gate 2: the chosen Claude subscription route, independent archive acknowledgement, native restore/fork and qualified recovery. API qualification can follow separately. No selected study task is released by these development checks. Subsequent engineering slices can prepare assets and archive formats while account, cost and provider choices are resolved.
