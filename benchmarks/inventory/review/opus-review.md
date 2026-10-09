# AgentTime v1.1 asset and selection review

**Scope and limits.** I reviewed only this packet: the totals, slot list, gap text and the two scripts. I could not see the evidence JSONs, the manifest, the cache or upstream sources. Points about upstream behaviour are therefore things to verify, not findings.

**Fix first, in this order:**
1. Add evidence visibility roles and a leak scanner.
2. Correct the builder rules that mark items `present` on any one file, on a bare directory, or unconditionally.
3. Relabel the Program, WeirdML and "retained" OSWorld rows.
4. Freeze slot IDs and re-derive every sample from its seed.
5. Add upstream checksums and a Git-LFS/Xet pointer-stub scan.

**What is already sound:**
- `admitted` and `runtime_qualified` are hard-false and asserted (build L188, verify L22).
- Environment is never `present` (verify L26).
- Symlinks and files outside the cache are rejected (verify L39–43).
- The "not hardest" and WeirdML limitations are disclosed.

## 1. Misleading selection and status claims

- **Program provenance conflict.**
  - The snapshot and README (build L232) call ten Program tasks "added Hard". Yet program-01 to program-18 are all `earlier_candidate`.
  - Either record that Michael named those exact IDs, or relabel them `draft_for_review`.
  - The display rows omit `provenance`, `replaces_task_id`, `selection_group` and review notes (L218). Surface them, because replacements are new choices.
- **Undocumented draft methods.** The packet gives no selection method for deepswe-13/14, osworld-17/18, paper-04, posttrain-03/04 or yc-04. The additions store only `reason` (L129). Label judgment-based picks as such.
- **"Retained" OSWorld rows are not uniform.**
  - One mapping's historical label (recurring meetings) disagrees with its v2.1 contract (non-recurring). OS092's historical runs omitted visual judging.
  - Add a per-task `equivalence` field: identical, instruction changed, grader changed, or environment changed.
  - These 16 rows enter via `add(**c)` (L166–167). Nothing checks them against a recorded old→2.1 map.
- **OS029.** Add an explicit `blocked` flag and pre-register its replacement now: the next eligible task in the same fixed-seed order and stratum. That way a later swap cannot be influenced by outcomes. Every family should have a pre-registered replacement order for qualification failures.
- **The 8 WeirdML rows are placeholders, not candidates.** All their components default to missing (L161–163).
  - Report "104 drafts, of which 8 are non-runnable placeholders".
  - Confirm that `selection_reason` did not use published per-task scores.
- **yc-04.**
  - It deep-copies seed-1 components, including seed-1 asset evidence (L114–118).
  - Its note says employees and clients keep internal seed 1.
  - Diff the initial worlds for all four seeds, and treat the yc rows as a correlated cluster in analysis.
- **Outcome-free claims need a checkable definition.**
  - The packet says "no new outcomes"; L84 says "no model outcomes used". These are different claims.
  - Record the pool, seed and strata fields, and assert that none is derived from outcomes or runtimes.
  - Document how the old cohort pool was formed. A filtered pool passes its bias on to the sample.
- **HLE screening.**
  - Publish the eligibility rule, whether adjudicators saw answers, and the reason for the molecular-biology exclusion.
  - If the reason is answer validity, state it as a rule applied across the whole inspected prefix.
  - Pin the dataset revision, since answer keys can be revised.
- **"Verified" wording.**
  - Verify compares files only to our own manifest (L37–52); nothing shows an upstream comparison.
  - README prose such as "task definitions are verified" and "successful authenticated acquisition" is hard-coded and unchecked.

## 2. Asset completeness and reproducibility (routine, can start now)

- Add `upstream_checksum` and `upstream_verified` to each manifest entry, as the acquisition policy requires.
- Scan for Git-LFS/Xet pointer stubs. PaperBench data is likely LFS-backed, and the size and hash checks pass on stubs.
- Download and pin:
  - Qwen3-1.7B-Base, by HF revision with per-file SHA256.
  - The OSWorld 2.1 VM image.
  - PINN external data.
  - The tau banking retrieval corpus and config.
  - TUA original download bodies, recording URL, date, headers and digest. Compare against task_spec digests if present. If the original bytes are unobtainable, mark the task unreproducible rather than substituting.
- Materialize DeepSWE repos at their pinned commits and record tree hashes. Pull published images by digest without running them.
- Static audits:
  - Dockerfiles: unpinned `FROM`, apt, pip or clone steps.
  - Terminal generators: unseeded randomness, timestamps or network fetches.
- Decode and hash the four HLE images.
- Implement the GPQA deterministic choice mapping, and check that correct positions are not constant.
- Record the tau v1.0.1 tag→commit mapping.
- Acquire each PaperBench paper's blacklist and any judge-only addendum.

## 3. Contamination and input/reference isolation

- **Evidence has no visibility roles.** `ev()` supports `role` (L33, L41), but no call sets it.
  - The tau task record is evidence for prompt, assets and grader at once (L148–152), and it holds private user goals and evaluation criteria.
  - BrowseComp's "prompt" evidence is the whole CSV (L134–138). That file contains the encrypted answers *and* the canary that decrypts them.
  - Add `visibility ∈ {subject, simulator, env_internal, verifier}` to every reference.
  - Build subject packages only as derived, allowlisted trees, and assert that no subject path is verifier-visible.
