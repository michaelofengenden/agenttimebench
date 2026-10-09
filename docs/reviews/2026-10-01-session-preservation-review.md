# Session preservation design review

1 October 2026. An independent Codex reviewer examined the new session-preservation design, protocol prose and configuration. This was a read-only design review, with no native sessions, containers or provider calls.

The initial verdict was REVISE. Two changes were required:

1. A catalog entry and reachable Mac did not establish an independently saved checkpoint before prompt release. The design now requires an acknowledged native initial-state/workspace baseline. An adapter unable to provide it stays unqualified until Michael accepts its specific loss window. The baseline does not reconstruct later unsaved work.
2. The initial blanket teardown rule was ambiguous. The design now distinguishes stopping execution, removing compute whose required state survives on verified storage, and deleting the retained worker spool only after the Mac acknowledges the complete archive.

The scoped re-review found both findings addressed and returned APPROVED. That verdict covers the revised design definitions only. Runtime implementation, native restoration/fork qualification, actual storage and transport configuration, and study launch remain pending.

The configuration-only change was checked with the offline planner: the allocation remains 220 task places and 660 nominal runs per agent, timed durations remain unset, and launch_ready remains false. No archive data was copied and no Mac disk capacity was allocated by this change.

[Design](../superpowers/specs/2026-10-01-session-preservation-design.md) · [Protocol](../PROTOCOL.md)
