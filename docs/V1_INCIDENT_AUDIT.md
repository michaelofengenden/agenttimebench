# What went wrong in AgentTimeExperiment

The retained records show recurring recovery and integration work. They do not support one reliable percentage of lost or unreachable sessions, or a numerical reliability ranking of Claude Code, Codex and Muse. Observability loss, subject failure, capture failure, missing grades and scientific admission were often different events.

## Counts and their limits

| Audited population | Finding | What it can establish |
| --- | --- | --- |
| September 4/9 historical pilot | 246 current receipt files and 18 excluded, including 13 quota stops | The two audits describe the same snapshot. Receipt files were not immutable attempts. Do not count them twice or infer a final campaign failure rate. |
| September 16 AssistantBench capture recovery | 76 naturally completed capture-failed records revalidated; 50 previously unpublished | Timing recovery only. Scores remained null; capture, grading and scientific admission did not follow automatically. |
| September 24 three-model snapshot | 3,091 distinct attempt IDs and 1,989 selected completed timings; 11 selected finalization warnings remained real; 16 Sakana warning labels had already been resolved in retained evidence | A dated inventory of residual issues, not lifetime outage incidence. |
| Enriched subset of 87 suspicious candidates | 36 substantive answers, 23 provider rejections, 4 pre-onset controller failures, 24 unresolved | Deliberately selected for investigation. It cannot estimate the failure rate of the whole cohort. Rejections included safeguards, credentials, one upstream timeout and one session limit. |

Sources: [pilot audit](../../AgentTimeExperiment/reviews/PILOT_FAILURE_AUDIT_20260909.md), [capture recovery](../../AgentTimeExperiment-data/capture-duration-recovery-20260916-01/REPORT.md), [September 24 audit](../../AgentTimeExperiment/local-artifacts/run-quality-audit-20260924/REPORT.md), [candidate classification](../../AgentTimeExperiment/local-artifacts/run-quality-audit-20260924/refusal-audit/SUMMARY.md). These populations overlap and must not be added.

## Causes and fixes

The old runner had concrete defects: global Docker-list differences were used to infer ownership, cell homes were reused, receipts were overwritten, quota cooldowns could be bypassed and Codex could stall on non-TTY stdin. One collector aborted a multi-host scan after a single SSH timeout; another discarded successes when any host failed. Empty dashboards therefore did not establish that work had stopped.

A Claude Terminal-Bench completion had timing and a grade but failed capture validation because Harbor redacted its transcript after the supervisor had sealed its hash. This was an integration-order defect. Seal the final retained bytes, and record transformations explicitly.

Other problems came from infrastructure and credentials. One Claude handoff records six storage preflight failures, six Docker DNS build failures and six setup/network failures, all before model work. A changed Mac IP blocked SSH to five healthy EC2 workers. This is a direct reason to retain reservations during observer loss.

Sources: [pilot mechanisms](../../AgentTimeExperiment/reviews/PILOT_FAILURE_AUDIT_20260904.md), [Claude handoff](../../AgentTimeExperiment-data/claude-completion-20260916-01/HANDOFF.md), [collector and connectivity records](../../AgentTimeExperiment-data/CONTINUE_AGENTTIME.md).

## Why each agent needs an adapter qualification

| Native agent | Recorded compatibility work |
| --- | --- |
| Claude Code | Startup hooks, transcript ancestry, automatic compaction authentication, background-task notifications and the transcript-redaction ordering problem. |
| Codex, including Astra | Non-TTY EOF behavior and a pinned CLI stream format emitting duplicate item identity keys in native web-search events. Generic strict JSON handling alone was insufficient for that particular historical stream; it needed a versioned decoder that preserved both identities. |
| Muse Code / muse-spark-1.3 | macOS Keychain versus Linux file login, expired approval attempts, version-specific session markers, token lifetime and exclusive account leases. |

Sources: [Claude native contract](../../AgentTimeExperiment/docs/CLAUDE_PERSISTED_INPUT.md), [Codex stream protocol](../../AgentTimeExperiment/docs/CODEX_ASTRA_STREAM_PROTOCOL.md), [Muse integration](../../AgentTimeExperiment/docs/MUSE_CODE.md). These demonstrate different compatibility needs, not a reliability ranking.

Harbor can give each integration the same outer lifecycle. The native CLIs still have different authentication, event streams, saved-session formats and completion behavior. AgentTime should test each pinned adapter against one common contract. A model and its native runner remain one evaluated agent configuration.

## Recovery requirements for v1.1

1. Separate observer reconnect from subject continuation. Collector failures should retain last-good observations and unaffected host results.
2. Reattach to a healthy worker without delivering another prompt. Qualify session continuation only when the exact session and task state survive.
3. Keep journals, session state and workspace snapshots durably and independently. Test restoration before deleting the only state copy.
4. Retry evidence transfer or grading independently of the subject. Missing quality must not erase valid timing or become a zero score.
5. Prove owned execution has stopped before releasing its reservation or starting a replacement. The user explicitly confirmed that an unprovable original blocks the next evaluated agent.

The September 29 HumanEval assessment found no resumable copy of the latest Claude attempt after the relevant volumes were deleted. It did not establish that no unidentified backup existed anywhere, but an earlier attempt's archive could not replace the missing exact session. [Assessment](../../AgentTimeExperiment/local-artifacts/human30-completion-check-20260929/resume-assessment.json).

The current v1.1 fixture proves a subset of these protections. Native adapters, durable remote backups, restoration tests, qualified continuation/replacement and fleet-wide failure recovery remain work to implement and qualify.
