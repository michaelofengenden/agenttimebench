"""Opt-in real Docker trials. No providers, model accounts or study attempts."""
import importlib
import hashlib
import os
from pathlib import Path
import tempfile
import unittest


@unittest.skipUnless(os.environ.get('AGENTTIME_NATIVE_DOCKER') == '1', 'requires explicit local Docker qualification')
class HarborNativeTests(unittest.TestCase):
    temp = None
    report = None

    @classmethod
    def setUpClass(cls):
        try:
            module = importlib.import_module('agenttime.harbor_fixture')
        except ModuleNotFoundError:
            raise AssertionError('Native Harbor qualification is not implemented')
        cls.temp = None
        destination = os.environ.get('AGENTTIME_NATIVE_OUTPUT')
        if destination:
            root = Path(destination)
        else:
            cls.temp = tempfile.TemporaryDirectory(prefix='agenttime-harbor-', dir='/tmp')
            root = Path(cls.temp.name) / 'evidence'
        cls.root = root
        cls.report = module.run_qualification(root)

    @classmethod
    def tearDownClass(cls):
        if cls.temp is not None:
            cls.temp.cleanup()

    def test_native_completion_preserves_timing_submission_and_reference_interval(self):
        result = self.report['cases']['complete']
        self.assertEqual(result['invocations'], 1)
        self.assertEqual(result['network_mode'], 'none')
        self.assertTrue(result['container_removal_verified'])
        self.assertEqual(result['timing_status'], 'valid')
        self.assertEqual(result['score'], 1)
        self.assertIsNone(result['exception_type'])
        self.assertIsNone(result['effective_agent_timeout_seconds'])
        self.assertGreater(result['harbor_agent_seconds'], result['runtime_seconds'])
        self.assertTrue(result['late_write_observed'])
        logs = self.root / 'trials' / result['trial_name'] / 'agent'
        self.assertEqual((logs / 'working-answer.txt').read_bytes(), b'late overwrite')
        self.assertEqual((logs / 'sealed-submission.bin').read_bytes(), b'42')
        for name, digest in self.report['source_hashes'].items():
            retained = self.root / 'sources' / name
            self.assertEqual(hashlib.sha256(retained.read_bytes()).hexdigest(), digest)
        self.assertFalse(self.report['study_launch_ready'])
        self.assertEqual(self.report['harbor_version'], '0.23.0')

    def test_native_crash_is_not_retried_or_called_a_natural_finish(self):
        result = self.report['cases']['crash']
        self.assertEqual(result['invocations'], 1)
        self.assertEqual(result['network_mode'], 'none')
        self.assertTrue(result['container_removal_verified'])
        self.assertNotEqual(result['timing_status'], 'valid')
        self.assertIsNone(result['runtime_seconds'])
        self.assertIsNotNone(result['exception_type'])
        self.assertIsNone(result['score'])
