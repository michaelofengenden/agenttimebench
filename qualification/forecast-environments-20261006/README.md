# Matching forecast environments

Michael selected clean copies of the matching benchmark environments, using the Claude subscription. The local Linux isolation and context test passed. **The corrected 200 forecasts have not been dispatched.**

For ordinary code and browser tasks without a published subject allocation, use 4 CPU cores and 16 GiB RAM. Native requirements take precedence. GPU, desktop and research tasks need their own reviewed profiles. Natural runs can use native harness subagents where the benchmark permits them. Forecast sessions have no tools or subagents.

The [current design](../../docs/superpowers/specs/2026-10-06-matched-forecast-design.md) and [selected policy](selected-policy.json) record these decisions. The original forecast batch and the earlier qualification evidence remain unchanged. Current work is forecasting only. GPU provisioning waits for actual task runs, using RunPod or Modal. Forecasts describe intended hardware and record their own actual resources separately.

## What the local test establishes

The [test receipt](clone-isolation-8f93f1268e/report.json) records two independent containers from the same immutable ProgramBench image. Neither had host folders mounted or external networking. The CLI used fake authentication and a local fake API, so this consumed no subscription quota and made no real model requests.

- Claude Code supplied a Linux environment note, with no macOS note or personal Mac path in the captured model context.
- A synthetic image arrived with identical bytes. The request used the requested model ID and maximum effort, with no tools. These are outgoing fake-request observations, not provider identity qualification.
- Forecast-only markers in home, configuration, workspace, memory and archive paths were absent from the other copy. The same detector caught deliberately introduced contamination.
- The native transcript and request body were saved. Both exact owned containers were removed afterward.

This ran an x86 image under Docker Desktop on an ARM Mac, with a deliberately small fixture allocation. It tests container separation and CLI context delivery. It does not qualify native x86 performance, the ProgramBench task or evaluator, real subscription authentication, all possible memory channels, or any GPU/desktop worker. No task was solved or graded.

The pinned Linux CLI package was downloaded from the official npm package named by the installed CLI's dependency manifest. Its npm integrity and executable hash were checked. The [receipt](linux-cli/receipt.json) records the version and hash. The executable is not copied into the repository.

## All 200 slots are accounted for

[environment-requirements.json](environment-requirements.json) records source hashes, native resource declarations, selected METR variants, immutable ProgramBench bindings, local image observations and remaining gaps for every slot. It is an inventory of eventual execution requirements, not a forecast launch manifest. The hardware audit was captured before the explicit decision to defer GPU provisioning. The selected policy above takes precedence. All actual natural-run runtime profiles remain unqualified; they do not have to be provisioned to ask for forecasts.

| Benchmark | Tasks | Evidence and next environment work |
| --- | ---: | --- |
| Terminal-Bench 4.0 | 16 | Task-specific CPU, memory and disk declarations are present. Build and verify each initial environment. |
| DeepSWE | 14 | Native task definitions declare 2 CPUs and 8 GiB memory. Acquire immutable image pins and qualify task state. |
| ProgramBench | 18 | All image bindings are pinned; 14 are currently cached. The clone test covers one image only. Preserve subject/evaluator separation and verify the remaining images. |
| OSWorld 2.1 | 18 | The VM archive is present. Boot the desktop, freeze task setup and qualify the controller. |
| AppWorld | 14 | Framework and task sources are present. Install and isolate the concrete app services and databases. |
| PPTArena | 10 | Decks and exact edit requests are present. Freeze the editing/rendering environment and its initial views. |
| Agents' Last Exam | 12 | Task-specific software and initial application state still need qualification. |
| TUA-Bench | 10 | Native environment definitions are present. Build images and verify dependencies and startup state. |
| AssistantBench | 8 | Prepare the native browser environment and fresh browser state. |
| BrowseComp | 12 | Freeze the browser/search environment and tool policy. |
| CORE-Bench Hard | 12 | Capsule archives are present. Review each dependency, platform and GPU requirement. |
| PaperBench | 4 | Papers and allowed addenda are present. Freeze compute and task inputs. One task still has a required-data availability issue. |
| PostTrainBench 1.2 | 4 | The shared native subject template requests one H100, 16 CPUs and 128 GiB memory. Verify actual GPU/storage provisioning and remove effective natural-work timers. |
| Sakana ALE-Bench | 10 | Freeze the subject environment and preserve the native solution judge's compute limits. |
| YC-Bench | 4 | Initialize separate seeded simulators. Include the complete business objective and endpoint without revealing private future state. |
| HLE Diamond | 20 | Freeze the model/tool route and deliver all required images. |
| GPQA Diamond | 12 | Freeze the fresh task route and verify the selected choice mapping. |
| METR public tasks | 2 | Respect the selected variants. Cowthello main declares 1 CPU, 4 GB memory and 32 GB storage; the selected payments manifest has no such allocation. |
| **Total** | **200** | **No corrected study slot is yet qualified for dispatch.** |

The Harbor definitions cover 34 slots with declared resources; Cowthello adds one selected native allocation. Absence from that count means this audit has not resolved a subject allocation, not that no requirements exist. The image inspection covered 32 slots with explicit image references. Finding 14 in cache says nothing about uninspected images or whether a cached image is runnable.

ProgramBench's cached evaluator code has its own CPU default. It is not a published subject allocation and must not be silently copied into the subject profile. PostTrainBench's template also contains a ten-hour agent timeout and a timer healthcheck; changing only the task wording would leave a cap active. Its storage declaration alone does not prove that the chosen provider enforces it.

For PaperBench's `bridging-data-gaps`, the inventory still records broken original links for required larger Babies/Sunglasses FID datasets and unresolved alternative/checkpoint compatibility. Preserve that blocker until it is resolved or the task is explicitly changed. Do not hide it to obtain a clean-looking batch.

## Next steps

1. Freeze each full task packet and completion rule. Attach actual ProgramBench specifications, deck views, papers and HLE images, rather than generic benchmark descriptions.
2. Freeze the intended task resources and prepare matching forecast software contexts. Describe GPUs explicitly; do not rent them for forecasts.
3. Qualify the native subscription route and per-adapter input delivery and isolation, using disposable non-study inputs.
4. Dispatch one forecast per slot, preserving every outcome. Later, verify actual natural-run hardware and start from independent pristine copies, never from forecast state.

Forecasts cannot set natural runtime, resource allocation, priority or timed requests. Missing or invalid forecasts do not remove otherwise valid natural measurements. The existing batch remains historical evidence of a different condition.

## Reproduction and review

These scripts are diagnostics, not the autonomous forecast runner. Copy them to a new temporary directory before running; do not overwrite this evidence. `audit_requirements.py` uses the repository's Python environment, PyYAML and read-only Docker inspection, with the repository path pinned in the script. It streams hashes and caches repeated file hashes. `probe_clean_clones.py` requires the recorded cached image and the verified Linux CLI at `linux-cli/claude`. It creates and removes only its own labelled containers. The original fixture capture paths remain in the records. Debug `latest` symlinks are recorded in `symlink-targets.json` rather than recreated; native transcript and request files retain their exact bytes.

The independent in-process Codex review found no design blockers and recommended clarifying controller-side versus subject-side setup time. That clarification is included. This was not an Opus review and does not establish runtime readiness.
