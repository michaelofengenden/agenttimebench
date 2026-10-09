# AutomationBench adapter code review, round 1

Final bounded re-review of the standalone model-free adapter. No remaining material findings in the reviewed repair deltas. No model, account, provider, campaign or real agent calls were made.

## Frozen evidence

- `src/agenttime/automationbench/backend.py`: `6aea8f3dccc6c5c2c6f3ef383057f59f32a848bdf6e4aedea81f2e130638da36`
- `src/agenttime/automationbench/native.py`: `9250b2a9275812e1a641ebfa85a52d537f20690d8d4d65d07a3f9af3537b0517`
- `src/agenttime/automationbench/_pin.py`: `14716cdba7d190e0c4b5d6cbb53c401ccf2c8f568791bcc99b8734c8fa1bf6bf`
- `tests/test_automationbench.py`: `d23696dacb21981ab05d5da12e8b4a303e737ab6c57a90a773b7086193a19d14`
- `qualification/automationbench/model-free-qualification.json`: `a33f1c46e90dceb971a664f5d3dad02d19e8dbc867427f6f245a0522c5877b3b`

All 12 file hashes in the final qualification receipt were independently checked and matched. The upstream source commit remains `4a8e1061254004d9dac807054eed33fad7d1ff14`.

## Repairs checked

1. The original receipt-integrity failure is fixed. Restore binds the constructed initial state through every before/after receipt hash to the current snapshot. Re-signed current-state mutations are rejected, including checkpoints with no calls.
2. The original clock failure is fixed. Each restored owner gets a new clock domain. Lower monotonic timestamps on a new owner are accepted, while ordering remains checked within each domain.
3. The original schema failure is fixed. Restore requires exact integer schema 2 and rejects unknown, missing, boolean and pre-clock variants.
4. The additional seal race found during this re-review is fixed in `backend.py:265-294`. The earlier implementation checked a live closing flag before capture. A two-thread probe let seal close admission after that check but before capture, producing `resumable=true`, `closing=true`, `sealed=null`; restore rejected the resulting checkpoint. The final implementation classifies the exact captured payload using its seal snapshot and event. That interleaving now rejects checkpoint creation and marks diagnostic output non-resumable. A payload captured wholly before closure remains restorable. Early outside-lock admission closure is preserved.
5. Root's cold-source pin finding is fixed in `native.py:18-84`. Only the optional generated search index is omitted from the runtime tree digest. If present, its bytes must exactly match deterministic generation from the pinned schemas; its own digest is also pinned. Cold and warmed source trees now share runtime digest `58b189a8c2a37c9ff085c2ec268cadca846665468e32cbca3e3c98bd1ff914f8`. A corrupted cache is rejected. The derivation was compared with the pinned upstream `_load_schemas`, `_build_index_line` and `_regenerate_index` implementation.

## Independent verification

Eight focused tests were freshly run with the optional native Python 3.13 environment and pinned source. All eight passed, with no skips:

- Cold/warm source pin equality and corrupt-cache rejection.
- Checkpoint restoration and tampering.
- A seal already requested before checkpoint creation.
- Seal arriving during capture, for both checkpoint and diagnostic output.
- A snapshot captured before closure.
- Constructed-initial/receipt/current consistency, including zero calls.
- Fresh monotonic clock domains after restore.
- Unsupported and pre-clock schema rejection.

Command used the `test_automationbench.SourcePinTests` class and the seven corresponding `AdapterTests` cases with `PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH=src:tests`, and `AT_AUTOMATIONBENCH_SOURCE` pointing at the pinned download. Result: `Ran 8 tests in 2.401s; OK`.

The final receipt separately records the executor's complete native and Harbor test runs. This approval does not qualify production dispatch, deployed network isolation, durable recovery between checkpoints, native terminal timing, private Harbor verifier wiring or full session archives. Those limits remain explicit in the receipt.

VERDICT: APPROVED
