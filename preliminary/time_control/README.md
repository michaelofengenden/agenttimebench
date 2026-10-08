# Controlling wall-clock time (preliminary study, not in the paper)

Three studies of whether a model can place public text, scheduled output or a saved file at a requested elapsed time.

**API prompt variation** (`api_prompt_variants/`). 288 tool-free OpenRouter requests: GPT-5.6 Sol, GPT-6 Astra, Fable
5.1 and Claude Opus 5.5 x 3 tasks (a food-waste brief, two HLE questions) x 6 wordings x {120, 180} s x 2, with
`reasoning: {effort: "xhigh", exclude: true}`, `max_tokens: 65536` and no provider fallbacks. t runs from client
dispatch to the first non-whitespace public text; cutoff 2T. The concise wording (food-waste brief, 120 s):
```text
Think about the task for exactly 2 minutes. Then begin your final answer. Produce no visible text during that interval.
```
**Native CLI** (`cli_native/`). 159 tool-free cells in eleven families, then 66 cells in tools/no-tools pairs; Codex CLI
0.151.0 (GPT-5.6 Sol, initial study) or 0.153.4 (the rest) via `app-server` and Claude Code 2.1.259 (Fable 5.1), effort
`max` (commands in `capture.py`). The tools arm's only tool is an MCP `shell` (`tool_service.py`) in a container with
`--network none --read-only --cap-drop ALL`; it replaces "Do not use tools." with the line below. Output is stamped on
the host monotonic clock from dispatch; `score.py` uses a 1,000 ms tolerance.
```text
You may use the available command-line tools, including reading and writing files in your task workspace, checking the time, and waiting.
```
**Crazy Eights** (`crazy_eights/`). Six briefs x {GPT-5.6 Sol, GPT-6 Astra}, Codex CLI 0.153.4, effort `max`, `shell`
tool; target 480 s, cutoff 960 s; endpoint: an interval around the last SVG change (`observer.py`). Imaginative brief:
```text
Starting when this complete request becomes available to you, spend exactly 8 minutes developing and drawing exactly eight distinct illustrations.
```
Run every command from this folder; each `analyze` script documents its input columns. The tools study and Crazy Eights
both need `--docker PATH --image ID` (a local Docker image with `python3`):
```sh
python -m api_prompt_variants.build_manifest --out manifest.json            # HLE text needs pyarrow, huggingface_hub
OPENROUTER_API_KEY=... python -m api_prompt_variants.run --manifest manifest.json --out attempts
python -m api_prompt_variants.analyze attempts.csv [--tex api_table.tex]    # Table tab:api-timing-short
python -m cli_native.run --study initial|tools --out DIR [--docker PATH --image ID]
python -m cli_native.analyze runs.csv [--tex cli_table.tex]                 # Table tab:cli-timing-short, the 17 pairs
python -m crazy_eights.run --brief crazy_eights/requests/X.txt --out DIR --docker PATH --image ID
python -m crazy_eights.analyze runs.csv observations.csv svgs/ [--tex drawing_table.tex] [--figures DIR]   # Table tab:drawing-timing-short; SVGs of fig:original-svg-examples-short
```
