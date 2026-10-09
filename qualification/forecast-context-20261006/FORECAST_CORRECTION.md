# Correcting the 200 forecasts

6 October 2026. Draft for review. The subscription route is Michael's choice. This note records local qualification results and the proposed correction; it does not change the active protocol or authorize a study dispatch.

We can fix the missing inputs and unclear scope. A clean experiment means every request passes the same input and isolation checks before dispatch. It cannot guarantee that every model reply interprets the task correctly or returns a usable number. Keep those outcomes visible.

The existing batch stays intact. The correction gets a new protocol version and a new batch containing one forecast attempt for each of the 200 selected slots. All slots use the corrected protocol, including those without an old review flag. The six replies that mentioned the Mac do not establish that the other replies were unaffected.

| Problem | Correction |
| --- | --- |
| Four YC forecasts estimated the opening commands | Include the native role, complete objective, rendered initial public state and one-year-or-bankruptcy endpoint. Ask for elapsed time through the entire assignment. |
| Eighteen ProgramBench prompts omitted the particular program | Include each program's subject-visible specification, documentation and asset manifest. Keep implementation source and hidden tests private. |
| Ten PPTArena prompts omitted the particular edit | Include the exact edit instructions and original deck previews. Retain the structural information needed for animation and layout requests. One original deck has no extractable text. |
| Four PaperBench prompts omitted the paper | Include the actual paper and allowed addenda. Resolve the conflicting work-duration instructions in both the natural task and its forecast copy. Preserve evaluation requirements and distinguish grading limits from work limits. |
| Four HLE questions omitted an image | Deliver the existing question images as image content, alongside the exact questions. |
| The forecast host differs from the execution host | Use a frozen execution profile. Preferred: forecast in a clean copy of the benchmark environment. Alternative awaiting Michael's choice: forecast on the Mac with an explicit description of the target environment. |
| Potential problems outside the old flags | Audit all 200 slots against their native initial messages, referenced materials and completion rule. |

The source material for the corrections above is mostly local. The inventory still marks every execution environment as needing checking, and it has not frozen the final natural messages. Source files alone do not settle GPU access, tool access, initial state or runtime behavior. The attached `preflight-audit.json` records all 200 slots and their current gaps.

## What the subscription qualification established

These are synthetic local tests using a fake token and a fake API. They used no model quota or account credentials. The tested executable was Claude Code 2.1.280; the capture reports pin its binary hash.

| Check | Observed result |
| --- | --- |
| Empty custom system prompt | Claude Code still supplies the Mac environment note. |
| Disable attachments | The environment note remains; the token-budget reminder disappears. |
| Explicit image through stream input | The local endpoint receives the exact image bytes. The native transcript retains the image. |
| The four actual omitted HLE images, using synthetic prompts | All four reach the local endpoint. Three retain identical pixels. Claude Code converts hle-03 from PNG to JPEG at the same dimensions. Record the actual delivered representation and qualify the natural adapter's image handling too. |
| Request-body logging | It preserves all transmitted body fields in this test; it also records `betas`, which the client sends as headers. A saved body is not proof of dispatch or provider acceptance. |
| Native session saving | The successful image probe saves a native transcript directly. |
| Retry count set to zero | A simulated HTTP 529 produces one request and an error, with no retry. This qualifies that failure case only. |
| Private runtime directory | The session opens its messaging socket inside its own short temporary directory. This prevents use of the default shared socket namespace; it is not a security boundary against other processes with the same user ID. |
| Bare mode with the fake subscription credential | It reports `Not logged in` and sends no message request. It is not a qualified subscription solution. |

The current CLI's `success` subtype can coexist with `is_error: true` and a nonzero process exit. The adapter must check the complete result, not that subtype alone.

