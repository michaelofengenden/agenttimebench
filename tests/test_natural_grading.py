"""Local immutable grading controls. Fixtures contain synthetic answers only."""
from concurrent.futures import ThreadPoolExecutor
import copy
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from agenttime.evidence import canonical_json
from agenttime.natural_inputs import compile_input
try:
    from agenttime import natural_grading as subject
except ImportError:
    subject = None


def sha(data):
    return hashlib.sha256(data).hexdigest()


def native_json(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False) + '\n').encode()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value))


def contract(family='gpqa'):
    closed = family in ('gpqa', 'hle')
    message = ('What is the correct answer to this question: A synthetic question.\n\nChoices:\n'
               '(A) Synthetic wrong one\n(B) Synthetic wrong two\n(C) Synthetic correct\n'
               '(D) Synthetic wrong three\n\nFormat your response as follows: '
               '"The correct answer is (insert answer here)"') if family == 'gpqa' else 'A synthetic question.'
    return {'schema_version': 'agenttime.natural-preparation-contract.v1',
        'visibility': 'controller_only_render_only_native_messages_and_allowlisted_materials_to_subject',
        'slot_id': family + '-01', 'family_id': family,
        'candidate_id': 'synthetic-record' if family == 'gpqa' else ('validation:2' if family == 'assistant' else 'synthetic-id'),
        'title': 'Synthetic task', 'arm': 'natural', 'duration_request': None, 'experiment_time_cap_seconds': None,
        'native_messages': [{'role': 'user', 'content': message}],
        'completion_rule': {'kind': 'native_requested_output', 'text': 'Native submission.'}, 'allowed_materials': [],
        'intended_target': {'intended_interfaces': 'Native interface.',
            'hardware_allocation': {'allocation_scope': 'no_task_compute' if closed else 'natural_subject',
                'architecture': None, 'cpus': None if closed else 4, 'memory_gib': None if closed else 16,
                'gpu': {'count': 0, 'model': None, 'memory_gib': None}, 'host_class': None, 'storage': None},
            'intended_network': {'policy': 'model_transport_only' if closed else 'public_web_task_access'},
            'intended_subagents': {'policy': 'disabled' if closed else 'native_only_where_permitted'}}}


class GradingTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(subject, 'The independent local natural grader is not implemented')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / 'repo'; self.controller = self.root / 'controller'
        self.repo.mkdir(); self.controller.mkdir()
        self.attempt = str(uuid4())
        self.dataset = self.repo / subject.GPQA_DATA
        self.dataset.parent.mkdir(parents=True)
        rows = [{'Record ID': 'synthetic-record', 'Question': 'A synthetic question.',
            'Correct Answer': 'Synthetic correct', 'Incorrect Answer 1': 'Synthetic wrong one',
            'Incorrect Answer 2': 'Synthetic wrong two', 'Incorrect Answer 3': 'Synthetic wrong three'}]
        with self.dataset.open('w', newline='') as output:
            writer = csv.DictWriter(output, list(rows[0])); writer.writeheader(); writer.writerows(rows)
        assistant_data = self.repo / subject.ASSISTANT_DATA
        write_json(assistant_data, {'rows': [{'row_idx': 2, 'row': {'id': 'synthetic-assistant',
            'task': 'A synthetic question.', 'answer': 'synthetic orange pear'}}]})
        # A tiny external scorer double tests the adapter boundary, not native accuracy.
        # Cached native code is characterized separately when its real deps exist.
        scorer = self.repo / subject.ASSISTANT_EVALUATION / 'evaluator.py'
        scorer.parent.mkdir(parents=True)
        scorer.write_text('def question_scorer(prediction, gold):\n'
                          '    if prediction == "native-error": raise ValueError(gold)\n'
                          '    if prediction == "nonfinite": return float("nan"), 1\n'
                          '    if prediction == "bad-shape": return "bad", 1\n'
                          '    if prediction == "bad-has-answer": return 1, 7\n'
                          '    if not prediction: return 0.0, 0.0\n'
                          '    if prediction == gold: return 1.0, 1.0\n'
                          '    if prediction == "orange": return 0.5, 1.0\n'
                          '    return 0.0, 1.0\n')
        self.make_completed()

    def make_completed(self, family='gpqa', answer='The correct answer is (C)'):
        self.value = contract(family); self.task = self.value['slot_id']
        self.contract_path = self.controller / 'contracts' / (sha(canonical_json(self.value)) + '.json')
        write_json(self.contract_path, self.value)
        self.directory = self.controller / 'attempts' / self.attempt
        self.archive = self.directory / 'final-archive'
        self.answer_path = self.archive / 'capture/sealed-answer.txt'
        self.answer_path.parent.mkdir(parents=True, exist_ok=True)
        self.answer_path.write_text(answer)
        delivered = native_json(compile_input(self.value, self.repo))
        (self.archive / 'capture/delivered-input.json').write_bytes(delivered)
        input_sha = sha(delivered); answer_sha = sha(answer.encode())
        manifest = {'schema_version': 1, 'executor': 'claude-natural-v1', 'arm': 'natural',
            'tasks': [{'id': self.task, 'contract_sha256': sha(self.contract_path.read_bytes()),
                       'input_sha256': input_sha}]}
        write_json(self.controller/'manifest.json', manifest)
        write_json(self.controller/'binding.json', {'campaign_id': 'synthetic-campaign',
            'manifest_sha256': sha(canonical_json(manifest)), 'runtime_sources_sha256': 'd'*64})
        inventory = {'capture/sealed-answer.txt': {'type': 'file', 'mode': 0o600,
                     'bytes': len(answer.encode()), 'sha256': answer_sha},
                     'capture/delivered-input.json': {'type': 'file', 'mode': 0o600,
                     'bytes': len(delivered), 'sha256': input_sha}}
        receipt = {'schema_version': 'agenttime.native-local-archive.v1', 'verified': True,
                   'inventory': inventory, 'inventory_sha256': sha(native_json(inventory))}
        receipt_raw = native_json(receipt)
        (self.directory / 'final-archive-receipt.json').write_bytes(receipt_raw)
        identity = {'session_id': str(uuid4()), 'worker_id': 'synthetic-worker', 'container_id': 'a'*64,
                    'host_id': 'synthetic-host', 'daemon_id': 'synthetic-daemon', 'docker_endpoint': 'unix:///synthetic.sock'}
        stop = {'identity': identity, 'stop_verified': True, 'container_running': False,
                'external_verification': True, 'observed_at': '2026-10-09T12:00:04Z'}
        write_json(self.directory / 'external-stop.json', stop)
        native_evidence = {'original_native_stores_verified': True, 'restoration_proven': False,
                          'session_id': identity['session_id'], 'stream_sha256': 'b'*64, 'parent': {}, 'children': {}}
        worker = {'state': 'captured', 'attempt_id': self.attempt, 'native_sessions_verified': True,
                  'native_session_evidence': native_evidence, 'archive': receipt,
                  'execution': {'timing_valid': True, 'cancelled': False, 'root_exit_code': 0}}
        write_json(self.directory / 'worker-final-report.json', worker)
        final = {'attempt_id': self.attempt, 'identity_sha256': sha(canonical_json(identity)),
            'contract_sha256': sha(self.contract_path.read_bytes()), 'input_sha256': input_sha,
            'outcome': 'completed', 'timing_status': 'valid', 'runtime_seconds': 3.0,
            'sealed_answer_sha256': answer_sha, 'worker_report_sha256': sha(canonical_json(worker)),
            'native_sessions_verified': True, 'native_session_evidence_sha256': sha(canonical_json(native_evidence)),
            'failure_reason_codes': [], 'native_clock_id': 'synthetic-clock',
            'native_boundaries': {'prompt_released_at': '2026-10-09T12:00:00Z',
                'root_process_exited_at': '2026-10-09T12:00:02Z', 'owned_work_drained_at': '2026-10-09T12:00:03Z',
                'native_terminal_at': '2026-10-09T12:00:03Z'},
            'archive_manifest_sha256': sha(receipt_raw), 'archive_inventory_sha256': receipt['inventory_sha256'],
            'independent_archive_verified': True, 'stop_evidence': {'identity_sha256': sha(canonical_json(identity)),
                'stop_verified': True, 'owned_work_drained': True, 'proof_sha256': sha(canonical_json(stop))}}
        write_json(self.directory / 'final-report.json', final)
        ack = {'schema_version': 'agenttime.natural-final-archive-ack.v1', 'attempt_id': self.attempt,
            'identity_sha256': final['identity_sha256'], 'input_sha256': input_sha,
            'worker_report_sha256': final['worker_report_sha256'], 'archive_manifest_sha256': final['archive_manifest_sha256'],
            'archive_inventory_sha256': final['archive_inventory_sha256'], 'independent_archive_verified': True,
            'native_sessions_verified': True, 'native_session_evidence_sha256': final['native_session_evidence_sha256']}
        write_json(self.directory / 'final-archive-ack.json', ack)
        queue = {'schema_version': 'agenttime.natural-grade-queue.v1', 'attempt_id': self.attempt,
            'task_id': self.task, 'status': 'queued', 'sealed_answer_sha256': answer_sha,
            'sealed_answer_ref': str(self.answer_path), 'contract_sha256': final['contract_sha256'],
            'archive_inventory_sha256': final['archive_inventory_sha256']}
        self.queue_path = self.controller / 'grade-queue' / (self.attempt + '.json')
        write_json(self.queue_path, queue)

    def grader(self, python=None):
        pins = subject.source_pins(self.repo, assistant_python=python)
        return subject.LocalGrader(self.controller, self.repo, pins, assistant_python=python)

    def test_gpqa_maps_correct_text_to_delivered_C_without_reshuffling(self):
        result = self.grader().grade(self.attempt)
        self.assertEqual((result['status'], result['score']), ('available', 1.0))
        self.assertEqual(result['scorer'], 'gpqa_native_terminal_choice')
        self.assertNotIn('Synthetic correct', json.dumps(result))
        self.assertNotIn('correct_choice', json.dumps(result))

    def test_gpqa_wrong_is_zero_but_empty_malformed_conflicting_are_unavailable(self):
        for answer, status, score in [('The correct answer is (A)', 'available', 0.0),
                ('', 'unavailable', None), ('C', 'unavailable', None),
                ('The correct answer is (C)\nThe correct answer is (A)', 'unavailable', None)]:
            with self.subTest(answer=answer):
                self.attempt = str(uuid4()); self.make_completed(answer=answer)
                result = self.grader().grade(self.attempt)
                self.assertEqual((result['status'], result['score']), (status, score))

    def test_existing_receipt_is_identical_and_atomic_under_concurrency(self):
        grader = self.grader()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: grader.grade(self.attempt), range(16)))
        self.assertTrue(all(row == results[0] for row in results))
        path = grader.receipt_path(self.attempt)
        original = path.read_bytes()
        self.assertEqual(json.loads(original), results[0])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(grader.grade(self.attempt), results[0])
        self.assertEqual(path.read_bytes(), original)

    def test_completed_wrong_task_hash_input_answer_and_archive_bindings_fail_closed(self):
        cases = [('answer', 'changed'), ('queue', {'task_id': 'gpqa-02'}),
            ('queue', {'contract_sha256': '0'*64}), ('final', {'sealed_answer_sha256': '0'*64}),
            ('final', {'archive_inventory_sha256': '0'*64}),
            ('ack', {'independent_archive_verified': False}), ('ack', {'input_sha256': '0'*64}),
            ('ack', {'native_session_evidence_sha256': '0'*64}),
            ('input', 'changed'), ('stop', {'container_running': True})]
        for kind, change in cases:
            with self.subTest(kind=kind, change=change):
                self.attempt = str(uuid4()); self.make_completed()
                paths = {'queue': self.queue_path, 'final': self.directory/'final-report.json',
                         'ack': self.directory/'final-archive-ack.json', 'stop': self.directory/'external-stop.json'}
                if kind in ('answer', 'input'):
                    path = self.answer_path if kind == 'answer' else self.archive/'capture/delivered-input.json'
                    path.write_text(change)
                else:
                    value = json.loads(paths[kind].read_bytes()); value.update(change); write_json(paths[kind], value)
                grader = self.grader()
                with self.assertRaises(subject.GradingError): grader.grade(self.attempt)
                self.assertFalse(grader.receipt_path(self.attempt).exists())

    def test_live_partial_failed_and_unproved_terminal_never_grade(self):
        for change in [{'outcome': 'unknown_failure'}, {'timing_status': 'invalid'},
                       {'native_sessions_verified': False}, {'native_boundaries': {}},
                       {'independent_archive_verified': False}]:
            with self.subTest(change=change):
                self.attempt = str(uuid4()); self.make_completed()
                path = self.directory/'final-report.json'; value = json.loads(path.read_bytes())
                value.update(change); write_json(path, value)
                grader = self.grader()
                with self.assertRaises(subject.GradingError): grader.grade(self.attempt)
                self.assertFalse(grader.receipt_path(self.attempt).exists())
        self.attempt = str(uuid4()); self.make_completed(); self.answer_path.unlink()
        with self.assertRaises(subject.GradingError): self.grader().grade(self.attempt)

    def test_queue_paths_symlinks_duplicate_keys_and_invalid_utf8_are_rejected(self):
        original = self.queue_path.read_bytes()
        for raw in [original[:-1] + b',"status":"queued"}', b'\xff']:
            self.queue_path.write_bytes(raw)
            with self.assertRaises(subject.GradingError): self.grader().grade(self.attempt)
        self.queue_path.write_bytes(original)
        answer = self.answer_path.read_bytes(); self.answer_path.unlink()
        target = self.root/'outside-answer'; target.write_bytes(answer); self.answer_path.symlink_to(target)
        with self.assertRaises(subject.GradingError): self.grader().grade(self.attempt)

    def test_changed_receipt_or_sources_cannot_silently_regrade(self):
        grader = self.grader(); grader.grade(self.attempt)
        path = grader.receipt_path(self.attempt)
        value = json.loads(path.read_bytes()); value['score'] = 0.0; write_json(path, value)
        with self.assertRaises(subject.GradingError): grader.grade(self.attempt)
        self.dataset.write_bytes(self.dataset.read_bytes() + b'\n')
        with self.assertRaises(subject.GradingError): grader.grade(self.attempt)

    def test_gpqa_changed_question_choices_ambiguous_or_missing_reference_is_unavailable(self):
        original = self.dataset.read_bytes()
        for kind in ['question', 'choice', 'duplicate', 'missing']:
            with self.subTest(kind=kind):
                self.dataset.write_bytes(original)
                self.attempt = str(uuid4()); self.make_completed()
                with self.dataset.open(newline='') as source: rows = list(csv.DictReader(source))
                if kind == 'question': rows[0]['Question'] = 'Other question.'
                if kind == 'choice': rows[0]['Incorrect Answer 3'] = 'Other option.'
                if kind == 'duplicate': rows.append(dict(rows[0]))
                if kind == 'missing': rows[0]['Record ID'] = 'Other record.'
                with self.dataset.open('w', newline='') as output:
                    writer = csv.DictWriter(output, rows[0].keys()); writer.writeheader(); writer.writerows(rows)
                result = self.grader().grade(self.attempt)
                self.assertEqual((result['status'], result['score']), ('unavailable', None))
                self.assertNotIn('Synthetic correct', json.dumps(result))

    def test_assistant_native_adapter_preserves_positive_zero_empty_and_partial_credit(self):
        for answer, score, has_answer in [('synthetic orange pear', 1.0, 1.0), ('wrong', 0.0, 1.0),
                                         ('', 0.0, 0.0), ('orange', 0.5, 1.0)]:
            with self.subTest(answer=answer):
                self.attempt = str(uuid4()); self.make_completed('assistant', answer)
                result = self.grader(sys.executable).grade(self.attempt)
                self.assertEqual((result['status'], result['score'], result['has_answer']), ('available', score, has_answer))
                self.assertEqual(result['scorer'], 'assistantbench_native_question_scorer')
                self.assertNotIn('synthetic orange pear', json.dumps(result))

    def test_assistant_dependency_missing_and_native_errors_are_unavailable_not_zero(self):
        for answer in ['native-error', 'nonfinite', 'bad-shape', 'bad-has-answer']:
            with self.subTest(answer=answer):
                self.attempt = str(uuid4()); self.make_completed('assistant', answer)
                result = self.grader(sys.executable).grade(self.attempt)
                self.assertEqual((result['status'], result['score']), ('unavailable', None))
                self.assertNotIn('synthetic orange pear', json.dumps(result))
        self.attempt = str(uuid4()); self.make_completed('assistant', 'orange')
        path = self.repo / subject.ASSISTANT_EVALUATION / 'evaluator.py'
        path.write_text('import definitely_absent_native_dependency\n')
        result = self.grader(sys.executable).grade(self.attempt)
        self.assertEqual(result['reason'], 'native_scorer_dependencies_unavailable')
        self.assertIsNone(result['score'])

    def test_assistant_question_binding_and_missing_gold_hold_locally(self):
        original = (self.repo / subject.ASSISTANT_DATA).read_bytes()
        for change in [{'task': 'different'}, {'answer': None}, {'answer': ''}]:
            with self.subTest(change=change):
                self.attempt = str(uuid4()); self.make_completed('assistant', 'orange')
                path = self.repo / subject.ASSISTANT_DATA
                value = json.loads(original); value['rows'][0]['row'].update(change); write_json(path, value)
                result = self.grader(sys.executable).grade(self.attempt)
                self.assertEqual((result['status'], result['score']), ('unavailable', None))

    def test_hle_browsecomp_queue_native_judge_without_any_scorer_or_network_call(self):
        for family in ('hle', 'browsecomp'):
            with self.subTest(family=family):
                self.attempt = str(uuid4()); self.make_completed(family, 'sealed response')
                grader = self.grader()
                with patch('subprocess.run', side_effect=AssertionError('No judge call is authorized')):
                    result = grader.grade(self.attempt)
                self.assertEqual((result['status'], result['score'], result['reason']),
                                 ('queued', None, 'native_judge_api_access_pending'))

    def test_cached_native_assistant_scorer_uses_supplied_venv_and_preserves_native_controls(self):
        repo = Path(__file__).resolve().parents[1]
        python = repo / 'qualification/assistantbench-native-grader-20261009/venv/bin/python'
        cached = repo / subject.ASSISTANT_EVALUATION
        if not python.exists() or not cached.exists():
            self.skipTest('The separately provisioned native scorer runtime is not present')
        shutil.rmtree(self.repo / subject.ASSISTANT_EVALUATION)
        shutil.copytree(cached, self.repo / subject.ASSISTANT_EVALUATION, ignore=shutil.ignore_patterns('__pycache__'))
        for answer, status, score, has_answer in [('synthetic orange pear', 'available', 1.0, 1.0),
                ('violet', 'available', 0.0, 1.0), ('', 'available', 0.0, 0.0),
                ('orange', 'available', 0.5, 1.0), ('{"', 'available', 0.0, 1.0),
                ('null', 'unavailable', None, None)]:
            with self.subTest(control=answer):
                self.attempt = str(uuid4()); self.make_completed('assistant', answer)
                result = self.grader(python).grade(self.attempt)
                self.assertEqual((result['status'], result['score'], result.get('has_answer')),
                                 (status, score, has_answer))
                self.assertEqual(result['runtime']['numpy'], '2.3.3')
                self.assertEqual(result['runtime']['scipy'], '1.16.2')

    def test_symlinked_receipt_directory_is_rejected_without_writing_outside_controller(self):
        outside = self.root / 'outside-receipts'; outside.mkdir()
        (self.controller / 'grade-receipts').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(subject.GradingError): self.grader().grade(self.attempt)
        self.assertEqual(list(outside.iterdir()), [])

    def test_malformed_completion_shapes_are_stable_errors_with_no_receipt(self):
        for key in ('native_boundaries', 'stop_evidence'):
            with self.subTest(field=key):
                self.attempt = str(uuid4()); self.make_completed()
                path = self.directory / 'final-report.json'; value = json.loads(path.read_bytes())
                value[key] = []; write_json(path, value)
                grader = self.grader()
                with self.assertRaises(subject.GradingError): grader.grade(self.attempt)
                self.assertFalse(grader.receipt_path(self.attempt).exists())

    def test_native_dependency_and_code_drift_cannot_publish_an_off_pin_score(self):
        self.make_completed('assistant', 'orange')
        grader = self.grader(sys.executable)
        bad_runtime = {'python': 'different', 'numpy': None, 'scipy': None}
        raw = canonical_json({'status': 'available', 'score': 0.5, 'has_answer': 1.0,
                              'reason': None, 'runtime': bad_runtime})
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, raw)):
            with self.assertRaises(subject.GradingError): grader.grade(self.attempt)
        self.assertFalse(grader.receipt_path(self.attempt).exists())
        path = self.repo / subject.ASSISTANT_EVALUATION / 'evaluator.py'
        path.write_text(path.read_text() + '\n# changed native code\n')
        with self.assertRaises(subject.GradingError): grader.grade(self.attempt)

    def test_separate_utc_annotations_do_not_retime_verified_monotonic_completion(self):
        path = self.directory / 'final-report.json'; value = json.loads(path.read_bytes())
        value['native_boundaries']['native_terminal_at'] = '2026-10-09T12:00:03.000019Z'
        write_json(path, value)
        result = self.grader().grade(self.attempt)
        self.assertEqual((result['status'], result['score']), ('available', 1.0))


if __name__ == '__main__':
    unittest.main()
