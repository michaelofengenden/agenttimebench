**Assessment**

The architecture and fixture-first scope are right. But the spec leaves several meanings ambiguous, and those meanings would be built into PostgreSQL constraints. Some acceptance tests could also pass without proving their claims. Amend the spec, then build. No redesign is needed.

**Launch ambiguity**

1. "Launch" is undefined, and nothing separates a dispatch from a subject attempt. As written, flaky environments would either leave slots stuck or use up the one replacement.
   - Make the irrevocable boundary a single release-authorization compare-and-set (`UPDATE … WHERE token=$1 AND state='prepared'`) immediately before prompt delivery.
   - That transaction creates the subject-attempt row and enforces the original-plus-one bound.
   - Failures before it expose no subject, so redispatch is not a replacement. Bump the token, redispatch under a bounded and separately reported budget, and keep the orphaned dispatch's reservation counted.
   - After the transaction, the spec's no-relaunch rule stands.
2. "Fenced/stopped" conflates two facts. Fencing blocks releases and evidence admission. It does not stop a zombie process from spending money or mutating state. Only a typed stop proof should free a reservation or authorize a replacement.

**Measurement correctness**

3. Without a segment-close record (event count plus final digest), "missing" and "not yet delivered" look identical. A late interruption event could then invalidate an interval already reported. Evaluate only sealed, contiguous sequences. Define "out of order" as inconsistency between sequence and monotonic time, not arrival order.
4. Clock handling:
   - Emit both boundaries on the worker host, from the process that delivers the prompt and observes termination.
   - Bracket delivery with monotonic readings; the interval starts when delivery completes.
   - Where an adapter passes the prompt at spawn, agent startup falls inside the interval. Label it.
   - Clock identity is host, boot ID and clock source. Execution identity adds a per-process nonce.
   - Monotonic clocks can exclude host sleep, so divergence from UTC beyond a tolerance should make the interval unavailable.
5. The spec does not say where Harbor exposes prompt release and the native terminal event. If only after-the-fact trial timestamps exist, the primary interval becomes the forbidden wrapper measurement.
   - Make the fixture a custom Harbor agent.
   - Keep Harbor's agent interval as the labelled reference.
   - Confirm in the 0.23.0 source that one trial can run without job-level retry or concurrency code.
   - Record the effective Harbor agent timeout. If it fires, the outcome is censored.
6. Byte-identical idempotency needs stored bytes. Journal each event once with its SHA-256 and redeliver exactly those bytes. Make (segment, sequence) and event ID unique. The same key with a different hash is a quarantined conflict.
7. Reserve event kinds for provider throttling now. At 220-way concurrency, rate-limit waits can inflate M, so they must be reported as a covariate.

**Autonomous recovery**

8. "No second invocation" is currently checked only against the system's own ledger. Add an independent witness: the fixture appends to a per-attempt file outside the system under test. Assert:
   - one entry per release;
   - none for unreleased dispatches;
   - exactly one for a deliberately crashing trial, which proves Harbor retries are off.
9. Test a matrix of crash points, not a single kill:
   - controller killed at every named transition;
   - worker killed on either side of the release transaction;
   - PostgreSQL restarted;
   - evidence store failing mid-upload.

   Run workers outside the controller's process group. Reattach only when nonce, attempt and token all match. A new nonce means the worker restarted.
10. Under PostgreSQL's default READ COMMITTED isolation, concurrent admissions can both pass the global cap and the one-agent-at-a-time check. Run all admission checks in one transaction under a locked campaign row. Test with concurrent processes against real PostgreSQL.
11. Slot keys that include revisions let a revision bump create fresh slots and fresh replacement allowances. That is an unrecorded retry path. Add a revision-free lineage key. A new revision of an already-attempted lineage should require a recorded supersession, and reports should show every attempt.

**Acceptance gaps**

12. Add tests showing that:
    - an exit without a native terminal marker is not a natural finish;
    - lingering after the terminal event does not extend the interval;
    - harness and operator stops are recorded as censored;
    - an upload failure withholds teardown and retries the upload without rerunning the agent;
    - a background writer active after termination cannot change graded bytes, because grading uses the sealed snapshot.

    Also define the structural projection that criterion 7 compares.

**Unnecessary complexity**

13. Cut or defer:
    - Segment merging and cross-boot continuity: report multi-segment intervals as unavailable, and set `continuation_supported=false` in the manifest.
    - Provider permits: use one generic capacity pool until model work begins.
    - Lease authority: leases become advisory heartbeats. Authority comes only from the release transaction and stop proofs.
    - Multiple ledger writers: workers write their journals; only the coordinator writes the ledger.
14. Share a strict loader with `plan.py` that rejects duplicate JSON keys and NaN before hashing. Admission must itself refuse:
    - short and long arms while calibration is unset;
    - study dispatch while required policies are absent.

    Fixture campaigns must never become study data.

**Recommended first slice**

1. PostgreSQL schema and transition functions (campaign, slot, dispatch, subject attempt, reservation, stop proof, event, quarantine), plus a pure interval evaluator tested on synthetic histories.
2. A content-addressed local evidence store (temporary file, fsync, rename, verify). The ledger cites only verified digests.
3. A worker daemon with a durable journal, a nonce and gated release. It runs one Harbor trial of the model-free custom agent (sleep, write a file, emit a terminal marker, write the witness), then seals and verifies evidence before teardown.
4. A coordinator handling admission, reconciliation, ingestion and reattach, with named fault-injection points and the crash matrix.
5. A report rebuilt solely from the ledger and evidence store, with lockfile hashes, image digests and the effective manifest. Compare a second clean run structurally.

Excluded for now: continuation, provider permits, live-state adapters, timed arms and 220-way capacity.

**Decisions for you**

1. **Stop proof.** What proves an unreachable original attempt has stopped? I recommend provider-confirmed deletion of its execution environment plus revocation of a per-worker provider credential. Also: may an attempt that can never be proven stopped be closed as permanently unresolved, with no replacement, so the next agent configuration can start? Without this, one lost host can block the campaign indefinitely.
2. **Fault scope.** Within your isolate/stop policy, please confirm this three-tier mapping:
   - attempt-local: one conflicting duplicate or journal mismatch;
   - host quarantine: one host's clock anomaly;
   - shared stop: the ledger, evidence store, source, identity issuance, or the same fault on two hosts.
3. **Replacement causes.** Milestone 1 injects failure causes, so it tests only the ledger's enforcement of the replacement limit. Before study execution, approve a closed list of machine-verifiable external infrastructure causes. Unknown causes get no replacement.

VERDICT: REVISE FIRST BUILD
