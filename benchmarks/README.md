# Benchmark preparation

`../configs/suite.json` is the selected allocation of task places. It is not an executable, qualified roster.

`candidates.json` carries the exact ProgramBench candidates and the three selected replacements. Their inclusion is selected direction; their new execution routes and verifiers still need qualification. Do not fill unresolved task places with invented IDs.

During preparation, add a source lock for each task: upstream revision, source and image digests, original/adapted prompt hashes, verifier revision, local patches, resource profile and submission-preservation contract. Acquire clean sources at those pins. Downloaded material belongs in the ignored `cache/` directory; reviewed locks and patches belong in Git.

Native deadlines and task-defining endpoints must be distinguished per benchmark. Qualify initial state, a correct reference submission and adversarial incorrect submissions before admitting a task to study runs.

The [task inventory](inventory/README.md) and [searchable review](inventory/index.html) cover all 200 places, with draft IDs, verified cache receipts and per-component gaps.

The [7 October revision](../docs/revisions/2026-10-07-hetzner-runpod.md) uses fresh slots `ale-13`, `ale-14`, `ale-15`, `core-13` and `core-14`. Its [selection receipt](inventory/evidence/selection-changes/2026-10-07-hetzner-runpod.json) preserves the retired identities and the other 195 task identities. CORE retains four mainline and eight OOD capsules. The public ALE bundles, protected references and original CORE archives are pinned in the dated private cache.

Molecular Cartesian geometry and the retained Terminal-Bench CAD task provide 3D work. The retired Gaussian-splatting task's visual-media creation coverage is removed. PaperBench retains its task identities and private native rubric; its separate fresh reproduction contract now specifies H100 grading and keeps the native seven-day reproduction maximum. The natural subjects remain uncapped.

The intended allocation is 192 Hetzner CPU/desktop candidates, including 32 closed-book controller tasks, and eight RunPod H100 research subjects. The revised task requirements no longer call for Windows, T4 or L4 workers. Native allocations still take precedence over the ordinary 4-CPU/16-GiB baseline. Every natural environment, material filter and grader remains subject to qualification.
