# AssistantBench bridge qualification

The bridge passes model-free BrowserGym and official MCP-client checks on Linux amd64. Nineteen focused tests pass. No selected task, model, credential or grader was used. The actual Claude-to-MCP canary is prepared for the root operator and has not been run by this implementer. Study admission remains separate.

The bridge exposes `NativeBrowserBackend(goal, seed=0)`, `AssistantBenchBridge(backend, state_dir, attempt_id)` and `serve_stdio(bridge)`. The goal contains only the frozen native task text. Each attempt requires a new private controller directory and UUID. Keep this directory, native session archives and all reference datasets outside subject filesystem mounts. The module provides a browser protocol, not a container or network firewall.

The stdio entry is:

```text
python -m agenttime.assistantbench_bridge --goal-file PATH --state-dir NEW_PATH --attempt-id UUID
```

It accepts newline-delimited MCP JSON-RPC. Calls are serial. Tool output contains only the latest native screenshot, accessibility tree, tabs, active tab and an action-failure boolean. Exceptions, host paths, rewards, scorer data and elapsed time are excluded. The native `send_msg_to_user` callback seals the answer before post-step observations. Further actions and repeated submissions are rejected; tool output never reports a grade.

The tool manifest now matches BrowserGym 0.14.3's dedicated `ACTION_SUBSETS['assistantbench']`: scroll, fill, select_option, click, press, go_back, goto and send_msg_to_user. BrowserGym always adds noop; the MCP transport adds observe. Native click button/modifier arguments are retained. The cached `core/action/highlevel.py` defines this set with a reference to the AssistantBench paper. Coordinate actions and report_infeasible are absent from this native subset, so their omission does not reduce that interface. The earlier provisional extra navigation, tab and element actions have been removed. No arbitrary Python, shell, file upload, browser evaluation or filesystem tool is exposed.

The answer-free task wrapper retains native en-US locale, America/New_York timezone, Google start page, 1280×720 viewport, native slow-motion and per-action/setup timeouts. It never imports the dataset-loading AssistantBench task module or scorer. Native termination still occurs on an assistant chat message; scoring is deferred to a separate controller. No experimental duration, turn or token cap is added.

On shutdown, the bridge writes private `browser-state.json` and a digest receipt before closing Chromium. They preserve cookies, localStorage, open-page URLs, the active page and profile. `NativeBrowserBackend(..., restore_snapshot=PATH, restore_sha256=INDEPENDENT_HASH)` rehydrates this state in a new context. The goal hash must match, and the bridge must use a fresh attempt directory. The Linux check proves cookie/localStorage recovery after browser shutdown and verifies that an unrelated attempt starts clean.

This is useful saved browser state, not a serialized running browser. JavaScript heap, history, sessionStorage, unsaved form state and pending downloads are not recreated. Screenshots and accessibility observations remain in the original MCP/native transcripts. A native Claude session fork and this browser checkpoint can be combined for a documented continuation; its fidelity and timing treatment must be stated. Exact JavaScript-heap restoration is not claimed or required by this module's checks.

The tested image is local Docker image `sha256:148e5ecfdd7d47fb01785f1f7b06471ec72fe939881eae6a4cb8d4282f59259d`, tagged `agenttime-assistantbench-qualification:20261009`. `Dockerfile` starts from official Python manifest `python@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3`, installs `requirements.lock`, and installs official Playwright Chromium build 1117. Linux evidence records Python 3.12.15, BrowserGym 0.14.3, Playwright 1.44.0 and Chromium 125.0.6422.26. Installed BrowserGym sources exactly match all four cached source hashes in `source-pins.json`. OS and Python package inventories accompany the image receipt. Future builds may obtain changed OS packages; a different image digest requires qualification.

The local Linux checks used Docker Desktop's amd64 environment with four CPU cores, 16 GiB RAM, a PID limit, dropped capabilities, no-new-privileges and network disabled. The localhost test page required no external access. This qualifies dependencies and tool semantics, not Hetzner performance or public-web reachability. `qualify.py` exercises nine native/profile/state checks. `qualify_mcp.py` exercises six official-client checks. Its watchdog is for the disposable synthetic test only. The final process check found no remaining Chromium or Playwright process.

To reproduce the browser checks, copy `assistantbench_bridge.py`, `jsonio.py` and `evidence.py` into an `agenttime/` directory in a clean bundle. Put `qualify.py`, `qualify_mcp.py` and `synthetic.html` beside that directory. Use the bundle as PYTHONPATH and `/opt/playwright` as PLAYWRIGHT_BROWSERS_PATH. Run each qualification script with `--output` pointing to a new private directory. The image contains no study inputs or Claude binary.

`claude_canary.py` has a fixed synthetic prompt and defaults to preparation. Run `python claude_canary.py prepare --root NEW_PATH` inside the image. The root operator independently archives that directory and provides an acknowledgement containing `contract_sha256`, `archive_verified: true` and `archive_location`. Explicitly running the model requires:

```text
python claude_canary.py run --root PATH --binary PINNED_CLAUDE --acknowledgement ACK_PATH
```

Only this command reads a subscription token from stdin. It never opens a credential file. It verifies Claude 2.1.280's binary hash, requests Opus 5.5 [1m] at max effort, disables built-in tools and permits only the ten named MCP tools. It freezes validated input bytes in memory before delivery. Its MCP command uses env -i so the browser child receives no OAuth token. No fallback model or paid API route is configured. A disposable synthetic watchdog defaults to 300 seconds; this helper is never a natural-run entry point.

The helper preserves the original native transcript, output stream, raw transport files and browser checkpoint. It checks actual startup/tools, request model/effort, final submission, changed browser state and included-subscription evidence. A missing or incomplete optional OTEL response can be supported only by a complete original stream with the exact matching response ID, model and session; the receipt labels that evidence and retains the original raw file. Root must still verify independent final archive copies, native resume/fork and container stop proof. A successful synthetic tool-path receipt does not admit study attempts.

Evidence from the earlier macOS checks is retained under `evidence/`. The additive `evidence/linux-amd64/` records this revision's RED/GREEN tests, Linux receipts, source/image pins, package inventories and model-free canary preparation. Full-regression failures outside these owned files are reported separately to their owner. Remaining study checks concern the actual Claude MCP path, deployed worker/network policy, public-web access, private scorer controls and the documented continuation/fork method.
