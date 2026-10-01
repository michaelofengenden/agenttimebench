# Benchmark preparation

`../configs/suite.json` is the selected allocation of task places. It is not an executable, qualified roster.

`candidates.json` carries the exact ProgramBench candidates and the three selected replacements. Their inclusion is selected direction; their new execution routes and verifiers still need qualification. Do not fill unresolved task places with invented IDs.

During preparation, add a source lock for each task: upstream revision, source and image digests, original/adapted prompt hashes, verifier revision, local patches, resource profile and submission-preservation contract. Acquire clean sources at those pins. Downloaded material belongs in the ignored `cache/` directory; reviewed locks and patches belong in Git.

Native deadlines and task-defining endpoints must be distinguished per benchmark. Qualify initial state, a correct reference submission and adversarial incorrect submissions before admitting a task to study runs.
