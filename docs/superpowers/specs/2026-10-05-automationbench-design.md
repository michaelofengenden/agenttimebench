# AutomationBench in AgentTime v1.1

5 October 2026. The user authorized replacing all twelve τ³ tasks with a quality-driven AutomationBench sample and explicitly approved removing the native turn-budget instruction and response cap for natural runs. No sample count is forced. This document covers a runnable model-free adapter and an audited source selection, not campaign launch.

## Outcome

Run selected public AutomationBench tasks through the evaluated agent's actual harness. Reuse the benchmark's tools, initial world and grader, while keeping private state inaccessible to the agent. Preserve the same visible task text for the separate text-only forecast. Keep natural duration unset and measure voluntary completion with the existing AgentTime timing contract.

The public source is v1.0.6 at `4a8e1061254004d9dac807054eed33fad7d1ff14`. Archive SHA-256: `3e725336ef5a58dd67c70a81cde6d628317426641a49e90931a21cb1d2028f05`. The upstream lock resolves verifiers 0.2.1 and MCP 1.25.0 under Python 3.13. Retain the upstream source without edits; the AgentTime adaptation is separately versioned. Record a file manifest and archive receipt.

## Task admission

Start with one substantive candidate in each of the six business domains. Exclude the 108 tasks reported in issue 28 and other applicable unresolved task/tool/grader defects at the exact source pin. Additional exclusions are based on actual code and tool mode; do not treat a limited_zapier-only defect as necessarily affecting API mode.

For every selected task, retain the original task contract hash, source prompt, adapted prompt hash, native initial state, tool mode, applicable issue screen and model-free witness. Require a discoverable valid action sequence through native API tools, baseline/no-op grading, a deliberately incomplete or incorrect outcome and relevant guardrail checks. A synthetic final state alone does not prove tool reachability. Lack of reported issues never establishes correctness. Failed candidates remain outside the roster; no replacement is forced.

Use the labels `selected_pending_native_qualification`, `model_free_checks_passed` and `study_launch_ready=false` accurately. Model-free witnesses must not be counted as evaluated agent runs or empirical difficulty estimates. Keep assertion details and witness actions in private qualification evidence, not the public review page or subject workspace.

## Harness contract

Use `toolset="api"` explicitly, matching the benchmark CLI default. Expose exactly `api_search`, `api_fetch` and `base64_encode`, retaining native argument schemas and result strings. The benchmark's provider loop is not used. Harbor 0.23.0 already supports task MCP configuration in its Codex and Claude Code adapters; actual model/CLI configurations remain separately unqualified.

A private per-attempt backend loads exactly one task. Reuse native normalization, initial-world creation, allowed-service calculation and argument normalization. Keep assertions, source files and raw WorldState private. The agent receives its adapted task prompt and MCP tool connection only. No task-selector, world-selector, arbitrary Python, filesystem access or grading method is exposed through MCP. The service has no provider credentials and makes no model calls.

The service serializes calls affecting its world. A request identity is the tuple of controller-owned attempt ID, transport-session epoch and actual MCP protocol request ID, obtained from request context without adding any native tool argument. Bind tool name and canonical arguments to its private receipt, alongside sequence, result and resulting world revision. Repeating the full identity and payload returns its prior receipt without reapplying a mutation; a conflicting tool or payload is rejected. A new transport epoch is a new invocation, not a safe replay. Actual transport tests must exercise these cases, including colliding numeric IDs in distinct sessions.

Native exceptions can occur after a partial mutation. Preserve the failed outcome and changed world revision in private evidence and fence the backend against further calls or resumable checkpoints until controller reconciliation. Never silently roll back effects or return a retriable error and continue with unjournaled state. A duplicate failed request returns the same sanitized failure without re-execution. Sealing can preserve an unavailable attempt for diagnosis; it cannot turn an unresolved mutation into a valid grade or resumable checkpoint.

Control operations are separate from the agent-facing MCP connection. Use a controller-private local channel or an in-process owner API for release, seal, export and grade. Do not publish control methods as tools or rely on a secret stored in the agent container. A deployable sidecar needs its own private filesystem and no agent-shared world/grader volume or Docker socket.

