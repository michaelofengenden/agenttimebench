# AutomationBench selection and adaptation

One candidate passed the scoped native API and grader audit: `sales.cross_reference_validation`. Remove all twelve tau-cubed tasks and add this one recommendation. The current allocation is 201 tasks, or 603 planned executions per agent, with 201 separate forecasts. No live study attempt is admitted by this change.

The user authorized replacing tau-cubed with any justified number of AutomationBench tasks and removing both the native turn-budget instruction and matching cap for natural runs. This does not change any of the other 200 task identities. Exact old and new mappings are preserved in the [selection transition](../../benchmarks/inventory/evidence/selection-changes/2026-10-05-automationbench.json).

## Evidence and limits

The audit uses the public AutomationBench 1.0.6 source at `4a8e1061254004d9dac807054eed33fad7d1ff14`, with the native `api` toolset. It is not the private AutomationBench-AA task set. The source archive and dependency lock are retained in the private cache.

The public 600-task catalog was indexed and screened against reported issues. Seven candidates received native API solution witnesses. Each valid witness passed, but six candidates then failed targeted grading controls. Those six are held out without silently modifying their graders. Two further marketing candidates were held out during requirements review. This is not an exhaustive audit of all public tasks and does not establish which task is hardest.

For the retained recommendation, doing nothing fails, a valid solution passes, fifteen incorrect variants fail, and an equivalent valid wording passes. All sixteen variant checks preserve the selected task's per-assertion grades across JSON export and restore. The root independently replayed these controls and all six rejected candidates' counterexamples. No model or real business service was called.

The selected task contains eleven authored assertions. Four are counted in the intact successful state; initially satisfied guards become counted failures if broken. Preserve native partial credit, strict completion and the assertion-level exclusion reasons. A final-state grader cannot prove which sources an agent read or which reasoning it used.

The [public audit page](../../benchmarks/inventory/automationbench-audit.html) summarizes the counterexamples without publishing solutions. Raw source task records, assertions, witness code and state remain in `benchmarks/cache/automationbench-1.0.6-20261005/audit`, outside the website and subject environment. The private archive-location map preserves original paths and content hashes.

## Natural-run variant

`agenttime-automationbench-api-uncapped-v1` removes exactly the upstream turn-budget clause. It preserves the other benchmark instructions and business request. Original message, adapted message and delivered-text hashes are recorded in the [release selection](../../benchmarks/releases/automationbench-1.0.6/selection.json).

The actual agent harness will receive three native tools over MCP: `api_search`, `api_fetch` and `base64_encode`. The benchmark's capped model-provider loop is bypassed. Initial state, assertions, grading and owner controls stay in a separate private backend. A forecast receives only the adapted visible task text, and its output never enters the natural session.

## Qualification boundary

The standalone adapter and Harbor task configuration are preparation work. No native Codex or Claude Code session, model call, fleet rollout or campaign launch occurred here. Production controller integration, effective uncapped outer orchestration, native completion timing, private network deployment, durable journaling and complete native session archives still require qualification. Backend state restoration alone does not establish session restoration or forks.

The [adapter guide](../../qualification/automationbench/README.md) describes the model-free fixture and its checks. Study readiness remains false.
