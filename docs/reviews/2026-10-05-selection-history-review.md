# Roster history review

MODE: code-review

Reviewed the four changed inventory files in `/private/tmp/agenttime-automationbench-expansion-20261005/staged-repository` against the installed AgentTime v1.1 repository. Implementation and roster data were not edited.

## Finding

**[P1] Anchor the beginning of the receipt chain before accepting an extension.**

Location: `benchmarks/inventory/evidence_checks.py:76` and `benchmarks/inventory/evidence_checks.py:97`.

`check_selection_history` treats the first supplied receipt's `before` list as authoritative, while `read_selection_history` trusts the manifest's supplied receipt list without checking that its historical prefix is complete. Consequently the first replacement receipt can be omitted, losing all knowledge of its retired slots. A later receipt can then reuse a retired slot and this validator accepts it. This defeats the requested append-only history and retired-slot protection. The new test named `test_missing_prior_transition_is_rejected` changes an identity between two supplied receipts; it does not test omitting the earlier receipt.

Using the same small fixture as the new tests, these three calls reproduced the problem:

```text
check_selection_history(final_rows, [first, second]) -> {'tau-01': 'old-task'}
check_selection_history(final_rows, [second]) -> {}
check_selection_history(rows_reusing_tau_01, [second_that_adds_tau_01]) -> {}
```

The last two should be rejected. Similarly, an explicit empty `selection_changes` list makes both callers skip history validation even when the earlier receipt remains in the evidence directory. Build-input hashes detect subsequent input changes, but rebuilding hashes the already-truncated history and does not enforce its completeness.

Retain and verify an independent origin or required prior receipt prefix, and reject a missing or empty required history. Add regression cases that actually remove the first receipt, remove all receipts, and then try to reuse a slot retired by the omitted receipt. Preserve the earlier receipt bytes or bind their hashes as part of that prefix.

## Verification and scope

- Ran the staged inventory test suite: **27 tests passed**.
- Ran the three direct adversarial calls above against the staged implementation.
- Staged `evidence_checks.py` SHA-256: `ab903798620d8f50060fb256c89d524c39f89073c83b967bdbdb758543281a8c`.
- Installed baseline SHA-256: `498b1244f3c7418647994749c2a8dfe586d0b9f722eaad673651bde1f01b9851`.
- Final expanded roster data has not yet been populated, so this review makes no claim about the final task count, identities or source asset binding.

VERDICT: REVISE
