# AgentTime v1.1: first autonomous runner build

1 October 2026. This specifies the first implementation milestone under the previously approved architecture. The user has asked to start building, use Opus 5.5 max for review, and ask about consequential decisions. The repository is now at `/Users/michaelofengenden/Developer/TimeResearch/AgentTime-v1.1`.

## Outcome

Build a reproducible controller and worker path that completes disposable tasks, preserves their evidence, survives a controller restart without duplicate execution, and reports what it could and could not measure. This milestone uses model-free fixtures. Study execution, paid provider qualification and the 220-worker fleet need later capacity and budget decisions. Passing fixtures cannot establish those capabilities.

Keep the existing Python coordinator, PostgreSQL ledger and immutable evidence-store architecture. Start with a local filesystem implementation of the evidence-store interface for disposable tests; production requires independent durable storage. Harbor 0.23.0 remains the pinned executor candidate. Python must be at least 3.12 because that Harbor version requires it. Do not copy the old runner wholesale or introduce a second workflow platform.

## Decisions already made

- The unit is one agent configuration: 220 natural, 220 short and 220 long slots, 660 nominal slots. Agent configurations are extensible.
- Collect genuinely unprompted natural runtimes first. M is the measured natural runtime, not a fixed budget. Timed requests remain unset until enough comparable old and new natural observations exist. No fourth calibration run and no automatic pooling of prompted historical runs.
- Natural attempts have no duration request or experiment time cap. Preserve native task-defining endpoints. Record external operational stops as censored or failed outcomes. Operational spend and incident policies must be chosen before model work.
- Target 220 active task attempts in total, one evaluated agent configuration at a time. Actual capacity is not yet proved.
- Use OSWorld 2.1 for 18 places and WeirdML v3 for eight. Task identities and native qualification remain incomplete.
- Reattach healthy sessions first. Continue interrupted sessions only through qualified paths with the same state, prompt, model and clock history. One fresh replacement is allowed only for confirmed external infrastructure failure that cannot be continued, after the original is proven stopped. No replacement chains and no retries of wrong answers, refusals or voluntary finishes.
- Native terminal evidence, artifact capture, raw grade and scientific admission are distinct facts.

## Measurement contract

An event records schema version, experiment and attempt identity, execution-segment identity, a unique event ID, per-segment sequence number, worker/boot clock identity, monotonic nanoseconds, UTC audit time, event kind and the event payload. Store the original events; do not reconstruct missing boundaries from wrapper exit times.

Record preparation, prompt release, native terminal response, submission preservation, grading and cleanup separately. The primary elapsed interval is complete prompt release to native voluntary terminal event or task-defined terminal endpoint. Tool calls, background work and waits count while the agent is active. Parallel subagent time is not summed. Harbor's agent-execution interval remains an independently labelled reference measurement. A 100 percent final score is not evidence of first success.

Monotonic differences require a shared clock identity. A restart on the same worker boot may preserve that identity; crossing a worker or boot boundary needs independently qualified clock-continuity evidence. Without it, report the primary interval as unavailable. UTC timestamps alone do not repair it. Retain all interruption intervals and never silently subtract them.

Missing or conflicting terminal evidence must not produce a valid natural finish. Delivery order may differ from source order: reconcile source sequences through one closing record before projecting an interval. Exactly repeated event delivery is idempotent only when all bytes/content match. A conflicting duplicate is an integrity error. Preserve invalid events separately from admitted measurements.

The file-submission fixture proves the timing and preservation contract for that fixture only. Desktop/live-state and external-state benchmarks need separate adapters. Do not represent an arbitrary directory copy as a complete preserved desktop or external service state.

## Durable execution contract

Each logical slot has an immutable identity: experiment revision, agent configuration, task revision and arm. A physical attempt belongs to one slot, with explicit original/replacement lineage. A database uniqueness constraint bounds allowed attempts. Event ingestion, state transitions and dispatch intent use transactions.

Write durable dispatch intent before launching external work. A worker consumes one permanent execution claim bound to its unique process identity. Only the caller that acquires the claim may invoke the fixture. Duplicate commands cannot consume the same claim again. A crash between launch and receipt is ambiguous, not permission to relaunch. Reconciliation first checks the original worker/session identity. Lease expiry alone never authorizes another subject process.

Controller restart and worker restart are different failure modes. Restarting the controller must reattach to a healthy worker without sending a second prompt. The fixture worker keeps its own durable event journal and execution identity. If a worker cannot be contacted, its slot stays accounted for until the original is proven stopped or its owned work is conclusively reconciled. Database fencing alone is not a stop proof. A permanently unreachable original keeps its reservation and blocks the next evaluated agent, as confirmed by the user.

Resource reservations and provider permits are separate admission checks. The coordinator owns the global cap; a worker owns one trial with Harbor attempts=1 and automatic retries=0. No nested concurrency multiplication. Unknown required capacity prevents dispatch.

Known recoverable operations such as uploading an already sealed artifact may retry idempotently. They must not replay an agent attempt. A failed grader is recorded as missing quality, with independently valid timing retained. A finished agent is never rerun because later evidence handling failed.

## Autonomy and selected failure policy

The program, not a supervising language model, must own transitions, retries, reconciliation, status and final reports. Review agents are development tools only.

The user selected isolation of an affected attempt while continuing unaffected work. Shared clock, source, identity or storage corruption stops new admissions. Existing owned resources are preserved and reconciled. No policy permits silently changing the experiment or retrying to improve scores.

The first milestone records this selected policy explicitly. Missing policy may still permit validation and inspection, but not campaign dispatch. Cloud/account choice, provider capacity, spend envelope, external operational-stop rules and historical-data eligibility remain separate decisions before study execution.

## First milestone acceptance

1. Valid event histories produce the expected elapsed interval; delayed setup and grading do not change it. Missing, conflicting and cross-clock boundaries are reported accurately.
2. Two competing dispatchers cannot both release the same slot. Repeated event delivery cannot overwrite earlier evidence.
3. A controller killed after worker release can restart, discover the original worker, collect its result and complete the fixture without a second invocation.
4. An ambiguous launch does not cause automatic replay. An infrastructure replacement consumes its one allowed slot and preserves the original outcome.
5. Artifact and grader failures never trigger subject replacement. A report distinguishes unavailable quality from an actual zero score.
6. No new evaluated agent configuration starts while potentially live work from the previous one remains unresolved.
7. Dependency/source pins and the exact effective manifest accompany the report. A second clean fixture run produces the same structural results; generated identities and observed timestamps are expected to differ.

## Review request

Review this milestone for measurement errors, unsafe replay, false claims of autonomy, excessive scope and unnecessary complexity. Recommend the smallest implementation sequence that actually exercises the production contracts. Identify decisions requiring the user, without reopening the established experiment choices. Native runtime and 220-way qualification remain separate gates.

## First-slice boundary

The implementation plan narrows the first milestone to standalone model-free fixtures, the real PostgreSQL ledger and local evidence. The first slice does not exercise Harbor. Worker-loss continuation, replacement execution, preparation redispatch, clock qualification including host sleep, provider permits, production storage, native stop proofs and fleet qualification remain separate work. The protocol permits a qualified replacement; this slice does not yet execute one. Terminal-bound bytes demonstrate one explicit submission contract.
