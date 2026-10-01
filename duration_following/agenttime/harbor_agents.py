"""Harbor 0.22.0 agents for the duration-following runs.

Each class is Harbor's own ClaudeCode or Codex agent. Harbor installs the CLI and builds
its one native run command; we check that command against harness.py, rewrite it into
the exact command that ran, and run it under supervisor.py (root-owned, read-only, run
as the agent user). A natural end returns normally. At the backstop we raise
BackstopReached, a NonZeroAgentExitCodeError, so Harbor still runs the benchmark's
verifier on what the agent left. Any other end raises SupervisionError, which Harbor
does not grade. Job kwargs: version, reasoning_effort="max", config, backstop_sec,
process_policy.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import re
import shlex
import tempfile
import uuid

from harbor.agents.installed.base import BaseInstalledAgent, NonZeroAgentExitCodeError
from harbor.agents.installed.claude_code import ClaudeCode
from harbor.agents.installed.codex import Codex

from . import clock_mcp, harness, supervisor

_PYTHON_PROBE = "python3 -c " + shlex.quote(
    "import sys, os; sys.exit(0 if sys.version_info >= (3, 9) and hasattr(os, 'waitstatus_to_exitcode') else 1)")


class BackstopReached(NonZeroAgentExitCodeError):
    """The hidden backstop ended the run; Harbor still grades it."""


class SupervisionError(RuntimeError):
    """The run neither ended naturally nor at the backstop; Harbor skips grading."""


class _Supervised:
    _RUN_MARKER: str
    # Defaults: most coding-task runs used owned-quiescence, all GPQA/HLE runs
    # strict-descendants; a job can pass process_policy to match a specific run.
    PROCESS_POLICY = supervisor.STRICT
    # Reinstall python3 when _PYTHON_PROBE finds no 3.9+ interpreter for the supervisor.
    SYSTEM_PACKAGES = {**BaseInstalledAgent.SYSTEM_PACKAGES, "python3": replace(
        BaseInstalledAgent.SYSTEM_PACKAGES["python3"], always_install=True)}

    def __init__(self, *args, backstop_sec: float, process_policy: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.backstop_sec = float(backstop_sec)
        self.process_policy = process_policy or self.PROCESS_POLICY
        self.control_dir = f"{harness.CONTROL_ROOT}/{uuid.uuid4().hex}"
        self.receipt: dict | None = None
        self._intercepted = False

    async def setup(self, environment) -> None:
        if (await environment.exec(command=_PYTHON_PROBE, timeout_sec=30)).return_code != 0:
            await self.ensure_system_dependencies(environment, ("python3",))
            if (await environment.exec(command=_PYTHON_PROBE, timeout_sec=30)).return_code != 0:
                raise SupervisionError("the supervisor needs python3 3.9 or newer")
        await super().setup(environment)

    async def run(self, instruction, environment, context) -> None:
        try:
            await super().run(instruction, environment, context)
        except (BackstopReached, SupervisionError):
            raise
        except asyncio.CancelledError as exc:  # Harbor's outer timeout (backstop + 60 s)
            raise SupervisionError("the supervised run was interrupted") from exc
        except Exception as exc:  # Harbor grades NonZeroAgentExitCodeError, so wrap everything else
            raise SupervisionError(f"the run failed outside the supervised command: {exc}") from exc
        if not self._intercepted:
            raise SupervisionError("the native command was never run")

    def native_command(self, command: str) -> str:
        raise NotImplementedError

    def terminal_config(self) -> dict:
        """How the supervisor checks the CLI's own final event (see supervisor.terminal_ok)."""
        raise NotImplementedError

    async def install_files(self, environment, files: dict[str, bytes], mode: str) -> None:
        """Upload files into control_dir, owned by root and read-only to the agent."""
        directory = shlex.quote(self.control_dir)
        await super().exec_as_root(environment, command=f"mkdir -p {directory} && chmod 755 {directory}")
        with tempfile.TemporaryDirectory() as temporary:
            for name, data in files.items():
                local = Path(temporary) / name
                local.write_bytes(data)
                await environment.upload_file(local, f"{self.control_dir}/{name}")
        targets = " ".join(shlex.quote(f"{self.control_dir}/{name}") for name in files)
        await super().exec_as_root(environment, command=f"chown root:root {targets} && chmod {mode} {targets}")

    async def exec_as_agent(self, environment, command: str, env=None, cwd=None, timeout_sec=None):
        if self._RUN_MARKER not in command:
            return await super().exec_as_agent(environment, command, env=env, cwd=cwd, timeout_sec=timeout_sec)
        if self._intercepted:
            raise SupervisionError("the native command was invoked twice")
        self._intercepted = True
        receipt_path = f"{self.environment_logs_dir}/duration-receipt.json"
        config = {"command": self.native_command(command), "backstop_sec": self.backstop_sec,
                  "cleanup_grace_sec": 5.0, "process_policy": self.process_policy,
                  "receipt_path": receipt_path, **self.terminal_config()}
        await self.install_files(environment, {"supervisor.py": Path(supervisor.__file__).read_bytes()}, "555")
        await self.install_files(environment, {"config.json": json.dumps(config).encode()}, "444")
        try:
            result = await super().exec_as_agent(
                environment, f"python3 {self.control_dir}/supervisor.py {self.control_dir}/config.json",
                env=env, cwd=cwd, timeout_sec=None)
        except NonZeroAgentExitCodeError:
            result = None  # the receipt says what happened
        with tempfile.TemporaryDirectory() as temporary:
            local = Path(temporary) / "receipt.json"
            await environment.download_file(receipt_path, local)
            self.receipt = json.loads(local.read_text())
        elapsed_ms, backstop_ms = self.receipt["elapsed_ms"], self.backstop_sec * 1000
        if self.receipt["timed_out"] and elapsed_ms and backstop_ms <= elapsed_ms <= backstop_ms + 30_000:
            raise BackstopReached(f"hidden backstop of {self.backstop_sec:g} s reached")
        if self.receipt["error_code"] is not None:
            raise SupervisionError(self.receipt["error_code"])
        return result


