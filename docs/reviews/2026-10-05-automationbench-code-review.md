# Independent adversarial code review

Reviewed the staged inventory changes against the existing AgentTime-v1.1 repository and reviewed `src/agenttime/automationbench/` plus its tests. No implementation edits, evaluated-model calls, provider/account operations or campaign launches were performed.

## P1: Restored world is not bound to the committed receipt chain

**Location:** `src/agenttime/automationbench/backend.py:308-322` and `:354-362` in the formatted review snapshot, SHA-256 `161733c986ea492ed8fd83af1a930a625387215905c300e775d4f7425d480f3b`.

Restore validates the envelope digest and matches commit-event hashes with receipt hashes, but never checks that a receipt's `before_sha256` equals the previous committed state, that the first receipt starts from the constructed initial state, or that `state['current']` equals the last committed state. Thus its accepted checkpoint can contain a different world from the history that supposedly produced it.

**Reproduced offline:** create a backend for `sales.cross_reference_validation`, release it, and commit one `base64_encode` request. Decode its checkpoint, change an existing Salesforce opportunity amount in `payload.current.world`, then recompute the envelope's existing payload digest. `Backend.restore` accepts it even though `digest(restored.owner_state()['current'])` differs from the final receipt's `after_sha256`. Recomputing the envelope simulates an internally inconsistent assembled checkpoint; this is a missing structural invariant, not a claim that an unkeyed checksum provides hostile-tamper authentication.

**Fix:** verify the state-hash chain from the constructed initial snapshot, across every receipt, to the current snapshot; validate hash shapes and reject state changes without a corresponding committed receipt. For a zero-call checkpoint, current state must equal the constructed initial snapshot. Preserve the existing sealed/current equivalence check. Add regressions for a modified final world, a broken intermediate link and a modified zero-call world.

## P2: Checkpoint continuation assumes one machine's monotonic clock forever

**Location:** `backend.py:69` and `:329-336`, same review snapshot.

Restoring a checkpoint preserves historical monotonic timestamps. New events then use the current process host's clock, but subsequent restore compares every timestamp globally as one increasing sequence. Moving the backend to a different host, or resuming after reboot, can therefore make a legitimate continued checkpoint unrestorable.

**Reproduced offline:** checkpoint and restore an active backend; patch the new process clock to return `1`; perform another valid tool call and checkpoint again. The first restoration and continued call succeed, but restoring the new checkpoint raises `Checkpoint integrity or identity rejected`.

**Fix:** record a distinct controller clock epoch for resumed processes, preserve the old events unchanged, and validate monotonic order only within an epoch while preserving global journal sequence order. An explicit restoration boundary must distinguish a legitimate clock reset from reordered events. These controller events remain outside scientific runtime qualification. Test continued operation, a second checkpoint/restore and seal on a lower new-host clock.

## P2: Unsupported checkpoint payload schemas are accepted

**Location:** `backend.py:283-306`, same review snapshot.

`owner_state` emits payload `schema: 1`, but restore does not check it. A payload marked with an unsupported schema and a recomputed envelope digest can be interpreted as the current format. This is unsafe for permanent versioned archives and future migrations even with matching source and prompt pins.

**Fix:** reject any non-integer or unsupported payload schema before consuming its fields. Test an unknown numeric version, a boolean and a missing version. Keep runtime-sidecar schema validation separate, as it already is.

## Concurrently identified seal-checkpoint race

The root reported that the executor independently identified and is repairing checkpoint export while seal has closed admission but has not captured the sealed snapshot. In that interval the current code can label an export resumable although its `closing` flag lacks the required seal event. This is not counted as an additional independent discovery here. The repair and its race regression should be included in the bounded re-review.

## Inventory and scientific boundary assessment

No material new defect was found in the reviewed inventory changes. The transition's 212-row before map exactly matches the existing repository's frozen identities. It removes the twelve exact τ³ identities, retains every unrelated slot and adds one new AutomationBench identity without reusing retired slots. The six selection-transition tests passed. The selected private task record's ID and canonical SHA-256 match the candidate reference. The current total is 201; public wording describes only scoped model-free evidence and keeps native admission false.

The report renders strings via text nodes and embeds escaped JSON. The AutomationBench addition exposes task identifiers, source hashes and review notes rather than initial world, assertions or witness actions. Expected not-yet-installed audit cache files were not classified as defects. Complete rebuilt-inventory hash/privacy verification remains the root's integration check.

The native adapter now contains the approved initial-world snapshot, typed runtime sidecar, exception fencing and actual MCP identity flow. The transport tests include real memory-protocol and HTTP requests, including a mutating duplicate. Those positives do not waive the restore defects above or establish native agent, timing, network isolation, archive or campaign readiness.

## Review snapshot and verification limits

- The root-owned inventory files retained their hashes throughout this review.
- Adapter code and tests were formatted while review was underway. The Harbor test file also gained pinned-Harbor configuration checks. Findings above were re-located in the current formatted backend and remain present there.
- Offline evidence: six inventory transition tests passed; both adversarial restore probes reproduced the described failures. I did not run the full adapter suite while implementation files were changing.
- Re-review the fixed, frozen hashes and regression results before approval. This review does not authorize a production launch.

VERDICT: REVISE
