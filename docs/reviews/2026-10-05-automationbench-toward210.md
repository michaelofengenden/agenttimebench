# Broader AutomationBench audit

The user asked for more AutomationBench tasks, aiming for approximately 210 tasks overall. This pass inspected 267 prompt/grader contracts across all six domains and exercised 22 additional candidates through native tools. Every tested candidate had a material grading or API problem, so no task was added. The proposal remains at 203 tasks, including three AutomationBench candidates. All prior slot identities, source selections and roster-change receipts remain unchanged.

Across three audit passes, 50 candidates have received native API checks and 47 are held. This is a purposive audit, not a defect-rate estimate for the complete benchmark. The three retained tasks still cover sales/CRM only. The private packet preserves the witnesses, incorrect-output controls, accepted alternatives and reproduction scripts. Independent root replay verifies the new hold outcomes against the pinned, unchanged native graders.

The current plan remains 609 executions plus 203 separate forecasts per evaluated agent. Native model execution, timing, controller recovery and full session archives still need qualification. No grader, task prompt, adapter or model configuration was changed; no benchmark model runs were launched.

See the [audit page](../../benchmarks/inventory/automationbench-audit.html) and [public audit record](../../benchmarks/inventory/evidence/automationbench-audit-summary.json). Private audit evidence is retained in `benchmarks/cache/automationbench-toward210-20261005` outside the served review directory.
