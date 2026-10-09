# Local build verification, 1 October 2026

The first working slice passed 96 core checks and two native Harbor checks. A retained demonstration completed all three disposable tasks. These are model-free fixtures, not measurements of evaluated agents.

The [receipt](2026-10-01-local-verification.json) records commands, runtime versions, source hashes, evidence locations and remaining limits. The core test run discovered 98 tests: 96 passed and the two opt-in native tests were skipped. The separate Docker run then passed those two native tests.

## What works

- Real PostgreSQL admission enforces capacity, one evaluated configuration and permanent single-use worker claims.
- A detached worker survives a controller restart. Reconciliation collects the same execution rather than sending another prompt.
- Source journals distinguish setup, prompt release, native terminal, grading and cleanup. Late delivery, conflicting records and missing evidence are tested.
- Sealed submission bytes survive later working-file writes. Missing grades remain unavailable rather than becoming zero or causing a rerun.
- Ambiguous work retains its reservation. Unaffected work can continue, while shared integrity failures pause new admissions.

## What the Docker probe establishes

A custom agent ran directly through Harbor 0.23.0 with a pinned Python image. One subject completed and another crashed after prompt release. Independent witnesses recorded exactly one invocation each. The failed subject had no valid natural duration or score.

The completed subject submitted 42, then overwrote the same working file with an incorrect answer. The verifier still graded the preserved submitted bytes correctly. Harbor's execution interval was broader than the native interval, as expected from the deliberate work after the terminal event.

Harbor's dynamic no-network policy was unavailable on this Mac. A task-owned Docker Compose override used `network_mode: none`; inspection verified the actual container setting before prompt release. Each exact container was confirmed removed after Harbor finalized its artifacts. This does not qualify dynamic egress control.

Inputs were frozen before execution and retained with hashes. The probe refuses a final qualification report when its module or frozen inputs change during execution. The native agent timeout was unset. A direct Trial has no Job retry layer.

## Review findings and repairs

Opus 5.5 max reviewed the approved design packet through the photojournalist subscription. Its [receipt](2026-10-01-opus-receipt.json) and [review](2026-10-01-opus-design.md) are retained. It did not review the final implementation bytes.

The independent core code review found concurrency and error-handling defects. Targeted regressions reproduced them before repair, including a reaper that waited for a live child. The final scoped core re-review returned `APPROVED`.

The separate native probe review required a real overwrite of the submitted working file and source snapshots taken before execution. Both were repaired, the stronger native regression passed, and the scoped re-review returned `APPROVED`. The reviewer did not execute the tests; the execution receipts are separate.

## Next connection

Connect Harbor to the durable worker and test failures through that combined path. Then qualify each real agent adapter, exact session/workspace restoration, typed stop evidence and the one permitted infrastructure replacement. Production storage, clocks across host sleep or reboot, the full benchmark roster and 220-way capacity still need qualification.

Study launch stays disabled. M remains an observed natural duration, and no short or long time requests are set.
