# AgentTime v1.1

Measure how long agents work naturally, then how they respond when asked to work for less or more time.

**The unit is one agent: 220 tasks × 3 runs = 660 planned runs.** Add as many agents as needed. The total follows from the roster; it is not a fixed study size.

| Run for each task | What the agent receives | When |
| --- | --- | --- |
| Natural, or M | Task only, with no duration request or experiment time cap | First |
| Short | The task plus a request to work for one quarter of the eventual average natural runtime | Later |
| Long | The task plus a request to work for four times that average | Later |

**No M values or timed durations are set yet.** M is the observed natural runtime of an attempt. After enough agents have run, use comparable natural measurements from the old and new versions to decide the per-task average for the timed requests. Old duration-prompted runs and externally stopped runs do not qualify as natural finishes. The natural run is also the calibration observation; there is no fourth run.

The concurrency target is **220 active task attempts, using one evaluated agent configuration at a time**. Qualification and provider capacity still need to be demonstrated. Any allowed infrastructure replacement is recorded separately from the 660 planned study slots.

## What is here

This is the new repository foundation. It contains the selected task allocation, an extensible agent list, the protocol decisions, a short build plan, and a working offline planning command. Execution, benchmark downloads and cloud provisioning are not implemented yet.

```text
configs/             Task mix, agents, experiment rules and calibration state
benchmarks/          Exact candidates, replacements, then source locks and patches
src/agenttime/       Working planning command; runner and adapters come next
tests/               Offline checks for counts and configuration mistakes
docs/                Build plan, protocol, v1 lessons and source provenance
runs/                Local run outputs, excluded from Git
```

Start with the [five-step build plan](docs/BUILD_PLAN.md), the [selected task mix](docs/TASK_MIX.md), or the [experiment rules](docs/PROTOCOL.md).

## Try the foundation

Python 3.11 or newer. No third-party packages, network access or credentials are needed for these commands.

```sh
PYTHONPATH=src python3 -m agenttime plan --agent opus-5.5-max
PYTHONPATH=src python3 -m agenttime plan --json
python3 -m unittest discover -s tests -v
```

Add an entry to [agents.json](configs/agents.json) to include another agent in planning. An agent is a model plus its runner, settings and tools, so changed configurations get distinct IDs. The initial Opus, Sol and MiMo entries are candidates whose execution routes still need qualification.

Earlier architecture documents used the working name “v2”. This repository is **AgentTime v1.1**. The [source record](docs/PROVENANCE.md) preserves that history and explains which decisions have since changed.
