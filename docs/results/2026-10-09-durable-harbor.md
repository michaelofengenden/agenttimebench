# Durable Harbor worker: local qualification

The combined PostgreSQL and Harbor worker passes its local checks after the Opus review fixes. Restarting the controller does not send a second prompt. Unknown executions keep their reservations, and a late workspace write cannot change the preserved submission's grade. No selected benchmark task or cloud worker was launched.

The new command is `agenttime harbor-fixture`. It runs one direct Harbor 0.23.0 Trial per permanent database claim and accepts only disposable model-free cases. It accepts no model name, study task path or arbitrary command. The original fixture path remains supported.

| Failure tested | Observed behavior |
| --- | --- |
| Restarted controller or concurrent duplicate worker commands | One native release per claim |
| Lost worker or ambiguous launch | Reservation retained; unaffected work can continue |
| Source changes before release or after execution | Admission pauses; no accepted completion from changed code |
| Broken Docker connection before dispatch | No task reservation consumed |
| Dispatch exception after reservation | That claim stays held; further dispatch pauses |
| Subject replaces its container identity | Worker checks its original identity; tampering is rejected |
| Duplicate release evidence | Admission receives an integrity failure, with stop proof preserved |
| Missing removal proof | Capacity stays held; subject is not replayed |
| Native grader failure | Timing retained, quality unavailable, no subject replay |
| Later workspace write | Grading uses terminal-bound bytes |
| Archive publication failure | Local evidence retained; reservation held |
| Corrupt, malformed or excessively nested evidence | Admission pauses instead of accepting it or crashing repeatedly |

Opus 5.5 max reviewed the exact approved packet through the Michael subscription and returned **REVISE**. Several findings concerned code already corrected while it reviewed; the additional defects were reproduced and fixed. The final independent local review approved those fixes. There is no second Opus approval of the revised tree. The [review response](../reviews/2026-10-09-opus-runner-response.md) maps every finding to its disposition.

Verification on the final source:

| Check | Passed | Skipped |
| --- | ---: | ---: |
| Combined PostgreSQL/Harbor suite | 21 | 0 |
| Boundary suite, including real Docker tampering | 19 | 0 |
| Original standalone Harbor probe | 2 | 0 |
| Full local suite with Harbor installed | 119 | 24 |
| Lightweight installation without Harbor | 97 | 46 |

Skipped checks are optional native or source-dependent checks; they are not counted as passes. Native suites above were run separately with opt-in enabled. Saved logs retain earlier failures and fixes. Exact recorded worker and container identities were checked after the tests. Harbor emits a checksum deprecation warning; the final native run did not report a worker leak.

The retained source snapshot matches every saved combined report. All six registered canonical forecast file hashes still match. These fixture results and development reviews are excluded from the study.

Next is a disposable Claude subscription qualification with real tool and subagent waits, native completion evidence, independent Mac archive acknowledgement, restoration/forking and recovery. Each task environment and grader, installed-environment provenance, production workers and 200-way capacity remain unqualified. Source/runtime changes currently pause a fixture campaign; audited migration and gate resume are still needed. Natural durations remain uncapped and unset.

Evidence: [qualification.json](../../runs/harbor-durable-qualification-20261009-final/qualification.json), [saved sources and logs](../../runs/harbor-durable-qualification-20261009-final/README.md), and [reproduction commands](../LOCAL_RUNNER.md). Earlier qualification directories remain intact as historical snapshots.
