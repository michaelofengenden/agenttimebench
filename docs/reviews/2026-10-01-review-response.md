# First-build review response

Opus 5.5 at max effort reviewed the exact user-approved design packet. The [receipt](2026-10-01-opus-receipt.json) confirms the model, requested effort, account route, packet hash and successful native completion. It was a design review, before the new runtime code. An independent Codex reviewer reviewed the plan and then the implementation.

## Changes made from the reviews

- Dispatch ownership is a permanent single-use claim. Losing a controller or lease cannot authorize another invocation.
- Source sequence and journal closure decide completeness; arrival order does not decide validity.
- The file fixture seals submission bytes inside the terminal event. Later writes cannot change those bytes.
- Real PostgreSQL admission is serialized across all campaigns with one database lock.
- The worker runs separately from the controller. A restarted controller discovers the original attempt.
- Effective manifests, report identities, source evidence and content hashes are checked before reconciliation.
- Duplicate JSON keys and nonfinite numbers are rejected before configuration is hashed.
- The user confirmed that an original which cannot be proven stopped blocks the next evaluated agent.

The implementation review found real concurrency and error-path defects after the initial tests passed. Targeted regressions reproduced them and the repairs passed a scoped re-review. The reviewer approved the core fixture, explicitly excluding native Harbor integration and study readiness.

## Native boundary probe and remaining work

A separate custom Harbor fixture passed two local Docker trials, covering native prompt and terminal hooks, an unset agent timeout, an independent invocation witness, a crash without replay, Docker network isolation and exact-container removal. The test uses a direct Harbor Trial, not a Job retry layer. Both its subjects and the PostgreSQL fixture use artificial delays, not model measurements. The combined PostgreSQL/Harbor worker path remains unqualified.

Preparation dispatch must be distinguished from subject onset before enabling safe preparation retries. The current first slice conservatively holds ambiguous reservations. It does not transfer ownership, continue lost workers or start fresh replacements. Those paths need typed stop evidence and machine-verifiable infrastructure causes.

Clock qualification must include host sleep, continuous-clock choice, interruption handling and a declared anomaly policy. Current fixture clocks are same-process monotonic observations, not a qualified cross-host timing system. Native throttling events must be recorded before the 220-way cohort is attempted.

Production evidence storage, upload recovery, a broader controller/worker/database crash matrix, revision-free study lineage and report reconstruction after storage failure remain qualification work. Study launch remains disabled.
