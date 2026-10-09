# Forecast correction qualification

The Claude subscription remains the selected route. This folder contains a model-free check of the installed Claude Code version and a proposed correction to the 200-task forecast batch.

The [correction note](FORECAST_CORRECTION.md) explains the missing inputs, the scope changes and the remaining decisions. [preflight-audit.json](preflight-audit.json) covers every selected slot. Neither file is a launch manifest.

Local fake-API tests verified explicit image delivery, native transcript saving, request-body capture and one-request behavior for a simulated HTTP 529 with retries disabled. Disabling attachments does not remove the host environment note. The four HLE image checks sent only a synthetic qualification prompt to the local fake endpoint; no model answered the task questions.

The host-strategy decision is pending: use matching benchmark environments, or forecast on the Mac with explicit target-environment descriptions. Final natural prompts and execution profiles must be frozen in either case. No corrected study forecast has been dispatched.

The reports preserve original capture paths. `artifact-index.json` maps the synthetic probe evidence to its archived location in this folder. Actual HLE image payload captures remain in the private temporary probe directory; this folder keeps their hashes and transport report, without copying those gated assets.

These scripts are disposable qualification probes. To reproduce the checks, copy the scripts and fake API to a new temporary directory and run them there. Preserve this evidence directory unchanged. The scripts pin the local Claude executable path and, for the HLE check, the repository path. They use fake authentication and a loopback API, never real account credentials.

The independent reviewer could not start its local database. The correction note has been self-reviewed; it has no independent approval.