Anthropic documents [conversation reminders and raw request logging](https://code.claude.com/docs/en/agent-sdk/modifying-system-prompts). The local captures establish the behavior of the installed version. They do not establish live subscription authentication, model availability or successful visual understanding.

## Proposed input contract

Freeze one task record before either session begins. It contains the source revision and seed; the exact natural messages; the public initial observation; explicitly allowed subject materials; the completion rule; and the intended agent and execution profile. Pin the OS, CPU and memory allocation, GPU model and count where applicable, installed tools, network access, model route, effort, tool permissions and subagent policy. Separate worker resources from grader resources.

The forecast copy adds only the estimation wrapper. The natural copy contains no forecast question, answer, forecast transcript or forecast memory. Keep forecasts out of dispatch priority, resource allocation and the later duration calculation.

The proposed wrapper is:

```text
Estimate the real elapsed time you would spend on the complete assignment
below in a fresh natural run with the specified execution setup. The run
has no requested work duration or experiment-imposed time limit.

Measure from receiving the complete assignment until your natural run
ends under the stated completion rule. Include time spent using tools and
waiting for work while the attempt is active. Concurrent work counts once
as elapsed time. Exclude environment provisioning, queueing, authentication,
external grading and cleanup. A simulation's calendar horizon is part of
the assignment, not the elapsed time requested here.

Forecast the full assignment, rather than just its opening steps. Do not
perform the assignment or call tools during this forecast.

Execution setup:
{frozen_execution_profile}

Assignment and completion rule:
{frozen_native_messages_and_endpoint}

Initial task materials:
{allowlisted_materials_and_attached_images}

Return exactly one line: minutes = <positive number>
```

This is a proposed new protocol, not a replication of the paper's text-only API condition. Its target is the duration of the natural attempt, with completion quality recorded separately. Open-ended PostTrainBench retains its open-ended objective; the forecast does not supply a stopping budget.

Do not manufacture differences between seed variants. If two YC seeds genuinely have the same public starting information, record them as repeated scenarios with shared forecast information. Do not expose future events, hidden client properties, answers, grading rubrics, reference submissions or private evaluator inputs to distinguish them.

Large code repositories and executable binaries are not silently substituted with model-written summaries. Define the allowed initial information per family before preparing the batch. A manifest may describe binary assets, but a manifest cannot substitute for a question image, the requested deck edit or the program specification. Record intentional limits of the information policy. Oversized or incomplete required inputs block a slot; do not truncate them silently.

## Isolation and accounting

Build forecast and natural state independently from the same immutable task source. Use different homes, settings, session IDs, temporary folders, memory stores and writable workspaces. Disable forecast tools, hooks, plugins, skills and MCP connections. Store actual request bodies and native transcripts in controller-only archives. Natural workers must not be able to reach those archives or forecast scratch state.

Before a live batch, qualify the actual subscription invocation and prove that a synthetic forecast marker placed in response, logs and memory cannot appear in the natural request or accessible state. Changing the estimate while holding the task fixed must leave the natural input and allocation unchanged. The Mac-only fake tests do not establish this worker boundary.

Persist a dispatch claim before invocation. Pin the model and effort, prohibit fallback, and qualify transport failures with automatic retries disabled. Missing receipts remain unresolved until reconciled; they do not authorize another call. Apply the existing explicit infrastructure-replacement rules where relevant. Never re-ask because an estimate is inconvenient, incomplete, refused or misinterpreted.

Classify process exit, error flag, finish reason, model identity, actual request, complete input delivery and numeric parsing separately. Preserve every outcome in the 200-slot denominator. A future interpretation concern remains an annotation and is not erased to meet a target of zero flags.

## Work remaining

1. Choose the host strategy and pin the intended execution profiles. Do not fill missing hardware values with guesses.
2. Assemble and review the 200 input packets. Freeze matching natural-message hashes and subject-material hashes. The available source inventory is the starting point, not a dispatch manifest.
3. Implement the selected correction in a versioned adapter using the existing evidence/accounting interfaces where qualified. Keep the old batch immutable.
4. Test incomplete images, generic packets missing required materials, answer leakage, context overflow, cross-session markers, lost receipts, errors disguised as successful results and retry behavior. Complete one non-study subscription qualification before any study forecast.
5. Run the new 200-slot batch once its preflight passes. Save original native sessions as they run. Pair forecasts later with independent natural measurements; estimates never become M.

The next decision is the host strategy. No corrected study forecasts have run. The independent Codex reviewer failed during startup because it could not initialize its local state database; this draft has a self-review only.
