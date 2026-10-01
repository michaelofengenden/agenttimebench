"""Exact native agent commands, flags and settings used for the duration-following runs.

Each Harbor run was one native CLI process that Harbor 0.22.0 started inside the
task container. The functions below return the command strings the supervisor ran.
Not built here: the "Fable 5.1 with fallbacks" runs, which Claude Opus 4.8 completed
after a refusal, left Claude Code's refusal fallback on or were rerun with Opus 4.8.
"""
from __future__ import annotations

import json
import shlex

# Claude Code 2.1.259 linux-amd64 binary: sha256 f7dd62ae415378018cd21dd950eb3bac174ab085830304d3b8b098146bfd47b6.
# Sol used Codex 0.151.0 on agentic tasks and 0.153.4 on GPQA Diamond and HLE; Astra used 0.153.4.
VERSIONS = {
    "fable": {"version": "2.1.259", "model": "claude-fable-5-1", "effort": "max"},
    "sol": {"version": "0.151.0", "model": "gpt-5.6-sol", "effort": "max"},
    "astra": {"version": "0.153.4", "model": "gpt-6-astra", "effort": "max"},
}

# In-container directory for the supervisor, its config and the GPQA/HLE clock files
# (root-owned, read-only to the agent), followed by /<uuid4 hex>.
# Verbatim: GPQA/HLE runs gave it to the agent (Claude Code's HOME and MCP config, Codex's model catalog).
CONTROL_ROOT = "/opt/agenttime-duration-smoke"
LOG_DIR = "/logs/agent"

# ---- Claude Code -------------------------------------------------------------

