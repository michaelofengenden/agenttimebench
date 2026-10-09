# AgentTime v1.1 task inventory

This is a preparation inventory for all 200 places, with 5 approved replacements kept separate from earlier choices and draft IDs. No study runs were launched.

[Open the searchable review](index.html) · [All 200 records](tasks.json) · [Draft recommendations](recommendations.json) · [Source manifest](evidence/source-manifest.json)

The cache contains 34,678 verified files (29.15 GB, including source archives and overlapping reference exports). These are actual local copies. They are not 200 qualified execution packages.

| Family | Places | Earlier / mapped | Draft for review | Approved replacements | Original prompt source present |
| --- | ---: | ---: | ---: | ---: | ---: |
| Terminal-Bench 4.0 | 16 | 16 | 0 | 0 | 16 |
| DeepSWE | 14 | 12 | 2 | 0 | 14 |
| ProgramBench | 18 | 18 | 0 | 0 | 18 |
| OSWorld 2.1 | 18 | 16 | 2 | 0 | 18 |
| AppWorld | 14 | 0 | 14 | 0 | 14 |
| PPTArena | 10 | 10 | 0 | 0 | 10 |
| Agents’ Last Exam | 12 | 9 | 0 | 3 | 12 |
| TUA-Bench | 10 | 0 | 10 | 0 | 10 |
| AssistantBench | 8 | 0 | 8 | 0 | 8 |
| BrowseComp | 12 | 0 | 12 | 0 | 12 |
| CORE-Bench Hard | 12 | 10 | 0 | 2 | 12 |
| PaperBench | 4 | 3 | 1 | 0 | 4 |
| PostTrainBench | 4 | 2 | 2 | 0 | 4 |
| Sakana ALE-Bench | 10 | 10 | 0 | 0 | 10 |
| YC-Bench | 4 | 3 | 1 | 0 | 4 |
| HLE Diamond | 20 | 0 | 20 | 0 | 20 |
| GPQA Diamond | 12 | 0 | 12 | 0 | 12 |
| METR public tasks | 2 | 2 | 0 | 0 | 2 |

## Selected allocation

The 7 October revision keeps 200 tasks across 18 families. Five approved CPU-compatible replacements use fresh slots; the other 195 task identities are preserved. CORE retains four mainline and eight OOD capsules.

## Latest asset preparation

- Three ALE public bundles and their verifier-only references are present at pinned revisions.
- Two original CORE capsule archives and their protected native metadata are copied and independently hash-verified.
- Each of the five user-approved replacements has a fresh slot, an explicit retired identity and a hash-linked selection receipt.
- Molecular geometry retains a genuine 3D component alongside the existing Terminal-Bench CAD task; visual-media creation coverage changes.

## What still needs work

- Qualify native environments, subject material separation, grader controls and uncapped natural endpoints before execution.
- Provider allocation remains intended: 192 Hetzner CPU/desktop candidates and eight RunPod GPU research candidates, plus separate GPU grading.
- PaperBench fresh reproduction uses the approved H100 80 GB, 16 CPU units, 128 GiB RAM and 400 GB storage contract; native RunPod reproduction remains unqualified.
- Enforce Chisel read-only input and solve-time network restrictions in the native worker; its scorer alone does not provide these controls.

## Independent review

Opus 5.5 max requested revisions to the inventory checks. The local fixes add complete source checks, stable task identities and clearer provenance. Its packet review is not approval of the runnable suite; native qualification remains ahead.

## Your decisions

- The user approved the five replacements and the separate PaperBench reproduction contract on 7 October. Selection approval does not grant runtime qualification or scientific admission.

## Review and reproduce

Use the search page to filter suggestions or missing components. Open a task for its reason and remaining work. Recommendations were not promoted into an executable roster. Dated selection-change records preserve all retired identities. The 7 October receipt replaces five tasks on fresh slots and holds the other 195 task identities constant. The original 100 earlier candidates remain in historical evidence; 95 remain active after the five recorded retirements.

The evidence folder preserves selection methods, source pins, access results and file hashes. Early access failures and acquisition gaps are historical snapshots; the latest receipts and task rows reflect subsequent source preparation.

Run `python benchmarks/inventory/build_inventory.py` to rebuild the report from saved evidence and the local cache. Run `python benchmarks/inventory/verify_inventory.py` for count, identity, reference and file-integrity checks. The companion notebook exposes the same checks without executing any benchmark.

Keep `benchmarks/cache` private. It includes answer keys and reference material as well as agent inputs. Task packaging must expose only the intended agent inputs.
