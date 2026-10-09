# Autonomous fixture implementation

The user approved building the v1.1 architecture. This is its first working slice. The development reviewers cannot authorize a study launch. No provider or benchmark execution is part of this slice.

## Scope and review response

Use the real PostgreSQL ledger, a persistent local worker, a source event journal and a content-addressed local evidence store. The executor is explicitly a model-free fixture, not a Harbor qualification. Ship commands that start, resume and inspect fixture campaigns. Native Harbor adapters follow after this slice passes.

The first independent review identified four changes, all accepted: a durable single-use worker claim; terminal-bound submission bytes; source-sequence validation independent of delivery order; and separating controller reattachment from unsupported worker-loss continuation and fresh replacement execution. Keep replacement identity reserved in the schema but refuse replacement execution until that separate path is qualified.

## Contracts

- A fixture campaign has an immutable effective manifest and digest, one agent configuration, natural-only tasks and an evidence root. Study configurations cannot be executed by the fixture command. Global capacity and one-agent admission are serialized in PostgreSQL. Ambiguous or quarantined attempts keep their reservation.
- Persist dispatch intent before starting a process. Each attempt has one immutable owner claim, acquired transactionally by the worker. Only the successful claim caller may invoke the fixture. No lease expiry or controller restart resets it. A duplicate worker command exits without executing the subject. A reservation with no confirmed worker is held, not replayed.
- Worker processes are detached from the controller and persist their journal independently. The controller can restart and collect the same attempt. Only a finishing owner that has joined its owned children can release its reservation. Worker-loss continuation and fresh replacements are recorded as unqualified, not silently attempted.
- Every source event has schema_version=1, attempt_id, execution_id, event_id, sequence (starting at 1), clock_id, monotonic_ns, recorded_at (UTC), kind and payload. A journal_closed event identifies the final source sequence. Exact repeated delivery is idempotent; conflicting IDs or sequence positions are integrity failures. Missing earlier events remain pending. A valid complete history has exactly one prompt_released and native_terminal event, in source and monotonic order, from one execution and clock.
- The fixture emits prompt_released only after setup. native_terminal contains the final submission bytes and digest at that boundary. Later workspace writes cannot change those bytes. This demonstrates an explicit byte-submission contract only. A directory snapshot taken after finish cannot substitute for it.
- Derive runtime_seconds only from the two native fixture boundaries. Never use controller timestamps, process exit or artifact upload time. Grading and capture failures preserve independently valid timing. Reports expose timing_status, runtime_seconds, artifact_status, quality_status, score, admission and limitations separately. Fixture results are always excluded from study analysis.
- Failed attempt handling follows the selected policy: quarantine an isolated attempt and continue unaffected work; shared ledger/evidence integrity failure pauses new admissions. No automatic clearing of a shared pause.

## Implementation sequence and ownership

1. Root: dependency lock, plan and public commands. Pin psycopg 3.3.6. A project-local pgserver 0.1.4 test extra supplies PostgreSQL without starting or modifying system services. Keep Harbor 0.23.0 as an optional candidate pin, not a validated executor. Python 3.12 is the reproducible local test interpreter.
2. Measurement implementer: `src/agenttime/measurement.py`, `src/agenttime/evidence.py`, `tests/test_measurement.py`, `tests/test_evidence.py`. Test missing/conflicting boundaries, delayed delivery, clock changes, partial journals and immutable artifacts before implementation.
3. Root: `src/agenttime/ledger.py`, `tests/test_ledger.py`, test database utility. Prove competing reservations/claims against actual PostgreSQL, one-agent/cap enforcement, manifest immutability and ambiguity holds.
4. Root: `src/agenttime/fixture.py`, CLI and `tests/test_fixture.py`. Exercise controller restart, duplicate workers, terminal-bound output despite late writes, grade loss and isolated failure. No fabricated model measurements.
5. Independent review of the actual diff, then full tests and a disposable demonstration. Preserve review receipts and reproducible commands. Report native integration, recovery qualification and capacity as pending.

## Acceptance evidence

Run the complete unittest suite with real local PostgreSQL. Kill a fixture controller after prompt release; a second controller must collect the same attempt and exactly one subject invocation. Launch competing controllers/workers and verify one-use claims. Deliver terminal evidence first, then the earlier records, and verify a correct interval. Attempt a late answer overwrite and verify the grade uses terminal-bound bytes. Retain ambiguous work while unrelated tasks complete. A failed grade must be unavailable, never zero by default or a reason to rerun.

Existing v1.1 allocation, natural collection and recovery policy remain unchanged. No fixed M, timed durations, study deadlines or calibration filters are introduced.