CLAUDE_MODEL = VERSIONS["fable"]["model"]
CLAUDE_SETTINGS = "/tmp/claude-code-settings/settings.json"  # Harbor uploads {"reasoning_effort": "max"} here
CLAUDE_RUN_MARKER = "claude --verbose --output-format=stream-json "
# Set by the job (Harbor itself sets FORCE_AUTO_BACKGROUND_TASKS=1, ENABLE_BACKGROUND_TASKS=1,
# ANTHROPIC_MODEL, IS_SANDBOX=1, CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 and
# CLAUDE_CONFIG_DIR=/logs/agent/sessions). Authentication: on most routes a Claude subscription
# token (CLAUDE_CODE_OAUTH_TOKEN), on some ANTHROPIC_API_KEY.
CLAUDE_JOB_ENV = {"CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "0"}  # -p waits for its own background tasks
# GPQA and HLE: no background tasks; HOME is the clean CONTROL_DIR/home.
CLAUDE_CLOCK_ENV = {"FORCE_AUTO_BACKGROUND_TASKS": "0", "ENABLE_BACKGROUND_TASKS": "0"}
CLAUDE_CLOCK_SETTINGS = {"disableAllHooks": True, "disableClaudeAiConnectors": True}
CLAUDE_CLOCK_TOOL = "mcp__agenttime_clock__current_time"
NO_DELEGATION_FLAG = "--disallowedTools Task,Agent,Workflow,TeamCreate,TeamDelete,SendMessage "


def harbor_claude_command(instruction_var: str) -> str:
    """The command Harbor 0.22.0 ClaudeCode.run builds for our job settings.

    Harbor passes the instruction in the environment variable instruction_var.upper().
    """
    return (
        'export PATH="$HOME/.local/bin:$PATH"; '
        f'{instruction_var}="${instruction_var.upper()}"; '
        f"unset {instruction_var.upper()}; "
        f'printf "%s" "${instruction_var}" | '
        f"{CLAUDE_RUN_MARKER}--settings {CLAUDE_SETTINGS} --effort max --permission-mode=bypassPermissions "
        f"--print 2>&1 | tee {LOG_DIR}/claude-code.txt"
    )


def claude_command(instruction_var: str, session_id: str, *, model: str = CLAUDE_MODEL,
                   aux_model_env: bool = False, no_delegation: bool = False,
                   add_dir: str | None = None) -> str:
    """Claude Code coding-task command: Harbor's command with a fresh session id, the
    explicit model and no permission prompts, and Claude Code's own refusal fallback off.

    Deployments that authenticated with an API key also pinned Claude Code's auxiliary
    models to the run model (aux_model_env); some benchmarks (e.g. AppWorld, ALE-Bench)
    also disallowed sub-agent tools (no_delegation); WildClawBench added its workspace
    (add_dir="/tmp_workspace").
    """
    prefix = "export CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK=1; "
    if aux_model_env:
        prefix += "unset ANTHROPIC_SMALL_FAST_MODEL; " + "".join(
            f"export {name}={shlex.quote(model)}; " for name in (
                "ANTHROPIC_DEFAULT_HAIKU_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL"))
        prefix += "export CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1; "
    flags = ((f"--add-dir {shlex.quote(add_dir)} " if add_dir else "")
             + f"--session-id {shlex.quote(session_id)} --model {shlex.quote(model)} "
             "--permission-prompts none " + (NO_DELEGATION_FLAG if no_delegation else ""))
    return prefix + harbor_claude_command(instruction_var).replace(
        CLAUDE_RUN_MARKER, CLAUDE_RUN_MARKER + flags, 1)


def claude_clock_command(instruction_var: str, session_id: str, control_dir: str,
                         system_prompt_file: str, *, aux_model_env: bool = False) -> str:
    """GPQA/HLE: the clock is the only tool, the default system prompt is replaced by
    system_prompt_file (empty for GPQA, HLE's own prompt for HLE), and no user settings,
    hooks, slash commands or MCP servers other than the clock are loaded."""
    command = claude_command(instruction_var, session_id, aux_model_env=aux_model_env)
    closed = (
        f"--settings {control_dir}/closed-book-settings.json "
        f"--tools {CLAUDE_CLOCK_TOOL} --allowedTools {CLAUDE_CLOCK_TOOL} --restricted "
        f"--strict-mcp-config --mcp-config {control_dir}/clock-mcp.json --setting-sources '' "
        f"--disable-slash-commands --no-chrome --system-prompt-file {control_dir}/{system_prompt_file} "
    )
    for old, new in (('export PATH="$HOME/.local/bin:$PATH"; ', f"export HOME={control_dir}/home; "),
                     (f" | {CLAUDE_RUN_MARKER}", f" | {control_dir}/{CLAUDE_RUN_MARKER}"),
                     (f"--settings {CLAUDE_SETTINGS} ", closed),
                     ("--permission-mode=bypassPermissions ", "--permission-mode=dontAsk ")):
        if command.count(old) != 1:
            raise ValueError("unexpected Claude command shape")
        command = command.replace(old, new)
    return command


# ---- Codex -------------------------------------------------------------------

CODEX_RUN_PREFIX = "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; codex exec "
SOL_FLAGS = "-c model_reasoning_effort=max"
# Sub-agents off: always for Astra; for Sol where sub-agents were disallowed (e.g. AppWorld, ALE-Bench).
NO_DELEGATION_FLAGS = "-c agents.enabled=false -c features.multi_agent_v2=false -c features.multi_agent=false"
ASTRA_FLAGS = SOL_FLAGS + " " + NO_DELEGATION_FLAGS
CLOCK_FLAGS = SOL_FLAGS + " --strict-config --ignore-rules"
# Authentication: on most routes a ChatGPT login ($CODEX_HOME/auth.json), on some an API key.


def codex_command(prompt: str, model: str, flags: str) -> str:
    """Harbor 0.22.0 Codex.run command; the prompt is one shell-quoted argument."""
    return (CODEX_RUN_PREFIX + "--dangerously-bypass-approvals-and-sandbox --skip-git-repo-check "
            f"--model {model} --json --enable unified_exec {flags} -- {shlex.quote(prompt)} "
            f"2>&1 </dev/null | tee {LOG_DIR}/codex.txt")


CLOCK_DISABLED_FEATURES = (
    "shell_tool", "view_image", "apps", "plugins", "remote_plugin", "tool_suggest",
    "skill_search", "skill_mcp_dependency_install", "multi_agent", "multi_agent_v2",
    "sleep_tool", "current_time_reminder", "goals", "computer_use", "browser_use",
    "browser_use_external", "image_generation", "code_mode", "code_mode_only",
    "hooks", "workspace_dependencies", "token_budget", "deferred_executor",
    "request_permissions_tool",
)
# Model-catalog fields changed so that the native clock is the only tool.
CLOCK_CATALOG_OVERRIDES = {
    "shell_type": "disabled", "apply_patch_tool_type": None,
    "experimental_supported_tools": ["clock"], "tool_mode": "direct",
}
# The model then sees one tool: Codex's namespace "clock" ("Tools for reading and waiting on time.")
# with the function curr_time ("Return the current time in UTC."), which takes no arguments.


def codex_clock_config(model: str, catalog_path: str, system_prompt: str = "") -> dict:
    """$CODEX_HOME/config.toml for GPQA/HLE. HLE's system prompt goes to developer_instructions."""
    config = {
        "model": model, "model_reasoning_effort": "max", "model_catalog_json": catalog_path,
        "agents": {"enabled": False}, "web_search": "disabled", "project_doc_max_bytes": 0,
        "mcp_servers": {}, "suppress_unstable_features_warning": True,
        "projects": {"/workspace": {"trust_level": "trusted"}},
        "tools": {"update_plan": {"enabled": False},
                  "experimental_request_user_input": {"enabled": False}},
        "features": {**dict.fromkeys(CLOCK_DISABLED_FEATURES, False), "skip_host_skill_discovery": True},
    }
    if system_prompt:
        config["developer_instructions"] = system_prompt
    return config


def codex_clock_catalog(models_cache: dict, model: str) -> bytes:
    """Model catalog for GPQA/HLE: Codex's own models_cache.json entry for this
    model with CLOCK_CATALOG_OVERRIDES applied; every other field (instructions,
    reasoning levels) is kept, and all other models are removed."""
    matches = [entry for entry in models_cache["models"] if entry.get("slug") == model]
    if len(matches) != 1:
        raise ValueError(f"expected one catalog entry for {model}")
    catalog = {"models": [{**matches[0], **CLOCK_CATALOG_OVERRIDES}]}
    return (json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
