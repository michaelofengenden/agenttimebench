# AutomationBench adapter qualification

This folder provides a **model-free tool-service fixture**, not a study launcher. It makes no model calls and does not read model-account credentials. Use the pinned AutomationBench source and its frozen Python 3.13 environment. Importing AgentTime's core and public prompt/config helpers still works in Python 3.12 without these optional dependencies.

The immutable source pin excludes only the optional derived API-search index. A present index must exactly match deterministic bytes reconstructed from the pinned JSONC schemas and the recorded expected hash; a missing index is valid. Mount native sources read-only so upstream search uses its existing in-memory fallback when it cannot save the cache.

The benchmark variant explicitly removes the native turn-budget sentence and the corresponding response cap. It preserves business instructions, initial state, native API tools and native grading. Forecast input is `backend.prompt['text']`; build each natural backend independently from the original task fixture. No forecast result enters this service.

## Standalone owner API

```python
from agenttime.automationbench.native import NativeBindings
from agenttime.automationbench.backend import Backend
from agenttime.automationbench.service import MCPService

native = NativeBindings(pinned_source_root)
backend = Backend(native, private_native_task_fixture, attempt_id)
service = MCPService(backend)
# Controller only, after setup:
backend.release()
app = service.http_app()  # Only /mcp. Default hosts are localhost/127.0.0.1.
# Attach app to a private per-attempt ASGI service.
# Controller only, at a separately qualified native completion boundary:
backend.seal()
checkpoint_bytes = backend.checkpoint()
private_grade = backend.grade()
restored = Backend.restore(native, private_native_task_fixture, checkpoint_bytes)
```

The owner API is never exported as MCP methods. MCP exposes exactly `api_search`, `api_fetch` and `base64_encode`. Streamable HTTP uses the real MCP session plus protocol request ID; no request-identity parameter is added to native tools. Reusing an ID within its session returns the original receipt, conflicting arguments are rejected, and a new session is a distinct invocation. Reconnecting into a new session is not an automatic retry mechanism.

Private source, raw task fixtures, assertions, world, receipts and exports belong outside the agent container. A deployable sidecar needs its own private filesystem, a dedicated per-attempt network and no Docker socket or shared source/state/grader mount. The supplied configuration proves none of that deployment isolation by itself. Do not expose this model-free service port to other users, agents or the public internet. For a private sidecar hostname, pass that exact hostname/port in `http_app(allowed_hosts=...)`.

The backend serializes native operations and closes admission before waiting for an accepted operation to finish during `seal()`. Sealing fixes the current revision; subsequent new calls fail. Exceptions after partial changes retain a sanitized failed tool receipt and private diagnostic details, taint the attempt, and prevent new calls, resumable checkpoints or valid grades. Diagnostic export is separate from resumable checkpoint. The in-memory receipt store and explicit checkpoints do **not** provide durable crash recovery between checkpoints; production journal persistence remains pending.

Owner-state schema 2 is required; earlier or unknown schema versions are rejected. Each restored owner creates a new clock domain. Historical monotonic values remain unchanged, and ordering is checked within each domain only. These are backend operation events, not native runtime measurements. Checkpoint restore verifies the constructed initial revision, every receipt transition and the final current revision. Resumability is determined from the exact captured state: a seal requested during capture cannot produce a checkpoint until its snapshot and event are complete; diagnostic export labels that incomplete state non-resumable.

The state sidecar preserves Google Sheets row-update markers and Google Ads offline jobs, including absence versus presence. Other undeclared mutable native instance extras fail closed. Grading restores a copy of the sealed world and uses the fully constructed initial world for free-assertion classification. Both partial credit and all-pass remain native; initially satisfied guardrails count as failures when broken, and authored exclusions remain excluded. No additional guardrail-zero metric is substituted. Grader exceptions produce unavailable results.

## Local model-free HTTP demo

Use `serve.py --help`, then run with explicit `--model-free-fixture`, `--source-root`, `--private-task-json`, `--private-output`, `--attempt-id` and an unused `--port`. It binds loopback only and writes a sealed diagnostic checkpoint, private grade and qualification receipt after shutdown. Its service shutdown time is **not** native agent completion or measured runtime. Never place its private input/output directory under a public website root or agent mount.

## Harbor public package

`agenttime.automationbench.harbor.materialize_task` writes only `instruction.md`, `task.toml` and a qualification receipt into a new directory. It takes an explicit digest-pinned agent image and the per-attempt MCP URL. It omits an agent timeout; installed Harbor 0.23.0 resolves that to `None`. It neither creates a Harbor job nor launches an agent. It supplies no verifier script because the private owner must seal and grade the backend independently at the qualified completion boundary.

Both native harness candidates are explicit: `codex` and `claude-code`. No direct-model API fallback exists. Installed Harbor's native MCP config renderers are tested without calling their constructors, reading account settings, installing CLIs or launching processes. Real CLI/model flags, native turn limits, background work, timing, pause/resume, session archives and production controller integration remain unqualified.

## Reproduce checks

Set `AT_AUTOMATIONBENCH_SOURCE` to the source at commit `4a8e1061254004d9dac807054eed33fad7d1ff14` and `PYTHONPATH` to this repository's `src`. Run:

```sh
python -m unittest discover -s tests -p 'test_automationbench*.py'
```

Use the isolated upstream frozen Python 3.13 environment for native state/tool/grader/MCP tests. Separately run `test_automationbench_harbor.py` under the existing pinned Harbor 0.23.0 environment. Missing optional runtimes are reported as skips, never passes. Tests use synthetic fixtures and native function calls, with no model or real business-account actions.
