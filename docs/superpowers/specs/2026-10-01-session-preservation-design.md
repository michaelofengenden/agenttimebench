# Save, restore and fork AgentTime sessions

1 October 2026. Requirement and proposed design for review. Runtime implementation and native restore qualification remain pending.

## User intent and accepted storage direction

Every AgentTime session should remain findable after its worker is gone, be restorable onto a compatible server, and support further sessions forked from its saved state. This applies to successful, incorrect, refused, interrupted and failed runs, including native subagent sessions where the agent exposes them. An interrupted record retains its latest complete checkpoint and explicitly identifies any unsaved tail.

Michael selected his Mac as the archive host, independent of worker servers, and said he can make approximately 4–5 TB available. Use a private filesystem archive; an S3 service is unnecessary. That capacity is an intended allocation, not verified free space or a retention guarantee. The proposed archive root is a sibling of this repository, `../AgentTimeSessionArchive`, under TimeResearch. No large archive or remote service is created by this design.

Keep the existing natural/short/long study rules, one-agent admission and one permitted infrastructure replacement unchanged. Saving a session, restoring files and starting another model invocation are separate operations.

## Archive contents

Each attempt has a stable catalog record before subject release. It binds campaign, logical slot, physical attempt, evaluated agent configuration, task revision, native session IDs and parent/child relationships. Before releasing the task prompt, require a baseline checkpoint containing the prepared workspace/environment and the adapter-qualified initial native session state, verified and acknowledged on the Mac. A reachable destination or a catalog record is insufficient. If an adapter cannot establish this boundary before prompt delivery, its preservation qualification stays blocked until that exact loss-window limitation is presented to Michael and explicitly accepted. A baseline restores the initial checkpoint; it does not reconstruct work performed after it. An empty or lost record remains discoverable with its actual status and cannot be called restorable.

A checkpoint contains:

- Exact native session material required by the pinned agent adapter, including available subagent histories and references. A rendered transcript or converted ATIF alone is not evidence of exact native context restoration.
- Workspace contents, including untracked and ignored files needed by the task, permissions and links, plus the applicable task-specific state export. Database, browser, desktop, external-service and GPU workloads need their own checkpoint contract.
- Effective prompts and settings, agent/CLI/adapter versions, tool definitions, source locks, environment image identities, architecture and hardware requirements. Required base images and large immutable dependencies must themselves be retained or mirrored under our control; a remote URL and digest alone are insufficient for long-term restoration.
- Original event journals, outputs, submitted artifacts, grades and provenance. Preserve the scientifically sealed submission separately from a later restorable working state, with each snapshot's actual capture time.
- A manifest of paths, object sizes and hashes, the checkpoint boundary, native consistency checks, missing components and required restore capabilities.

Do not archive account homes wholesale. Adapters export an explicit inventory of required state. Provider credentials and auth stores stay outside the archive and are provisioned separately when restoring. Treat native logs as private; do not promise that arbitrary tool output is secret-free or rewrite native histories silently to redact them.

## Storage and transfer

Use immutable objects addressed by content hash, with manifests and catalog entries linking them. Stream large files and reuse identical objects across sessions; do not route multi-gigabyte snapshots through the current in-memory EvidenceStore bytes API. Chunking/compression choices are implementation details to benchmark against real sample sizes, not reasons to reserve the user's disk now.

Workers first retain a local spool. Transfer to the Mac is resumable and authenticated. Publish a checkpoint only after every required object is present, its size/hash verifies and the manifest is durably committed on the receiver. The acknowledgment identifies the manifest and required objects. An interrupted upload cannot publish a successful archive status.

No automatic expiry or destructive garbage collection. References from originals, checkpoints and fork descendants protect shared objects. If space becomes insufficient, stop new admissions and preserve existing spool and checkpoint data; do not delete older sessions to make room. A usable capacity guard must account for concurrent writers and incomplete transfers before production dispatch.

The Mac may sleep or be disconnected. New subject releases require the verified independent baseline checkpoint and the configured archive admission checks. Existing work retains its spool and retries transfer without invoking the subject again. Evidence transfer can recover independently of timing and grading.

Separate three operations explicitly:

1. Stopping owned subject execution follows the existing operational policy and may proceed without a Mac receipt. Stop proof is an execution fact, not an archival fact.
2. Removing disposable compute requires proof that execution stopped and that all required state already survives on verified storage unaffected by that removal. A persistent worker volume can satisfy the storage condition only when its lifecycle and retained identity are proved; an ephemeral container filesystem cannot. Retain the resources that hold the only usable state.
3. Deleting the retained worker spool requires acknowledgment of the complete archive on the Mac. A failed or partial transfer cannot authorize deletion. Archive objects on the Mac have no automatic expiry.

A Mac outage therefore need not retain idle compute when a complete verified export is safe on independently retained worker storage. Keep execution-stop, capacity reservations, retained storage and archive status separate. Do not release an unresolved execution reservation based on an archive receipt.

