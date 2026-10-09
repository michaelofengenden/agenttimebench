# Roster history re-review, round 1

MODE: code-review

No remaining actionable findings in the five reviewed inventory files. The earlier P1 is resolved for the current roster origin and an appended receipt chain.

The staged implementation was compared with the installed baseline at `/Users/michaelofengenden/Developer/TimeResearch/AgentTime-v1.1`. Review scope was `evidence_checks.py`, `build_inventory.py`, `verify_inventory.py`, `test_selection_transition.py` and `test_inventory_evidence.py` under `/private/tmp/agenttime-automationbench-expansion-20261005/staged-repository/benchmarks/inventory`. Implementation and roster data were not edited.

## Resolution of the earlier finding

`read_selection_history` requires a nonempty history beginning at the preserved origin path and independently pinned SHA-256. That origin has identical bytes in the staged and installed repositories: `e6f4e5df316c19322467204100b8c30f6c7b83b8798840a03ae108e1521c40fd`. Later receipts must name and hash their immediate predecessor. Both production callers now load and check history unconditionally. `check_selection_history` takes explicit origin rows, checks every retained identity at each step and accumulates occupied slots across the chain.

These changes close the reproduced omission and retired-slot reuse cases. Added tests include actual missing-first and empty-history cases, rather than only an identity mismatch between two supplied receipts.

## Fresh verification

- All 33 inventory tests passed in the staged directory with bytecode writes disabled.
- Two additional positive cases passed: the existing legacy single-receipt history retained its 201 identities and 12 retired slots; an independently constructed three-receipt extension retained those identities and retirements while adding two synthetic review-only identities.
- Thirteen additional negative cases were rejected: missing first receipt, missing middle receipt, empty history, out-of-order history, repeated receipt path, path escape, altered origin bytes, wrong predecessor hash, wrong predecessor path, retired-slot reuse, an unrecorded change to a retained identity, an unrecorded final addition and the direct helper called with a truncated chain plus independent origin rows.
- Temporary test fixtures were removed. No benchmark, agent, account, provider or network call was made.

Reviewed staged file SHA-256 values:

```text
evidence_checks.py          550a2295c97f1f1171ac83d68afe2a078341fc4192092cf29caea6c0c69d6ecc
build_inventory.py          612ca1725f898b56deacc7b46848fb69614b1f24c048bcd57fbf7abf32fada9f
verify_inventory.py         47c2f00d8775d5c2a3a5f8f4b5e6c2b0236856200369414856776242e724c25b
test_selection_transition.py 6be51e906c087413175ccbf25afc747daa9dc070debf8518548449e0ea558f38
test_inventory_evidence.py  ca7d21c9b17f2f40ed8d66edb85f24f2bb1a079dcec493f897b8d69bc16cd2cc
```

This approval covers the history implementation at those hashes. The final expanded roster, task evidence and rebuilt inventory were outside this re-review and still require their own identity/content verification. Synthetic review additions are not task selections or runtime qualification.

VERDICT: APPROVED
