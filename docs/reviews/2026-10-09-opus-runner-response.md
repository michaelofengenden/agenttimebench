# Opus runner review and response

Opus found real defects in the first combined worker snapshot. Its verdict was **REVISE**. The fixes now pass the focused regressions, native Docker tests and a separate local review. This is local fixture qualification, not approval to launch the study.

The user approved one exact packet. It was sent through the Michael Claude subscription using Linux Claude Code 2.1.280 with max effort; the completed response records `claude-opus-5-5`. The installed Mac client was rejected before inference because it was older. An initial Linux review reached a development watchdog without a verdict; its checkpoint was retained, and the same approved packet completed on the later attempt. No paid API fallback was used. Development watchdogs do not apply to natural study runs.

[Original Opus review](../../runs/harbor-durable-qualification-20261009-final/review/opus-review.txt) · [completion receipt](../../runs/harbor-durable-qualification-20261009-final/review/opus-receipt.json) · [exact approved packet](../../runs/harbor-durable-qualification-20261009-final/review/OPUS_REVIEW_PACKET.md)

| Opus finding | Disposition |
| --- | --- |
| 1. Container evidence not fully bound to the trial | Fixed. Full UUID trial identity, Harbor's verified `__env` suffix, owner, image, service, endpoint and daemon must agree across trusted identity, configuration, boundary and stop records. |
| 2. Subject-writable identity used as removal authority | Fixed. Setup retains immutable identity bytes in worker memory and writes a separate controller receipt. Removal uses the original observation; altered visible evidence fails. Real Docker tampering test passes. |
| 3. Contradictory releases only quarantined | Fixed. Witness/event contradictions raise IntegrityError, which pauses shared admission. The original stop proof remains available. |
| 4. Dispatch failure consumes every slot | Fixed. Docker preflight runs before reservation. A later dispatch exception retains one uncertain claim, pauses and breaks. Both failure cases were reproduced first. |
| 5. Imported boundary source not frozen | Fixed. Import occurs before the worker fingerprint, loaded fingerprints are checked, and an AGENT_START guard revalidates code and inputs after setup. Before-release and after-execution drift tests pass. |
| 6. Malformed evidence crashes reconciliation | Fixed for the reproduced type and nesting cases. Input guards and error normalization produce an integrity pause. Database failures are not disguised as malformed evidence. |
| 7. Code/runtime updates prevent old-campaign reconciliation | Accepted conservative limitation for this fixture. Same pins are required. Preserve the old database/evidence and use a separate disposable campaign for new code. Audited resume/migration remains required before production; no automatic bypass was added. |
| 8. Effective task files not bound to frozen inputs | Fixed. The executed verifier, Compose file, task configuration and paths are checked against the frozen contract. |
| 9. Missing amendment tests | Expanded. Added concurrent CLI claims, source drift at each boundary, consistent identity forgeries and unavailable removal proof; existing boundary tests reject a changed/foreign daemon. Cleanup uses the recorded endpoint and daemon. |

The final local reviewer found no blockers in these changes. The requested Codex companion review had failed to initialize its local state, so the available independent reviewer was used as the fallback. Root separately inspected the implementation and ran the regression and native tests. The final tree has not been sent for another Opus review.

Before real agents, source events and terminal capture must come from the harness, session/workspace archives must restore on another worker, recovery and one-replacement rules must be qualified, installed software contents must be attested, and task/provider capacity must be tested. None of those future requirements is marked complete by this fixture.

See the [verification report](../results/2026-10-09-durable-harbor.md) for exact counts and retained evidence.
