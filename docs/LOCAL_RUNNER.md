# Run the local fixture

This is a real controller, PostgreSQL ledger and worker path using disposable tasks. It makes no model calls and its results cannot enter the study.

Use Python 3.12 and install the committed dependency lock:

```sh
uv sync --locked --group test
uv run --locked --group test python -m unittest discover -s tests -v
uv run --locked --group test agenttime fixture demo --root runs/first-demo
```

Choose a new output directory each time. The demo refuses to overwrite existing evidence. It starts an isolated PostgreSQL server on a local socket, runs three fixtures, stops that server and retains `manifest.json`, `report.json`, source journals and hashed evidence. No system database or Docker service is changed.

One fixture submits 42, then a background child overwrites its working file. Its grade must still use the submitted bytes. Another loses its grade and reports unavailable quality. A third submits a wrong answer and reports a real zero. All three retain their measured fixture interval separately.

## Persistent local campaigns

Supply a dedicated PostgreSQL database in the `AGENTTIME_DSN` environment variable. Do not put credentials in manifests or command arguments. Create a JSON array of fixture tasks, such as `[{"id":"example","work_seconds":0.2}]`.

```sh
agenttime fixture init --campaign check-1 --tasks fixture-tasks.json --root runs/check-1 --capacity 4
agenttime fixture run --campaign check-1
agenttime fixture status --campaign check-1
```

The schema is `agenttime_fixture`. Capacity is fixed when the schema is initialized; use a separate fixture database for a different capacity. The run command can restart after a controller crash. It discovers existing claims and workers. It never restarts a subject because an attempt is missing or ambiguous. Exit code 3 means work remains held or blocked, and the JSON report explains it. Source or runtime changes pause the campaign, including reconciliation under changed pins. Preserve its database, source snapshot and evidence. Do not edit a frozen manifest or clear reservations to force progress. Use a separate disposable database and campaign for revised code; audited migration and gate resume remain unimplemented.

## What recovery currently means

A healthy worker survives a controller restart. Reconciliation can recover a published completion without replaying the subject. An unavailable worker retains its reservation; unaffected tasks can continue when capacity remains. Shared evidence or manifest corruption pauses new admissions. There is no automatic clearing of that pause.

Lost-worker continuation and one permitted infrastructure replacement remain unqualified. They are protocol choices that still need implementation and native evidence. The current conservative holds are deliberate; they are not a complete recovery solution.

## Local Harbor boundary probe

With Docker Desktop running, install the optional pinned integration and run the opt-in tests:

```sh
uv sync --locked --group test --extra harbor
AGENTTIME_NATIVE_DOCKER=1 \
AGENTTIME_NATIVE_OUTPUT=runs/harbor-local-check \
LITELLM_LOCAL_MODEL_COST_MAP=True \
uv run --locked --group test --extra harbor python -m unittest discover -s tests -p test_harbor_native.py -v
```

Choose a new output directory. This runs two disposable subjects through Harbor 0.23.0: one completion and one crash after prompt release. Neither uses a model or account. The probe verifies one invocation, the native interval, terminal-bound submission, no experiment agent timeout, network isolation and removal of each exact container. It seals event hashes after Harbor finishes collecting and transforming its artifacts.

Harbor's local provider rejected its dynamic `no-network` policy on this Mac. The fixture instead supplies a Docker Compose `network_mode: none` override and inspects the actual container before releasing the prompt. This verifies Docker isolation for this fixture; it does not qualify Harbor's dynamic egress control.

The subject's short waits are artificial. Its natural interval is not a model measurement. Harbor's broader agent interval is retained as a reference, including time after the subject's native terminal marker. The probe uses a direct `Trial`, with no `Job` retry layer. It is separate from the PostgreSQL worker and does not qualify session continuation, provider behavior, host sleep or 220-way execution.

## Durable Harbor fixture

The `harbor-fixture` command uses the same PostgreSQL admission and permanent worker claims as the local fixture. It can run only four fixed model-free modes: `complete`, `wrong_answer`, `grade_failure` and `crash`. It accepts no study paths, model names or arbitrary commands. A task list can be `[{"id":"example","mode":"complete","work_seconds":0.15}]`; the bounded delay is artificial test work, not a natural-run budget.

```sh
uv sync --locked --group test --extra harbor
# Set AGENTTIME_DSN to a dedicated disposable PostgreSQL database.
uv run --locked --group test --extra harbor agenttime harbor-fixture init --campaign native-check --tasks harbor-tasks.json --root runs/native-check --capacity 2
uv run --locked --group test --extra harbor agenttime harbor-fixture run --campaign native-check
uv run --locked --group test --extra harbor agenttime harbor-fixture status --campaign native-check
```

Docker is checked before any permanent reservation. The exact same Docker socket, daemon ID and full container ID must support the stop proof; authoritative identity is retained outside subject-writable logs. A successful accounting finish requires verified archived evidence and resource removal. A crashed fixture may finish with unavailable native timing; it is never called a natural completion. An uncertain worker or launch retains capacity. Grader and archive failures never cause another subject invocation.

To repeat the combined fault suite, use a fresh evidence directory:

```sh
AGENTTIME_NATIVE_DOCKER=1 \
AGENTTIME_DURABLE_OUTPUT=runs/harbor-durable-check \
LITELLM_LOCAL_MODEL_COST_MAP=True \
uv run --locked --group test --extra harbor python -m unittest discover -s tests -p test_harbor_worker.py -v
```

The tests create their own disposable PostgreSQL instance. They kill only their owned test controllers/workers and clean up the exact recorded fixture containers. The evidence deliberately includes malformed/corrupt artifacts from negative tests. The [saved qualification](results/2026-10-09-durable-harbor.md) separates those tests from study readiness.

## Native qualification next

1. Qualify Claude Code through the chosen subscription, including actual model/effort, completion signals, tool waits, native subagents and account interruption. API qualification can follow separately.
2. Preserve native session state plus task state on the Mac, verify archive acknowledgement, and restore and fork on a fresh worker.
3. Add qualified continuation and the one permitted infrastructure replacement using typed external failure, exact stop proof and clock continuity. The current worker conservatively holds lost executions.
4. Qualify each selected task environment and grader, then test increasing capacity before the 200-task natural cohort.

Keep natural duration requests and time caps unset. Native qualification does not authorize the full study launch.
