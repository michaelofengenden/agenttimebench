# Independent AutomationBench plan review

Reviewed the staged specification and implementation plan on 2026-10-05 against upstream `4a8e1061254004d9dac807054eed33fad7d1ff14`. This is a plan review for standalone adapter qualification and roster changes. No model calls, repo edits or launch qualification were performed.

The approach is sound: retain the upstream source and tools, isolate the private backend, use the actual evaluated harness, label the uncapped prompt as an adaptation, and separate model-free evidence from live qualification. The two user choices are explicit and need no further approval. Four state/protocol requirements need tightening before implementation acceptance.

## P1: Specify the actual initialized baseline and scoring parity

**Location:** spec lines 15, 23, 41-43; plan lines 23, 39-42.

“Native initial state” is ambiguous. Upstream `runner.py:238-248` constructs WorldState and assigns allowed services but stores the raw fixture as `initial_state`. The grader reconstructs that raw fixture at `rubric/__init__.py:75-78`, rerunning defaults. Merely calling the native setup and grader therefore retains issue 19.

**Required change:** retain three distinct artifacts: the raw factory fixture, the normalized input dict, and the fully constructed initial WorldState captured after allowed-service assignment and before the first tool call. Seal and restore use the constructed snapshot, including its generated identifiers, timestamps and runtime sidecar. Pass that exact baseline to native assertion evaluation. Version this baseline treatment in the adaptation manifest. Compare every assertion's initial/final result, exclusion status and denominator against native grading for each candidate witness. An unexpected difference fails qualification; do not silently adopt changed scores under a native-parity label.

**Acceptance:** a fixture with omitted generated identifiers/timestamps keeps exactly the same initial object identity values across grade/copy/restore. Repeated grading cannot change the denominator. Preserve the raw fixture for audit rather than replacing its evidence with the normalized snapshot.

## P1: Serialize grader-relevant runtime state explicitly

**Location:** spec lines 39-43; plan lines 38-42.

WorldState's declared JSON fields are not a complete checkpoint. Native Sheets writes attach `_updated_row_keys` outside the schema and graders consume it. A `model_dump`/revalidate copy drops this history and can turn a write into an apparent no-op. The current source also keeps Google Ads offline jobs outside declared fields, so ordinary tools can behave differently after restore even when displayed world JSON matches.

**Required change:** add a typed, versioned runtime-state sidecar, allowlisted by exact object path:

| Path | Serialization and native evidence |
| --- | --- |
| `world.google_sheets._updated_row_keys` | Preserve presence plus a sorted string list; restore as a set. Created at `tools/api/impl/google_sheets.py:1157`, read by `_was_row_updated` at line 1166 and by sheet update/negative guards. |
| `world.google_ads._offline_jobs` | Preserve presence plus validated JSON job records. Created at `tools/api/impl/google_ads.py:292`, consumed by add-operations at line 313 and run at line 413. |

An exhaustive AST scan of all Python files at the pin found these two explicit WorldState runtime-only attachments. Other underscore assignments concern runner, metadata, registry, batch or pricing objects. Other dynamic `setattr` calls occur in HubSpot, QuickBooks, Salesforce account tools, and the Zapier folder tool. They are not an additional approved snapshot field list. Recursively reject unknown mutable instance attributes outside declared schema fields and this sidecar, accounting separately for Pydantic's own internal bookkeeping. Do not silently omit unknown attributes or serialize arbitrary object internals.

**Acceptance:** update a real sheet through the native API, then compare every assertion and denominator before seal, after sealed copying, and after checkpoint restore. Create an Ads job, restore, then prove the same job remains usable. Test unknown runtime extras and private/dunder keys nested inside API JSON bodies: HubSpot's `hasattr`/`setattr` branch makes top-level MCP-key tests insufficient. Unknown mutable extra state must reject qualification/export rather than disappearing.

## P1: Define failed-call mutation and journal semantics

**Location:** spec lines 25 and 39-43; plan lines 38-42.

Serial execution alone does not ensure that a failed native call left the world unchanged. For example, `gmail_messages_modify` appends labels before iterating `removeLabelIds` (`tools/api/impl/gmail.py:541-551`); a malformed later field can raise after an earlier mutation. A journal containing only successful receipts then omits committed effects, and retry or restore can apply a different history.

**Required change:** specify how a call that raises after mutation is recorded and fenced. Either preserve the exact failed-call outcome and changed revision in the journal, or mark the attempt unavailable and reject further operations until controller reconciliation. Never return an ordinary retriable transport error while continuing on an unjournaled changed world. Do not silently roll back native effects while claiming native state parity. A checkpoint containing an unresolved outcome remains unrestorable, as already proposed.

**Acceptance:** inject an exception after the first real state mutation. Assert no duplicate reapplication under the same identity, no unrecorded mutation in an accepted checkpoint, and an explicit unavailable/reconciled outcome. Exercise this together with seal, not only as an isolated exception test.

## P2: Bind request identity to attempt and transport session

**Location:** spec line 25; plan lines 38-40.

The design promises duplicate suppression but does not define request identity or how the MCP handler obtains it. JSON-RPC IDs alone can collide between client sessions and process restarts; adding an agent-controlled idempotency tool argument would also change the native schemas.

**Required change:** define the controller-owned identity tuple, such as attempt ID, transport-session epoch and the actual protocol request ID. Preserve it across the real model-free MCP call path and checkpointed receipts. Explicitly state that a new transport epoch/new request is not a safe replay. Bind the tool name plus canonical arguments to the receipt. This needs no additional native tool parameter and no production exactly-once claim.

**Acceptance:** send the same protocol request twice and verify one mutation, then reuse its numeric ID in a different transport session and verify it is treated according to the documented new-session rule. A conflicting tool name or payload under the same full identity must fail without mutation. Test the actual MCP server, not just a backend method passed a synthetic ID.

These changes can remain within the existing standalone scope. No campaign dispatcher, model account, general benchmark fixes or new launch authority is needed.

VERDICT: REVISE
