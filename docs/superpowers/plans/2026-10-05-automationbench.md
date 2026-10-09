# AutomationBench Implementation Plan

> Implement with scoped subagents and independent review. Preserve the existing dirty repository and do not commit or push.

**Goal:** Replace τ³ with audited AutomationBench candidates and provide a model-free-qualified native-harness tool adapter.

**Architecture:** Private native benchmark backend, thin MCP connection to Harbor's existing harness adapters, immutable state grading, and explicit separation of preparation from native qualification.

**Tech stack:** Python 3.13 for the pinned benchmark, upstream frozen dependencies, MCP 1.25.0, Harbor 0.23.0. The existing core remains Python 3.12 compatible by keeping optional benchmark imports lazy.

**Spec:** `docs/superpowers/specs/2026-10-05-automationbench-design.md`.

## Constraints

- Source commit `4a8e1061254004d9dac807054eed33fad7d1ff14`, toolset `api`, no benchmark provider loop.
- The user approved removing the turn-budget instruction and response cap. No experimental natural timeout.
- No live subject calls, account changes, shared memory, grader exposure, silent retries or assertion fixes.
- Stage changes separately; install only after comparing original file hashes. Do not overwrite concurrent edits.
- Preserve historical task sources and records; changes affect the selected v1.1 allocation only.

## Review focus

Private information through exception text or MCP metadata; duplicated mutations after transport errors; concurrent calls crossing the seal boundary; default_factory differences on restore/grading; source or prompt drift silently accepted as the same experiment. These conditions receive explicit tests before acceptance.

## Task 1: Source and candidate qualification

Root owns source receipt, inventory and selection integration. Task-audit worker owns private controls. Issue-audit worker owns report/exclusion evidence.

- [ ] Download and hash the public pinned archive and preserve upstream issue records.
- [ ] Screen the complete 600-task public pool, identifying conditional tool-mode and handler risks separately.
- [ ] Demonstrate reachable positive and meaningful negative cases for every proposed task using native API tools; retain commands and results privately.
- [ ] Record exact selected IDs and reasons without model-result-based selection. Exclude candidates whose checks fail.

## Task 2: Backend, prompt adaptation and MCP transport

Backend executor owns only `src/agenttime/automationbench/`, `tests/test_automationbench*.py`, and `qualification/automationbench/` in the staged repository. Root owns roster, inventory and docs.

- [ ] Add failing tests for prompt mutation limits, private-field rejection, isolated world changes, duplicate/conflicting request IDs, sealing, grading failures and checkpoint integrity.
- [ ] Implement the smallest native backend that satisfies those cases. Use the pinned environment's setup and tools, with strict grading and no provider loop.
- [ ] Add a thin MCP service exposing only the three native tools. Verify the actual exported schema and a real model-free client call.
- [ ] Prove native and bridged tools produce equal responses and resulting state on representative candidate operations.
- [ ] Add checkpoint/restore tests. Report backend restoration separately from native agent-session restoration.
- [ ] Preserve raw, normalized and constructed initial state separately. Test stable generated IDs/timestamps and native per-assertion/denominator parity.
- [ ] Add the typed runtime sidecar for Sheets update markers and Ads offline jobs, testing real native API mutations and post-restore grade/tool equivalence. Reject unknown extras and nested private-key injection.
- [ ] Fence exceptions after partial mutations, preserving a failed receipt and changed revision. Test duplicate failed requests, seal and restore rejection together.
- [ ] Derive request identity from attempt, transport epoch and actual MCP request ID. Test true transport duplicates, conflicting payloads and same ID on distinct sessions without adding tool arguments.

## Task 3: Harbor configuration and suite integration

- [ ] Materialize public task instructions and task MCP configuration without private source/state mounts.
- [ ] Validate configuration with Harbor 0.23.0 for Codex and Claude Code. Reject unsupported/unqualified routes rather than falling back to API calls.
- [ ] Update the selected suite: τ³ count zero, AutomationBench count equal to the qualified candidate list, all native qualification flags false.
- [ ] Extend inventory building to the new family and ignore historical τ³ acquisition updates only after confirming its explicit exclusion.
- [ ] Retain prior slot identity evidence and produce a dated selection transition receipt. Rebuild the review page with clear selected/prepared/qualified counts and task reasons.
- [ ] Update current task counts in README, BUILD_PLAN, TASK_MIX and inventory documentation. Historical dated documents retain their original counts and gain a short supersession note where needed.

## Task 4: Review and verification

- [ ] Run candidate controls, adapter/MCP tests, existing core tests appropriate to the change, and inventory/hash/privacy checks.
- [ ] Independent code and scientific-contract review; fix material findings and rerun affected checks.
- [ ] Install the guarded changes and show the updated task inventory.
- [ ] Report exact task IDs, evidence, measured test results and remaining native route/controller/archive qualification. Do not call model-free controls a live pilot.
