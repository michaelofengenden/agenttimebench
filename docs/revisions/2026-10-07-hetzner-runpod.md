# A 200-task suite for Hetzner and RunPod

The 7 October revision keeps 200 tasks across 18 benchmark families. Five task replacements remove the selected Windows and T4 dependencies. PaperBench's separate reproduction worker moves from A10 to H100. The intended fleet uses only Hetzner and RunPod; actual worker environments and capacity still need qualification.

| Removed task | Replacement | New slot |
| --- | --- | --- |
| ALE Odoo (`ale-02`) | Apple FY2024 financial statement reconstruction | `ale-13` |
| ALE KiCad (`ale-08`) | Chisel-to-Verilog source alignment | `ale-14` |
| ALE Gaussian splatting (`ale-11`) | Molecular structure plausibility | `ale-15` |
| CORE T4 capsule 3449234 (`core-04`) | Mainline CPU capsule 2816027 | `core-13` |
| CORE T4 capsule 7350043 (`core-11`) | OOD CPU capsule 9419423 | `core-14` |

The other 195 task identities stay unchanged. Retired slot IDs are never reused. CORE retains four mainline and eight OOD tasks. The [selection receipt](../../benchmarks/inventory/evidence/selection-changes/2026-10-07-hetzner-runpod.json) records exact identities, source pins and roster history. These choices were fixed before the new forecasts and use source requirements and evaluator checks, not model performance or forecast values.

There is still real 3D work. The new molecular task supplies 54 XYZ structures and asks the agent to assess geometry, including distances, angles and impossible configurations. Terminal-Bench's `cad-model` still asks for a 3D part reconstructed from a 2D schematic. Molecular geometry does not replace the removed task's visual-media creation coverage.

The source audit rejected A/B, ABB robot, Basel BIA and prostate-planning alternatives because of concrete evaluator or input problems. OpenROAD's additional twelve-pass constraint was avoided. The chosen tasks still need native qualification: passing scorer controls is evidence about the tested cases, not a claim that a benchmark is flawless.

PaperBench retains its four papers, original reproduction rules, private unchanged rubric and native seven-day external reproduction maximum. Its fresh reproduction contract is one H100 80 GB, 16 CPU units, 128 GiB RAM and 400 GB disk. Only the hardware sentence in its forecast description changes. The subject's natural work remains uncapped. This is an AgentTime grading adaptation, so results must not be described as an unchanged native PaperBench configuration.

The intended allocation is 192 Hetzner candidates and eight RunPod H100 research subjects, with separate H100 reproduction grading. The Hetzner count includes 32 closed-book controller tasks. Native CPU and memory allocations take precedence over the ordinary baseline. No Windows, T4 or L4 requirement remains in these subject profiles. See the [provider plan](../NATURAL_RUN_PROVIDERS.md) for the distinction between requirements and available capacity.

The [current canonical forecast view](../../runs/canonical-opus-5.5-max-20261007/README.md) selects 191 unchanged sessions and nine fresh sessions: five replacement tasks plus the four PaperBench descriptions. Its [manifest](../../runs/canonical-opus-5.5-max-20261007/selection-manifest.json) binds every row to its actual source attempt, native session, request, task hashes and archive. Historical batches and their format flags remain intact. This is not a new 200-call batch. See the [results record](../results/OPUS_5_5_FORECASTS.md) for the verified counts and analysis views.

The [new natural preparation](../../runs/natural-opus-5.5-max-preparation-20261007/README.md) contains 200 clean contracts derived from those exact common inputs. Forecast answers, transcripts and session state are excluded. Each natural session must be initialized from pristine sources. No forecast sets a deadline, resource allocation, task order or M.

Verification evidence is retained in the [inventory receipt](../../benchmarks/inventory/verification-revision-20261007.json), [native scorer controls](../../runs/forecast-opus-5.5-max-revision-20261007/evidence/grader-controls/grader-control-receipt.json), [forecast selection audit](../../runs/canonical-opus-5.5-max-20261007/final-audit.json) and [revision verification](../../runs/canonical-opus-5.5-max-20261007/revision-verification.json). The inventory and website, suite/protocol configuration, task mix, build plan, provider plan, result registry and natural preparation all record the change.

The scorer controls preserve native partial credit. Chisel needs read-only inputs and enforced task-network restrictions, and its native scorer has a malformed-object error case and an unchecked location field. The molecular scorer uses Jaccard partial credit despite a stale binary description. These limitations are recorded rather than silently changed. References and answer keys stay outside forecaster and subject inputs.

No natural task or cloud resource was launched. The next step is to connect Harbor to the durable worker and qualify the real task, grader, timing and archive path. The outstanding `paper-01` dataset/checkpoint sources, OSWorld setup, provider resource equivalence, nested Docker and archive restoration remain explicit holds in the [natural-run plan](../NATURAL_RUN_PREPARATION.md).