## Natural prompt and measurement

Remove only the native instruction assigning approximately fifty tool-using turns. Fail if its expected versioned text is absent or appears ambiguously. Preserve all business objectives, policy constraints and available-tool instructions. Do not impose the corresponding response cap or an experimental wall-clock cap. Store the original and adapted texts and hashes. Label the variant as an AgentTime adaptation rather than claiming official leaderboard parity.

Forecasts use the adapted visible task text in the existing forecast wrapper. They do not access the backend, task state, grader or prior natural session. A fresh natural session is prepared independently, with no forecast record, marker, estimate or shared writable memory.

Preparing the world and MCP service is setup. The task runtime starts at verified prompt release and ends at verified native harness termination, including tools and waiting while active. A model-free release/seal receipt is not native timing evidence. Do not use outer process duration or time of the last tool call as M.

## Freeze, grade and restoration

On controller seal, prevent new tool operations, wait for an already accepted operation to commit, and capture an immutable world revision. Record the seal boundary and outstanding-call state. Do not assert exact native-terminal capture if the real harness cannot establish when sealing should occur; that route remains unqualified.

Retain three baseline artifacts: the raw factory fixture, normalized native input and the fully constructed initial WorldState after allowed-service assignment and before any tool call. Use that actual initial world, including generated IDs and timestamps, for free-assertion classification. Version this as `constructed_initial_world_v1` in the adaptation manifest. Compare each candidate's assertion outcomes, exclusions and denominator with upstream native grading; unexpected disagreement blocks qualification. Preserve raw fixture evidence even when normalization changes its representation.

Grade a copy of sealed state with the pinned native partial-credit and all-pass functions and strict assertion exceptions. Preserve both values, assertion counts, native exclusions and the reason each assertion was excluded (authored exclusion or initially satisfied). A missing or failed grader returns an unavailable status, never an invented zero. Do not silently adopt Artificial Analysis's distinct guardrail-zero scoring rule.

WorldState JSON is not sufficient. A versioned runtime-state sidecar preserves presence/absence and the typed contents of `world.google_sheets._updated_row_keys` (sorted strings restored as a set) and `world.google_ads._offline_jobs` (JSON job records). Recursively reject undeclared mutable instance extras, except this exact allowlist and Pydantic bookkeeping. Sealed copies and restore must preserve grade and tool behavior. Reject nested private/dunder property injection in API JSON bodies, not just unknown top-level MCP arguments; do not allow those inputs to alter serialization machinery or runtime sidecars.

An owned checkpoint records initial state, current world, task/variant identity, committed journal and seal state. Restore rejects wrong task or source identity, tampered bytes and incomplete/pending mutation state. This qualifies backend restoration only. Full native agent-session restoration, independent Mac archives and production crash recovery retain their existing pending status.

## Deliverables and boundaries

- Pinned source receipt, issue screen, private task controls and selected manifest.
- Dependency-light prompt/manifest helpers in `src/agenttime/automationbench/`.
- Native backend and a thin MCP service, with reproducible offline tests in an isolated Python 3.13 environment.
- Harbor task configuration/materialization for the same MCP tool surface; no provider loop or synthetic native timing claims.
- Updated suite allocation, source inventory and review page. τ³ sources remain archived but its selected count becomes zero.
- An explicit qualification report naming what was exercised and what still needs a real agent pilot.

Do not wire this first adapter into automatic campaign dispatch, select model accounts, launch subjects, or claim 220-way capacity. Existing production controller integration and archive qualification are outside this benchmark addition and remain visible as required work.

## Verification

Test real native tool/schema/result parity; successful and incorrect candidate witnesses; nested private-field injection and unknown tools; isolation between concurrent attempts; duplicate request handling through the MCP transport; mutation/seal races; partial mutation followed by exception; late-write rejection; strict grader failures; checkpoint integrity and restore; omitted generated baseline fields; real Sheet mutation markers across grade/seal/restore; Ads job use after restore; exact prompt adaptation and unchanged business text; independent forecast/natural inputs; and Harbor MCP config for both supported harnesses. Run relevant existing planner/inventory tests and verify source hashes after integration.