A Mac copy survives worker deletion, but it is not a second independent archive replica. A second backup destination can be added later without changing archive IDs or retaining worker compute indefinitely.

## Checkpoints and timing

Register each attempt and obtain the independent baseline acknowledgment before release. Retain native journal data as it becomes available; checkpoint at declared consistent boundaries and finalize all outcome types. Periodic background transfers must not inject follow-up prompts, keep an agent working artificially or silently pause its measured interval.

A live filesystem copy with unknown tool/process state is diagnostic evidence, not a proven restorable checkpoint. A task adapter must identify when the native session and workspace describe the same boundary, including active tools, background children and external effects. Unsupported or incomplete snapshots stay explicitly unverified. Exact rollback of an in-flight tool or external action is not implied by archived files.

Checkpoint frequency and transfer limits require a measured pilot before the 220-way cohort. They remain unset rather than inventing a universal pause or schedule that changes natural runtimes. Record checkpoint/transfer overhead and resource contention.

## Restoration, forks and scientific identity

Restore into a fresh isolated environment that satisfies the recorded OS, architecture, agent and task requirements. Download from the Mac, verify all required objects and reconstruct state before importing native session material. Restoring bytes must not launch a model or execute restored hooks/scripts. Native continuation or fork launch is a separate authorized operation with normal resource admission.

A fork receives a new AgentTime session ID and records its parent attempt, parent native session, exact checkpoint manifest and purpose. The native adapter must use the agent's supported new-session/fork semantics and verify the new native identity. Reusing the parent's native session ID in a writable shared session store is not a fork. Original archive objects, transcripts, grades and timings remain immutable.

Post-study exploration uses a separate follow-up campaign and stays outside the original 660 nominal slots and their calibration observations. The planned short/long attempts still start fresh. A future fork experiment can have its own explicit protocol. Recovery continuation of an interrupted original remains governed by the existing recovery policy, stop proof and clock requirements; availability of a checkpoint does not authorize another copy of the original to run.

Record capability facts separately: catalogued, locally spooled, Mac copy verified, structurally restored, native context restored, and native fork qualified. Full restore requires both agent and task-adapter qualification. If native model/CLI availability or an external service prevents future continuation, keep the archive reachable and report that limit instead of silently changing models or reconstructing history from a summary.

## Reuse Harbor and native agents

Pinned Harbor 0.23.0 declares resume and native-trajectory loading for both Claude Code and Codex. Claude also declares handoff. Its local handoff copies a single main native session; it does not establish complete subagent or workspace restoration. Reuse supported native import/fork mechanisms while adding the missing durable archive, environment state and provenance checks.

Official source checks on 1 October 2026:

- [Harbor trial handoff](https://www.harborframework.com/docs/run-jobs/handoff): conversation restoration has a narrower scope than environment restoration.
- [Codex CLI documentation](https://developers.openai.com/codex/cli/slash-commands/): native resume and fork operations exist; our exact pinned route still needs qualification.
- [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference): `--resume` with `--fork-session` requests a new session identity. This does not by itself restore task state.

Muse and the eventual MiMo runner need a concrete native-state contract. Do not infer their capabilities from Claude or Codex. Sessions must be preserved regardless of outcome, while an unqualified adapter cannot advertise full restore/fork support.

## Implementation slices and acceptance

1. Local archive format, streaming object storage, catalog and explicit statuses. Test empty/corrupt/truncated inputs, unsafe paths/links, native subagent references, deduplication, competing writers, partial manifests and disk-full handling using owned disposable directories.
2. Restore and fork preparation for a model-free subject. Recover using only the archive after deleting the fixture's original workspace. Create two isolated children from one checkpoint, mutate both, and prove the parent and sibling remain unchanged. Test wrong images/versions and unavailable required objects. No model calls or account material.
3. Connect collection and transfer receipts to the durable worker and Harbor lifecycle. The pre-teardown hook must be proved against pinned Harbor's actual ordering; generic cleanup flags or an END callback after cleanup are insufficient. Exercise Mac unavailability, interrupted upload, controller restart, duplicate delivery, lost worker and concurrent restore requests. No subject replay.
4. Qualify native restore/fork for each exact agent and task route on a separate server with newly provisioned authentication. Verify context, files, subagent references, native child IDs and original immutability. This is separate from a live study attempt and needs its own authorized pilot.

Before study dispatch, require the adapter's preservation/restore qualification, a verified baseline checkpoint on the independent archive host, and the configured archive capacity/availability checks. A synthetic local pass cannot satisfy native restoration. A record with a missing checkpoint must never be displayed as safely archived or forkable.

## Remaining deployment choices

The user chose Mac storage. Before configuring transfers, confirm the actual archive volume/path, reserved capacity, authenticated transport and behavior during prolonged disconnection. Before production checkpoints, measure archive sizes and overhead, then choose checkpoint frequency and queue limits. These choices do not block writing and testing a local format against disposable data.
