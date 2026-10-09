"""Dependency-light public materialization; no agent is launched."""

import tempfile
from pathlib import Path
import unittest

from agenttime.automationbench.harbor import materialize_task, agent_route
from agenttime.automationbench.prompts import TURN_BUDGET


class HarborMaterializationTests(unittest.TestCase):
    def test_public_only_package_and_exact_harness_routes(self):
        import tomllib

        prompt = [
            {"role": "system", "content": "Use tools. " + TURN_BUDGET},
            {"role": "user", "content": "Do the task."},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "task"
            receipt = materialize_task(
                folder,
                prompt,
                mcp_url="http://automationbench:8000/mcp",
                image="python@sha256:" + "a" * 64,
            )
            config = tomllib.loads((folder / "task.toml").read_text())
            self.assertEqual(
                config["environment"]["mcp_servers"][0]["transport"], "streamable-http"
            )
            self.assertNotIn("timeout_sec", config.get("agent", {}))
            self.assertFalse(receipt["study_launch_ready"])
            self.assertEqual(
                set(p.name for p in folder.iterdir()),
                {"instruction.md", "task.toml", "adapter-qualification.json"},
            )
            self.assertEqual(agent_route("codex")["name"], "codex")
            self.assertEqual(agent_route("claude-code")["name"], "claude-code")
            with self.assertRaises(ValueError):
                agent_route("direct-api")
            with self.assertRaises(FileExistsError):
                materialize_task(
                    folder,
                    prompt,
                    mcp_url="http://automationbench:8000/mcp",
                    image="python@sha256:" + "a" * 64,
                )

    def test_config_parses_in_pinned_harbor(self):
        try:
            from importlib.metadata import version
            from harbor.models.task.config import TaskConfig
        except ImportError:
            raise unittest.SkipTest("Optional Harbor runtime not installed")
        self.assertEqual(version("harbor"), "0.23.0")
        prompt = [
            {"role": "system", "content": TURN_BUDGET},
            {"role": "user", "content": "task"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "task"
            materialize_task(
                root,
                prompt,
                mcp_url="http://automationbench:8000/mcp",
                image="python@sha256:" + "a" * 64,
            )
            import tomllib

            config = TaskConfig.model_validate(
                tomllib.loads((root / "task.toml").read_text())
            )
            self.assertIsNone(config.agent.timeout_sec)
            self.assertEqual(config.environment.mcp_servers[0].name, "automationbench")
            # Exercise adapter config rendering only. Bypass constructors so no accounts/environment are read.
            from harbor.agents.installed.codex import Codex
            from harbor.agents.installed.claude_code import ClaudeCode

            codex = object.__new__(Codex)
            codex._base_config = {}
            codex.mcp_servers = config.environment.mcp_servers
            self.assertEqual(
                codex._build_effective_config()["mcp_servers"]["automationbench"][
                    "url"
                ],
                "http://automationbench:8000/mcp",
            )
            claude = object.__new__(ClaudeCode)
            claude.mcp_servers = config.environment.mcp_servers
            command = claude._build_register_mcp_servers_command()
            self.assertIn('"type": "http"', command)
            self.assertIn("http://automationbench:8000/mcp", command)


if __name__ == "__main__":
    unittest.main()
