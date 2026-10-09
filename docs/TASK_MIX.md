# Selected task mix

This allocation provides 200 task places across 18 families. Every place has an exact candidate ID in the source inventory. The inventory retains the distinction between earlier proposals, draft choices and approved replacements; native qualification remains incomplete.

| Family | Places | Preparation |
| --- | ---: | --- |
| Terminal-Bench 4.0 | 16 | Qualify the recorded 16-task roster and its replacement verifiers. |
| DeepSWE | 14 | Qualify the recorded 14-task roster, replacement for OPA and two added candidates. |
| ProgramBench | 18 | 8 retained tasks + 10 Hard candidates. |
| OSWorld 2.1 | 18 | Use the 18 recorded OSWorld 2.1 IDs; verify task 092 and resolve task 029 setup. |
| AppWorld | 14 | Qualify the recorded 14-task sample and its app coverage. |
| PPTArena | 10 | Keep a fixed judging protocol. |
| Agents’ Last Exam | 12 | Qualify nine retained tasks and the approved finance, Chisel and molecular-geometry replacements. |
| TUA-Bench | 10 | Qualify the ten recorded task IDs. |
| AssistantBench | 8 | 8 places selected; the qualification decision is still open. |
| BrowseComp | 12 | Audit and qualify the 12 recorded task IDs and web access. |
| CORE-Bench Hard | 12 | Keep Hard: 4 mainline + 8 out-of-distribution after two approved CPU replacements; qualification remains open. |
| PaperBench | 4 | Preserve the task identities and private rubric; qualify the separate H100 fresh-reproduction contract. |
| PostTrainBench | 4 | Use the four recorded v1.2 candidates; qualify the Harbor adapter, grading and uncapped natural objective. |
| Sakana ALE-Bench | 10 | Keep ten; verify effective limits and compute quotas. |
| YC-Bench | 4 | Qualify the four recorded seeds; preserve the one-year or bankruptcy endpoint. |
| HLE Diamond | 20 | Diamond: random 10 reasoning + 10 knowledge, with existing subject exclusions. |
| GPQA Diamond | 12 | Qualify the 12 recorded Diamond controls. |
| METR public tasks | 2 | Keep Payments and Cowthello; recalibrate on the new models. |

WeirdML was removed on 2 October 2026 because no complete public runnable package was located. No replacement tasks are being added. Its downloaded prompts and research notes remain as history. WildClawBench and SimpleQA also have zero places in this mix.

The [candidate file](../benchmarks/candidates.json) preserves the selected replacements and all 18 ProgramBench IDs. Eight ProgramBench tasks are retained and ten additions come from the Hard pool. FFmpeg and PHP remain pilots only.

AssistantBench and CORE-Bench qualification decisions remain open. Existing candidates are not automatically admitted by copying their names here.

The [OSWorld 2.1 release pins](../benchmarks/releases/osworld-2.1/selection.json) record the matching code, tasks, assets, website and image references. Native qualification remains pending.

The [PostTrainBench v1.2 assessment](../benchmarks/releases/posttrainbench-1.2/assessment.json) records the recommended upstream base and inspected commit. Its official Harbor adapter still defaults to a ten-hour budget and partial workspace export. Natural timing, complete session archives and final score handling need qualification. The inventory records HumanEval, GSM8K, AIME2025 and ArenaHardWriting candidates. All four still need native qualification.

## Source inventory and draft IDs

The [200-place source inventory](../benchmarks/inventory/README.md) records actual local files, exact draft identities and remaining preparation for every family. The [searchable review](../benchmarks/inventory/index.html) keeps drafts separate from earlier choices. These files do not create an executable roster.

On 5 October 2026 the user removed AutomationBench after reviewing the adapted nine-task option. The three earlier recommendations are retired; the six proposed prompt adaptations were not adopted. The suite has 200 tasks across 18 families, with all unrelated task identities unchanged and no replacement tasks requested. Tau-cubed remains excluded. The [audit page](../benchmarks/inventory/automationbench-audit.html), cached sources, earlier candidate records and hash-linked roster history are retained. This selection decision does not imply that every AutomationBench task is flawed.

## Current replacement decision

The [7 October decision](revisions/2026-10-07-hetzner-runpod.md) preserves 200 tasks and the family counts above. These choices use pinned runtime requirements and the static input/evaluator audit, without forecast values or evaluated-agent outcomes.

| Retired slot | Fresh slot | Selected native task |
| --- | --- | --- |
| `ale-02` | `ale-13` | `business_finance/financial_stmt_reconstruction_aapl_fy2024` |
| `ale-08` | `ale-14` | `engineering/chisel_verilog_alignment_seq_1` |
| `ale-11` | `ale-15` | `physical_sciences/molecular_structure_plausibility` |
| `core-04` | `core-13` | Mainline `capsule-2816027` |
| `core-11` | `core-14` | OOD `capsule-9419423` |

The [hash-linked receipt](../benchmarks/inventory/evidence/selection-changes/2026-10-07-hetzner-runpod.json) records every retirement and addition. Retired slots cannot be reused. The other 195 task identities are unchanged.

The molecular task requires reasoning about 3D coordinates, bond distances and angles. The Terminal-Bench CAD task remains. Visual-media creation through Gaussian splatting is no longer covered. The audit rejected A/B, ABB robot, Basel BIA and prostate-planning candidates for evaluator or input problems; OpenROAD's additional twelve-pass constraint was also avoided.

PaperBench `paper-01` through `paper-04` retain their task identities. Fresh reproduction uses a separate H100 80 GB GPU, 16 CPU units, 128 GiB RAM and 400 GB storage, with the native seven-day reproduction maximum. The rubric remains unchanged and private. This AgentTime grading adaptation needs native qualification and does not establish original PaperBench comparability.

The [provider plan](NATURAL_RUN_PROVIDERS.md) assigns 192 CPU/desktop candidates to Hetzner and eight H100 research subjects to RunPod. The Hetzner count includes 32 closed-book controller tasks. The revised requirements contain no Windows, T4 or L4 dependency. Preserve native CPU and memory allocations; the ordinary 4-CPU/16-GiB baseline applies only when native requirements are absent.
