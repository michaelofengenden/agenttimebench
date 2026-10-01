"""Exact command lines per cell (model x harness), as frozen for the run.

Reasoning effort is pinned to xhigh for every turn (CLI defaults differ: Codex/Sol `none`, Claude/Opus `high`).
`{prompt}` is the prompt text itself, never a path. Each row gets a fresh CLI home (`home_env`); only the
variables in `env` pass through from the host. Claude Code read its login token from CLAUDE_CODE_OAUTH_TOKEN;
Codex used the login stored in its CODEX_HOME.
CLI versions: claude-code 2.1.220, codex-cli 0.146.0.
"""

CLAUDE_FORK = ["--output-format", "json", "--effort", "xhigh"]
CODEX = ["-c", "model_reasoning_effort=xhigh", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox"]
CODEX_NO_TOOLS = ["--disable", "shell_tool", "--disable", "unified_exec", "--disable", "web_search"]

CELLS = {
    "opus-claude": {
        "home_env": "CLAUDE_CONFIG_DIR",
        "env": ["CLAUDE_CONFIG_DIR", "HOME", "PATH", "TERM", "LANG"],
        # execution and forecast use the same command
        "run": ["claude", "-p", "{prompt}", "--model", "claude-opus-5", "--effort", "xhigh",
                "--dangerously-skip-permissions"],
        # retrospection: turn 1 forks the parent session, turn 2 resumes the fork
        "fork": {
            "native": (["claude", "-p", "{prompt}", "--resume", "{parent_session_id}", "--fork-session", *CLAUDE_FORK],
                       ["claude", "-p", "{prompt}", "--resume", "{fork_session_id}", *CLAUDE_FORK]),
            "context-only": (
                ["claude", "-p", "{prompt}", "--resume", "{parent_session_id}", "--fork-session", "--tools", "",
                 *CLAUDE_FORK],
                ["claude", "-p", "{prompt}", "--resume", "{fork_session_id}", "--tools", "", *CLAUDE_FORK]),
            "elapsed-oracle": (
                ["claude", "-p", "{prompt}", "--resume", "{parent_session_id}", "--fork-session", "--output-format",
                 "json", "--allowedTools", "mcp__elapsed__elapsed_seconds", "--effort", "xhigh"],
                ["claude", "-p", "{prompt}", "--resume", "{fork_session_id}", "--output-format", "json",
                 "--allowedTools", "mcp__elapsed__elapsed_seconds", "--effort", "xhigh"]),
        },
        # elapsed-oracle: MCP server registered in <home>/.claude.json
        "oracle_config": (".claude.json", {"mcpServers": {"elapsed": {
            "command": "{python}", "args": ["{oracle_tool}"], "env": {"ELAPSED_SECONDS": "{elapsed_seconds}"}}}}),
        "replay": {"url": "https://api.anthropic.com/v1/messages", "format": "anthropic", "model": "claude-opus-5",
                   "fields": {"max_tokens": 64000, "thinking": {"type": "adaptive", "display": "omitted"},
                              "output_config": {"effort": "xhigh"}},
                   "role_map": {"developer": "user", "system": "user", "tool": "user", "function": "user"}},
    },
    "sol-codex": {
        "home_env": "CODEX_HOME",
        "env": ["CODEX_HOME", "HOME", "PATH", "TERM", "LANG"],
        "run": ["codex", "exec", *CODEX, "--model", "gpt-5.6-sol", "{prompt}"],
        # Codex has no non-interactive fork: `codex exec resume` runs inside a per-arm copy of CODEX_HOME,
        # and turn 2 resumes the same session id inside that copy.
        "fork": {
            "native": (["codex", "exec", "resume", *CODEX, "{parent_session_id}", "{prompt}"],) * 2,
            "context-only": (["codex", "exec", "resume", *CODEX, *CODEX_NO_TOOLS, "{parent_session_id}",
                              "{prompt}"],) * 2,
            "elapsed-oracle": (["codex", "exec", "resume", *CODEX, "{parent_session_id}", "{prompt}"],) * 2,
        },
        # elapsed-oracle: [mcp_servers.elapsed] in <home>/.codex/config.toml
        "oracle_config": (".codex/config.toml", {"mcp_servers": {"elapsed": {
            "command": "{python}", "args": ["{oracle_tool}"], "env": {"ELAPSED_SECONDS": "{elapsed_seconds}"}}}}),
        "replay": {"url": "https://api.openai.com/v1/chat/completions", "format": "openai", "model": "gpt-5.6-sol",
                   "fields": {"reasoning_effort": "xhigh"},
                   "role_map": {"developer": "system", "tool": "user", "function": "user"}},
    },
}


def expand(argv, values):
    """Fill {placeholders} in an argv template."""
    out = []
    for arg in argv:
        for name, value in values.items():
            arg = arg.replace("{" + name + "}", value)
        out.append(arg)
    return out
