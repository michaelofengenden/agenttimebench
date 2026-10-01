# ProgramBench arena — run an agent against tasks, no leakage

A self-contained sandbox for having an agent (e.g. Claude Code) attempt ProgramBench tasks.

## The no-leakage guarantee (by construction)

Every task sandbox is built **only** from two public, leak-free sources:

1. the **cleanroom image** `programbench/<id>:task_cleanroom_v6` — the compiled binary +
   documentation, with all source code stripped (verified: no source in the working tree or
   git history), and
2. the **agent prompt** in [`PROMPT.md`](PROMPT.md).

`pb` **never** reads `../programbench-tests/` (the test archives, which DO contain original
source + golden outputs) or any `tests.json` (test names). `pb add` also asserts the extracted
docs contain zero source files and prints the full inventory, so it's auditable. The original
`./executable` is deliberately **not** copied into the editable workspace (you observe it via
`pb probe`), so a submission can't accidentally ship/wrap it.

## Layout

```
arena/
├── pb                      # the driver (add / probe / build / submit / ls)
├── PROMPT.md               # verbatim agent prompt (the rules you must follow for a valid score)
├── tasks/<id>/
│   ├── IMAGE               # the cleanroom image ref
│   └── workspace/          # docs + .git only; the agent writes source + compile.sh HERE
└── runs/<run>/<id>/submission.tar.gz   # produced by `pb submit`, ready for scoring
```

## Workflow

```bash
./pb add tomnomnom__gron.88a6234         # create a source-free sandbox for a task
./pb probe tomnomnom__gron.88a6234 --help   # observe the original binary (network none)
echo '{"a":[1,2]}' | ./pb probe tomnomnom__gron.88a6234 -m   # pipe stdin
# ...read tasks/<id>/workspace docs, then write source + compile.sh into that workspace...
./pb build tomnomnom__gron.88a6234       # compile your solution in a fresh cleanroom image
./pb submit tomnomnom__gron.88a6234      # -> runs/claude_run/<id>/submission.tar.gz
```

## To have Claude Code attempt a task

Point it at one task and tell it to follow `PROMPT.md` **strictly** (black-box only — no
internet, no cloning, no decompiling/strace, no wrapping `./executable`). It reads the docs in
`tasks/<id>/workspace`, uses `./pb probe <id> …` to interact with the binary, writes a real
reimplementation + `compile.sh` into the workspace, iterates with `./pb build`, then `./pb submit`.

## Scoring (separate, on Linux x86_64)

Scoring needs the test suites + Docker and only runs on a Linux x86_64 host (see `../cloud/`):

```bash
# on the Linux box, with the programbench CLI installed:
programbench eval runs/claude_run        # pulls test images, runs behavioral tests
programbench info  runs/claude_run        # final scores
```

Keep that step on a different machine/checkout from where the agent runs — never let the agent
see `programbench-tests/`.

> Note: `pb probe`/`pb build` run amd64 under emulation on Apple Silicon (slower; some binaries
> may misbehave). Fine for a few tasks; for serious runs do inference on the x86_64 box too.
