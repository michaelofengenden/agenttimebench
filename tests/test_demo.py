"""A fresh checkout can demonstrate the real fixture path without credentials."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class DemoTests(unittest.TestCase):
    def test_demo_retains_report_and_manifest_and_refuses_overwriting(self):
        with tempfile.TemporaryDirectory(prefix='agenttime-demo-', dir='/tmp') as temp:
            root = Path(temp) / 'evidence'
            cmd = [sys.executable, '-m', 'agenttime', 'fixture', 'demo', '--root', str(root)]
            first = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            self.assertEqual(first.returncode, 0, first.stderr)
            report = json.loads(first.stdout)
            self.assertEqual(report['finished'], 3)
            self.assertFalse(report['study_launch_ready'])
            self.assertEqual(json.loads((root / 'report.json').read_text()), report)
            self.assertTrue((root / 'manifest.json').is_file())
            self.assertFalse((root / 'database').exists())
            original = (root / 'report.json').read_bytes()
            second = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(second.returncode, 0)
            self.assertEqual((root / 'report.json').read_bytes(), original)