- **Leak scanner, now.** Scan `tasks.json` (including embedded candidate records, L139 and L164), `index.html`, `recommendations.json`, the README and future subject trees for:
  - answer strings and canary GUIDs;
  - credentials;
  - private path patterns such as `tests/`, `rubric`, `gold`, `solution`, `expected`.

  The privacy claim (L213) is currently asserted, not checked.
- **The inventory itself is hint material.** OSWorld gap text discloses contract details. Keep it out of forecast and subject views. Move `benchmarks/cache` outside the repo to a separately permissioned volume.
- **Online leakage paths need a per-family network policy:**
  - Program cleanroom: original sources and package managers.
  - DeepSWE: upstream fixes. Strip post-base history and remotes.
  - CORE: public capsules that include results.
  - PaperBench: the authors' code. Enforce the blacklist and its monitor.
  - BrowseComp, HLE, GPQA and AssistantBench: dataset mirrors.
- **Forks.** Fork only pre-grading snapshots. The LabPlot task declares intermediate evaluation unsafe, and graders that copy tests into the environment poison later snapshots.

## 4. Builder and verifier false-ready risks

- **L197:** `present` requires only *one* existing evidence file. A paper or posttrain prompt with a missing addendum or benchmark file still passes (L103, L109). Require all listed files to exist, be hashed and not be stubs.
- **L36 and L152:** directories count as evidence and are never hashed (e.g. `src/tau2/evaluator`). This contradicts the packet's own rule. Expand them into hashed file lists.
- **Unconditional claims (L124, L128, L173, L176):**
  - "Hydrated and hash-verified", "test_data present" and the forced PPTArena grader `present` are never checked.
  - Verify L31–32 only enforces that the old to-do notes were deleted.
  - Gate each claim on file existence, manifest hash and a receipt cross-check.
- **L171–173:** this block overwrites the earlier posttrain asset evidence. It also derives the target from the candidate-ID suffix rather than `adapter_benchmark_id` (L108). A mismatch silently stays `needs_checking`.
- **`add(**c)`:**
  - Rows from resolved-candidates (evidently OSWorld and HLE) bypass all family-specific logic.
  - Their extra fields can override `slot_id` and `components`.
  - Hashes supplied in the JSON evidence files are never rechecked against the files.
- **L61:** slot IDs follow insertion order. Reordering an evidence file silently remaps the slots that sessions and forks will key on. Commit a frozen slot→identity map and assert against it.
- **Missing asserts:**
  - `selection_status` is one of the defined values.
  - Every fixed-seed sample can be re-derived from pool, seed and strata.
  - BrowseComp row hashes match when the CSV is parsed.
  - The tau record is found by ID, not taken as `paths[0]` (L148).
  - The L144–146 globs match the native loader's file list. They may pull in solo-mode or user-side files and miss manuals or corpora.
- **Verify never ties `tasks.json` to its inputs.** Record input hashes at build time, then rebuild and diff, ignoring `created_at_utc`. Separately, `--metadata-only` overwrites `verification.json` (L69–70).
- **Rename `present` to `source_present` in the totals**, so that "prompt present 202" is not read as "prompt ready".

## Later runtime gates (these do not block acquisition)

- **Images:** build once, pin by digest, and rebuild to confirm the initial state is deterministic. Audit image layers for builder-stage references.
- **Graders:** run native positive and negative controls, and report infrastructure failures distinctly from task failures (OS092).
- **Prompts and limits:** adapt natural prompts and audit timers and turn limits (PaperBench and PostTrainBench duration wording, tau max steps).
- **Conditions:** freeze VM date and time zone, and record web conditions and URL logs.
- **Forecast sessions:** each gets its own environment instance, with no shared caches, browser profiles or provider-side memory.
- **Replacement attempts:** they must reproduce the original initial state. Kafka history seeded at startup is a risk here.

## Needs Michael

1. **Intrinsic budgets** (PostTrainBench compute, PaperBench time, METR and AHC durations): are they part of the objective, or removed? Is there an operational cap, and if so how are censored natural runs treated?
2. **What M measures** under 220-way concurrency: wall-clock time including simulator, judge and queue latency, or active time? (If not already fixed.)
3. **Per-family network and tool policy**, especially whether HLE and GPQA get tools. Drafting native defaults is routine; any deviation is his call.
4. **Substitute judge or user-simulator models** where the native ones are unavailable.
5. **What "challenging" means:** partition-level difficulty, or outcome-informed item choice labelled as such?
6. **WeirdML:** if the runnable packages are not public, seek maintainer access or reallocate the 8 slots.
7. **OS029:** allow the excluded upstream repair, or replace the task.
8. **Confirmations:** whether he chose the Program Hard IDs, and whether yc-04 stays if its world is not distinct.

Everything else above is routine work.

VERDICT: REVISE. This applies to inventory labels and status logic only; source acquisition should continue in parallel.
