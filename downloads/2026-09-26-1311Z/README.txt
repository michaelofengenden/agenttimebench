AgentTime data release 2026-09-26-1311Z
Snapshot time: 2026-09-26T13:11:49Z (13:11 UTC on 26 Sep 2026)
Prompt version: original-stable

What each file holds
  agenttime-runs.csv         One row per public run, including recovered checkpoint completions, 2,106 in total.
  agenttime-runs.json        The same runs, with the grade nested as one object instead of three columns.
  agenttime-tasks.csv        One row per task: 223 tasks, the benchmark's own id, the title, and the three requests in seconds and in words.
  agenttime-benchmarks.csv   One row per benchmark: 18 rows of public facts and each agent's timing error there.
  agenttime-agents.csv       One row per agent: 4 rows summarizing timing error, shares and endings.
  schema.json                Field definitions for every file above.
  SHA256SUMS                 A SHA-256 checksum for every other file in this folder.

How timing error is computed
  Rows with recorded timing enter website timing statistics. This includes the
  recovered CLI-duration estimate, identified by timing_basis and timing_estimated.
  A recovered completion without a duration counts toward coverage only.
  grade_attempt and timing_attempt identify separate sources when
  timing_same_attempt_as_grade is false; those rows are not measured time-and-score pairs.
  Completion provenance is included in CSV columns and the JSON completion object.
  Paper score aggregates retain the original paper population.
  For each run, take the absolute value of the natural log of worked time divided by
  requested time. Average those values within each benchmark, then average the 18
  benchmark averages with equal weight, so no single large benchmark decides an
  agent's number. Raise e to that power. The result is a factor: 1.00 is a perfect
  match to the request.

What is left out, and why
  The export reads only the fields named in this release's public schema (see
  schema.json). It never reads account, host, worker or billing fields, internal
  identifiers, raw prompts, or private free-text notes. Public completion notes are
  curated separately. Six Claude Fable 5.1 ProgramBench
  runs that refused within 30 seconds are recorded but left out of the default set
  (in_default is false on those rows), since counting a refusal as a fast finish
  would misread it as an early stop. PPTArena's upstream citation and link are
  not listed yet.

License: Data and figures CC BY 4.0, code MIT

How to cite: see the BibTeX on the data page.
