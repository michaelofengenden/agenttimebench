# Forecasts in matching benchmark environments

6 October 2026. Michael selected matching benchmark environments and the Claude subscription. This decision replaces the text-only API route for the corrected Opus batch. The older batch and its review flags remain unchanged. This document records the selected design; the current work is forecasting only. No natural run or GPU provisioning is authorized by a forecast manifest.

For each task, freeze a pinned, pristine source and its initialization recipe. Create the forecast copy from it and the natural copy independently when actual execution is ready. Keep the same task revision, native objective, permitted initial information and benchmark software context. Each copy has its own writable storage, session and memory. The natural copy is never restored from the forecast copy.

The forecast runs through the pinned native Claude Code executable inside the matching environment. It uses the subscription route and maximum qualified effort. Forecast tools, plugins, hooks, skills, MCP connections and memory are disabled. The packet describes the tools and subagent policy that the later natural agent will actually have. Record the forecast restriction separately from that natural-run policy.

Claude Code supplies an environment note even with a custom system prompt. Running in the matching software environment avoids presenting the personal Mac as the task machine. Michael explicitly selected GPU provisioning only for actual task execution: tool-disabled forecasts describe the intended GPU without renting one. Record the forecast process's actual hardware separately from the target task profile. RunPod and Modal are the selected GPU provider candidates; no provider has been provisioned. Local emulation can test context and isolation, but does not qualify native x86 performance. Verify the actual task hardware before a natural run. A described H100 is not evidence of an attached H100.

## Selected resource and subagent defaults

Michael selected 4 CPU cores and 16 GiB RAM for ordinary code and browser tasks with no published subject allocation. Published task requirements take precedence. GPU, desktop and research tasks need a separate profile. Storage, host class, architecture, network and installed dependencies still need explicit values and worker verification. This policy does not qualify an environment by itself.

Allow the harness's native subagents where the benchmark permits them. Preserve native restrictions and review unspecified permissions before dispatch. Freeze the actual exposed tools and delegation configuration, describe them in the forecast, and retain available child-session evidence. Count elapsed time including owned work and waits once; do not add child durations to wall-clock time. Forecast sessions still have no tools or subagents.

## What every pair must share

The controller freezes a common task-and-target record before forecasting. Bind both attempts to its hash, while recording actual forecast resources separately. Record:

- The task identity, upstream revision and seed, the exact natural messages and the task's completion rule.
- The initialized subject workspace, public initial observations and allowed materials. Keep private simulation state and evaluator inputs behind their native access boundary.
- Immutable container or VM identity where materialized, operating system and architecture; intended natural-run CPU allocation and host class, memory, storage and GPU; required services and network policy. Mark unresolved image or profile values explicitly and resolve the input-relevant fields before forecasting.
- The model route and observed identity, CLI version and executable hash, effort, natural tools and subagent policy.
- The forecast launch configuration, delivered input receipt and software-context observation. Before natural execution, verify the actual worker allocation, runtime image and initial state against the frozen target. Source declarations and cached images are preparation evidence only.

Instance IDs and private writable locations differ intentionally. Preserve the benchmark's expected paths inside each independent environment where possible. Freeze semantic initial state rather than requiring unrelated session IDs to be equal. Verify task inputs and public observations, not just equal directory names or image tags.

## Corrected forecast input

Use the full task objective and completion rule. Include the subject-facing system instructions when they define the assignment, such as YC's business objective and simulation horizon. Include ProgramBench's actual specification, PPTArena's requested edit and original deck previews, PaperBench's paper and allowed addenda, and HLE's required images. Define each family's information policy before the batch. Do not silently shorten required materials to fit a context window.

The four YC scenarios may legitimately share public initial information. Keep that fact visible; never reveal future simulation events or private client properties to make prompts unique. The forecaster must estimate the whole simulation assignment, not its five opening commands.

Resolve natural-work duration instructions in the task adapter before copying the natural messages into the forecast packet. Preserve task-defining endpoints and evaluation requirements. A grader timeout is not a requested work duration. PostTrainBench remains an open-ended improvement task without a forecast-imposed stopping rule.

The wrapper in `configs/forecast-prompt.txt` is a draft until the common task records and measurement contracts are frozen. The forecast asks for elapsed time through the natural attempt's completion boundary. Tool waits and concurrent work count as elapsed time while the attempt is active. Controller-side provisioning, queueing, authentication, external grading and cleanup are separate. Setup, dependency installation and data or model downloads performed by the subject during the assignment count as task time. Forecasting does not run or solve the task.

This is a new condition, not an exact replication of the paper's text-only API forecasts. Include the condition and input hashes in every result and comparison.

## Isolation and evidence

Freeze the original source and independent initialization recipe before any forecast result is available. The natural copy may be created later from that source; it must never consume forecast state or outputs. Never share writable homes, session stores, task volumes, browser profiles or simulator databases between the two. Do not mount the controller's forecast archive in a natural worker. Provision only the required authentication through the qualified native route, without copying the user's personal settings, memory or conversation store.

Save the actual outgoing request body, its hash, CLI initialization, native transcript, response, finish reason and usage in a private controller archive. The request-body log alone does not establish provider acceptance. Preserve transport receipts and distinguish prepared, dispatched, accepted, completed, archived and parsed states.

Test isolation by placing unique markers in a synthetic forecast's response, logs, workspace and memory. Confirm that the natural copy cannot access those markers. Changing a forecast value must leave natural messages, resources and dispatch policy unchanged. A new session ID alone is insufficient.

Persist a claim before dispatch. Disable and qualify automatic retries; prohibit model fallback. Keep unresolved outcomes until they can be reconciled. Use the existing infrastructure-replacement policy when applicable. Refusals, malformed replies, truncations and mistaken interpretations remain recorded outcomes. Do not repeat them to obtain 200 acceptable numbers.

## Qualification and current state

The local fake-API [qualification](../../../qualification/forecast-context-20261006/README.md) established image transport, native transcript saving, raw-body capture and no retry for a simulated HTTP 529. It also established that disabling attachments leaves the environment note. Those tests used no real account credentials or model calls.

The [environment requirements audit](../../../qualification/forecast-environments-20261006/README.md) records all 200 slots, native declarations and relevant local image metadata. It does not select undeclared hardware values or mark any task ready to dispatch.

Before a corrected study request, the adapter must verify the common task-and-target record, input completeness, forecast software context, context delivery and independent-copy isolation. Run a non-study subscription qualification in the selected forecast environment first. Preserve that receipt separately from the 200 study slots. Do not require attached GPUs or a launched natural worker to collect forecasts. Actual worker and GPU checks are gates on the later natural run; if the implemented task setup differs from its forecast target, retain and label that mismatch rather than calling it a clean pair.

The current allocation remains 200 forecasts plus 600 task executions per agent. M is still observed natural runtime. Forecasts never set M, timed requests, priority or resource allocation. No corrected study forecast has been dispatched.
