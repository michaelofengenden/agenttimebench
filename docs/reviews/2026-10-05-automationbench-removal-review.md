# AutomationBench removal review

No blocking findings.

Reviewed the staged removal against the live repository and its original guard. The change implements the user’s explicit choice, “Remove AutomationBench; keep 200 tasks.”

- Only `automation-01`, `automation-02`, and `automation-03` are retired. All 200 surviving complete task rows and identities match the live baseline exactly; the remaining 84 recommendation rows also match.
- The third selection receipt preserves both earlier receipts byte for byte, links to the second receipt with its correct SHA-256, records the exact prior 203 identities, removes only those three slots, and adds none.
- The allocation is 200 tasks across 18 active families. AutomationBench and τ³ both have zero places. The plan is 600 executions and 200 separate forecasts. Protocol configuration remains byte-identical, including the 220 capacity ceiling and one evaluated agent at a time.
- Runtime and test source files, the source manifest, and the historical candidate records are unchanged. Candidate metadata explicitly marks them inactive and excluded by the user.
- The main inventory, audit page, and current documentation clearly show removal. The nine-task adaptation proposal is archived and not adopted. It is not presented as awaiting approval or as an implemented prompt/grader change. The text does not infer that all upstream tasks are flawed.

Independent review checks covered full-row equality, exact removals, receipt ancestry and hashes, surviving recommendation equality, unchanged protocol/runtime/source-manifest bytes, and the public removal/archive wording. Root additionally reports 33 inventory/history tests and 14 planner tests passing, full verification of 34,585 cached files, and passing static page/link/JavaScript checks. Those checks establish removal consistency, not native task readiness.

VERDICT: APPROVED