class FableClaudeCode(_Supervised, ClaudeCode):
    """Claude Code with claude-fable-5-1 on coding and agentic tasks.

    Extra kwargs aux_model_env, no_delegation and add_dir select the command
    variants described in harness.claude_command.
    """
    _RUN_MARKER = harness.CLAUDE_RUN_MARKER
    PROCESS_POLICY = supervisor.QUIESCENCE

    def __init__(self, *args, aux_model_env: bool = False, no_delegation: bool = False,
                 add_dir: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.session_id = str(uuid.uuid4())
        self.aux_model_env = aux_model_env
        self.no_delegation = no_delegation
        self.add_dir = add_dir

    def _instruction_var(self, command: str) -> str:
        names = set(re.findall(r"\bharbor_claude_code_instruction_[0-9a-f]{32}\b", command))
        if len(names) != 1 or command != harness.harbor_claude_command(next(iter(names))):
            raise SupervisionError("Harbor's Claude Code command has an unexpected shape")
        return names.pop()

    def native_command(self, command: str) -> str:
        return harness.claude_command(self._instruction_var(command), self.session_id,
                                      aux_model_env=self.aux_model_env, no_delegation=self.no_delegation,
                                      add_dir=self.add_dir)

    def terminal_config(self) -> dict:
        return {"terminal_protocol": "claude-code", "log_path": f"{harness.LOG_DIR}/claude-code.txt",
                "expected_session_id": self.session_id, "expected_model": harness.CLAUDE_MODEL}


class FableClaudeCodeClock(FableClaudeCode):
    """GPQA and HLE: the agenttime_clock MCP server is the only tool.

    system_prompt is "" for GPQA (Claude Code's default system prompt replaced by an
    empty file) and benchmarks.HLE_SYSTEM_PROMPT for HLE. The CLI runs from a
    root-owned copy with a clean HOME, and /home/agent is sealed.
    """

    PROCESS_POLICY = supervisor.STRICT

    def __init__(self, *args, system_prompt: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.system_prompt = system_prompt
        self.system_prompt_file = "native-system-prompt.txt" if system_prompt else "empty-system-prompt.txt"
        self._sealed = False

    async def setup(self, environment) -> None:
        await super().setup(environment)
        directory = shlex.quote(self.control_dir)
        await self.exec_as_root(environment, command=(
            f"mkdir -p {directory} && chmod 755 {directory} && mkdir {directory}/home && "
            f"chown 1000:1000 {directory}/home && chmod 700 {directory}/home && "
            "agenttime_claude=$(readlink -f /home/agent/.local/bin/claude) && "
            f'install -o root -g root -m 0555 "$agenttime_claude" {directory}/claude && '
            "chown root:root /home/agent && chmod 000 /home/agent"))
        self._sealed = True

    def native_command(self, command: str) -> str:
        return harness.claude_clock_command(self._instruction_var(command), self.session_id,
                                            self.control_dir, self.system_prompt_file,
                                            aux_model_env=self.aux_model_env)

    async def exec_as_agent(self, environment, command: str, env=None, cwd=None, timeout_sec=None):
        if self._RUN_MARKER in command:
            settings = json.dumps(harness.CLAUDE_CLOCK_SETTINGS, sort_keys=True, separators=(",", ":"))
            await self.install_files(environment, {
                "closed-book-settings.json": settings.encode() + b"\n",
                self.system_prompt_file: self.system_prompt.encode(),
                "clock_tool.py": Path(clock_mcp.__file__).read_bytes(),  # the file name the runs used
                "clock-mcp.json": json.dumps(clock_mcp.mcp_config(f"{self.control_dir}/clock_tool.py"),
                                             sort_keys=True, separators=(",", ":")).encode() + b"\n",
            }, "444")
        env = {**(env or {}), **harness.CLAUDE_CLOCK_ENV}
        if self._sealed:
            env["HOME"] = f"{self.control_dir}/home"
        return await super().exec_as_agent(environment, command, env=env, cwd=cwd, timeout_sec=timeout_sec)


class SolCodex(_Supervised, Codex):
    """Codex with gpt-5.6-sol (Codex 0.151.0 on coding tasks).

    no_delegation=True adds harness.NO_DELEGATION_FLAGS (sub-agents off), as on
    AppWorld and ALE-Bench.
    """
    _RUN_MARKER = harness.CODEX_RUN_PREFIX
    PROCESS_POLICY = supervisor.QUIESCENCE
    FLAGS = harness.SOL_FLAGS

    def __init__(self, *args, no_delegation: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        if no_delegation:
            if self.FLAGS != harness.SOL_FLAGS:
                raise ValueError("no_delegation applies to SolCodex only")
            self.FLAGS = harness.SOL_FLAGS + " " + harness.NO_DELEGATION_FLAGS

    def build_cli_flags(self) -> str:
        if super().build_cli_flags() != harness.SOL_FLAGS:
            raise SupervisionError("reasoning_effort must be max")
        return self.FLAGS

    async def run(self, instruction, environment, context) -> None:
        self._instruction = instruction
        await super().run(instruction, environment, context)

    def native_command(self, command: str) -> str:
        model = self.model_name.split("/")[-1]
        if command != harness.codex_command(self._instruction, model, self.FLAGS):
            raise SupervisionError("Harbor's Codex command has an unexpected shape")
        return command

    def terminal_config(self) -> dict:
        return {"terminal_protocol": "codex", "log_path": f"{harness.LOG_DIR}/codex.txt"}


class AstraCodex(SolCodex):
    """Codex 0.153.4 with gpt-6-astra and sub-agents disabled."""
    FLAGS = harness.ASTRA_FLAGS


class CodexClock(SolCodex):
    """GPQA and HLE in Codex 0.153.4 (Sol or Astra): the native clock.curr_time is the only tool.

    models_cache_path: Codex's own models_cache.json for this CLI version, from which
    the clock-only model catalog is derived. system_prompt: "" for GPQA,
    benchmarks.HLE_SYSTEM_PROMPT for HLE.
    """
    FLAGS = harness.CLOCK_FLAGS
    PROCESS_POLICY = supervisor.STRICT

    def __init__(self, *args, models_cache_path: str, system_prompt: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self.catalog = harness.codex_clock_catalog(
            json.loads(Path(models_cache_path).read_text()), self.model_name.split("/")[-1])
        self.system_prompt = system_prompt

    async def setup(self, environment) -> None:
        await super().setup(environment)
        await self.install_files(environment, {"clock-model-catalog.json": self.catalog}, "444")

    def _build_effective_config(self, openai_base_url=None) -> dict:
        return harness.codex_clock_config(self.model_name.split("/")[-1],
                                          f"{self.control_dir}/clock-model-catalog.json", self.system_prompt)
