"""Exercise the offline CLI with small, independently counted allocations."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name)
        self.suite = {"families": [{"id": "one", "count": 2}, {"id": "two", "count": 3}]}
        self.agents = {"agents": [{"id": "alpha"}, {"id": "beta"}]}
        self.protocol = {
            "arms": [{"id": "natural"}, {"id": "short"}, {"id": "long"}],
            "concurrency": {"active_attempt_limit": 220, "evaluated_agents_at_once": 1},
        }
        self.calibration = {"status": "collect_natural_first", "average_seconds_by_task": {}}

    def run_plan(self, *args):
        for name, data in [("suite", self.suite), ("agents", self.agents),
                           ("protocol", self.protocol), ("calibration", self.calibration)]:
            (self.config / f"{name}.json").write_text(json.dumps(data))
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run(
            [sys.executable, "-m", "agenttime", "plan", "--config-dir", str(self.config),
             "--json", *args], capture_output=True, text=True, env=env, check=False,
        )

    def valid_plan(self, *args):
        result = self.run_plan(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_one_agent_gets_three_runs_per_task_without_extra_calibration(self):
        plan = self.valid_plan("--agent", "alpha")
        self.assertEqual(plan["per_agent"], {"natural": 5, "short": 5, "long": 5, "total": 15})
        self.assertEqual(plan["selected_agent_ids"], ["alpha"])
        self.assertEqual(plan["selected_total_slots"], 15)

    def test_adding_an_agent_changes_only_campaign_size(self):
        before = self.valid_plan()
        self.agents["agents"].append({"id": "gamma"})
        after = self.valid_plan()
        self.assertEqual(before["selected_total_slots"], 30)
        self.assertEqual(after["selected_total_slots"], 45)
        self.assertEqual(before["per_agent"], after["per_agent"])
        self.assertEqual(before["timed_duration_seconds"], after["timed_duration_seconds"])

    def test_changed_task_mix_changes_counts_without_hardcoded_220(self):
        self.suite["families"][1]["count"] = 1
        plan = self.valid_plan("--agent", "beta")
        self.assertEqual(plan["per_agent"]["total"], 9)
        self.assertEqual(plan["maximum_planned_overlap"], 3)

    def test_timed_durations_remain_unset_before_natural_data(self):
        plan = self.valid_plan()
        self.assertEqual(plan["timed_duration_seconds"], {"short": None, "long": None})
        self.assertFalse(plan["execution_implemented"])
        self.assertFalse(plan["launch_ready"])

    def test_unknown_agent_is_an_error(self):
        result = self.run_plan("--agent", "unknown")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown agent", result.stderr)

    def test_duplicate_agent_ids_cannot_inflate_run_counts(self):
        self.agents["agents"].append({"id": "alpha"})
        result = self.run_plan()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Duplicate agent", result.stderr)

    def test_duplicate_selected_agent_is_rejected(self):
        result = self.run_plan("--agent", "alpha", "--agent", "alpha")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Duplicate selected agent", result.stderr)

    def test_invalid_family_counts_are_rejected(self):
        for count in [-1, True, 1.5, "2"]:
            with self.subTest(count=count):
                self.suite["families"][0]["count"] = count
                result = self.run_plan()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("non-negative integer", result.stderr)

    def test_duplicate_families_cannot_inflate_run_counts(self):
        self.suite["families"].append({"id": "one", "count": 2})
        result = self.run_plan()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Duplicate family", result.stderr)

    def test_empty_suite_or_agent_registry_is_rejected(self):
        self.agents["agents"] = []
        self.assertIn("At least one agent", self.run_plan().stderr)
        self.agents["agents"] = [{"id": "alpha"}]
        self.suite["families"] = []
        self.assertIn("At least one task", self.run_plan().stderr)

    def test_unsupported_timed_calculation_is_not_silently_ignored(self):
        self.calibration["average_seconds_by_task"] = {"task-1": 100}
        result = self.run_plan()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("timed calculation", result.stderr)

    def test_multiple_evaluated_agents_at_once_is_rejected(self):
        self.protocol["concurrency"]["evaluated_agents_at_once"] = 2
        result = self.run_plan()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("one evaluated agent", result.stderr)

    def test_extra_timed_middle_arm_is_rejected(self):
        self.protocol["arms"].append({"id": "timed_middle"})
        result = self.run_plan()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("natural, short and long", result.stderr)

    def test_preassigned_duration_is_rejected_in_natural_collection_phase(self):
        self.protocol["arms"][1]["duration_seconds"] = 60
        result = self.run_plan()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("durations must remain unset", result.stderr)


if __name__ == "__main__":
    unittest.main()
