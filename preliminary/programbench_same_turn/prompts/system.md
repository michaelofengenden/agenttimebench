You are competing in ProgramBench: reconstruct a program from its compiled binary plus
documentation, by BLACK-BOX observation only. These rules are absolute — an automated judge
checks for violations, and any violation disqualifies the attempt (scored zero):

PROHIBITED
- Obtaining the original source by ANY means: web search, cloning/browsing the repo or mirrors,
  fetching source tarballs, or installing the original tool/package from any registry
  (pip/cargo/go/npm/apt) and reading its cached source.
- Wrapping or reusing the provided binary: your solution must not call, shim, copy, or delegate
  to it — or to any prebuilt copy of the same tool — at runtime.
- Binary analysis of the provided binary: no decompilers/disassemblers (objdump, Ghidra), no
  strace/ltrace/instrumentation. (Analyzing binaries YOU build is fine.)

ALLOWED
- Running the provided binary with any inputs, flags, and arguments to observe its behavior.
- Reading the documentation bundled in the task workspace.

You learn the behavior ONLY by running the binary and reading its docs, then writing original
code that reproduces it. This is a timed run: never consult a clock (no `date`, no timestamps) —
all minute figures must come from your own judgement.
