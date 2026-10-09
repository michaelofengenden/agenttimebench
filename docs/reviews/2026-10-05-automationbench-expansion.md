# AutomationBench expansion

The proposal now has three AutomationBench tasks and 203 tasks overall, or 609 planned executions plus 203 separate forecasts per evaluated agent. Two additions, `sales.negative_selection` and `sales.qualify_lead`, join `sales.cross_reference_validation`. All 201 earlier slot identities remain unchanged. Exact IDs remain recommendations, and runtime admission is false.

The expansion screened metadata across all six domains and exercised 21 additional task candidates through native APIs. Two passed the scoped selection checks; nineteen remain held with counterexamples. Including the original pass, 28 candidates were exercised and three selected. This purposive sample is not a benchmark-wide defect-rate estimate. All three selected tasks are sales/CRM workflows.

The additions passed 37 incorrect-state controls, two valid-state controls and two alternate native API sequences. Independent review found no missing core output requirement in the selected pair. Root replay reproduced the controls, and the unchanged private backend matched native positive/no-op grades. All three selected checkpoints also restored in a fresh process with a different Python hash seed. These checks do not qualify native model sessions, timing, production recovery or full session archives.

A held Google Ads task exposed hash-seed-dependent set ordering during backend restoration. Its counterexample is archived; the adapter remains unchanged and this broader issue remains open. The three selected tasks passed the separate-process check.

The new roster receipt is appended to the original tau-cubed replacement receipt. Review found an omitted-origin gap in the first history validator. The repaired checker pins the original receipt bytes and requires predecessor links; all 33 inventory tests and the independent re-review pass. Historical reports and original source/cache material remain intact.

See the [audit page](../../benchmarks/inventory/automationbench-audit.html), [public audit record](../../benchmarks/inventory/evidence/automationbench-audit-summary.json), [selection](../../benchmarks/releases/automationbench-1.0.6/selection.json) and [new roster receipt](../../benchmarks/inventory/evidence/selection-changes/2026-10-05-automationbench-expansion.json). Private witness state and source records are retained under `benchmarks/cache/automationbench-expansion-20261005`; they must not be mounted into an evaluated agent or served with the review site. No model, native trial or campaign was launched.
