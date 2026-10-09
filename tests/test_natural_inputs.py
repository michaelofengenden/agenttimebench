"""Answer-free input projection and conservative grading controls, no model calls."""

import base64
import copy
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

try:
    from agenttime import natural_inputs as subject
except ImportError:
    subject = None


def png():
    def chunk(kind, body):
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\0\xff\0\0')) + chunk(b'IEND', b''))


def contract(slot='gpqa-01'):
    family = slot.split('-')[0]
    closed = family in ('gpqa', 'hle')
    return {
        'schema_version': 'agenttime.natural-preparation-contract.v1',
        'visibility': 'controller_only_render_only_native_messages_and_allowlisted_materials_to_subject',
        'slot_id': slot, 'family_id': family,
        'candidate_id': '672ccc56010db734d4f3f109' if family == 'hle' else 'synthetic-id',
        'title': 'Synthetic task', 'arm': 'natural', 'duration_request': None,
        'experiment_time_cap_seconds': None,
        'native_messages': [{'role': 'user', 'content': 'Solve this fictional problem.\nKeep these spaces.  '}],
        'completion_rule': {'kind': 'final_answer', 'text': 'CONTROLLER_COMPLETION_SENTINEL'},
        'allowed_materials': [],
        'intended_target': {
            'hardware_allocation': {'allocation_scope': 'no_task_compute' if closed else 'natural_subject',
                'architecture': None, 'cpus': None if closed else 4, 'memory_gib': None if closed else 16,
                'gpu': {'count': 0, 'model': None, 'memory_gib': None}, 'host_class': None, 'storage': None},
            'intended_interfaces': 'No tools.' if closed else 'Native browser interface.',
            'intended_network': {'policy': 'model_transport_only' if closed else 'public_web_task_access'},
            'intended_subagents': {'policy': 'disabled' if closed else 'native_only_where_permitted'},
            'target_environment_text': 'CONTROLLER_TARGET_SENTINEL',
        },
        'initialization': {'secret_controller_note': 'CONTROLLER_INITIALIZATION_SENTINEL'},
    }


class InputTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(subject, 'The natural input compiler has not been implemented')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def image_contract(self):
        value = contract('hle-03')
        data = png()
        path = self.root / 'benchmarks/cache/hle-question-images-20261002/images' / (value['candidate_id'] + '.png')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        value['allowed_materials'] = [{'kind': 'image', 'name': 'question.png', 'media_type': 'image/png',
            'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data), 'width': 1, 'height': 1}]
        return value, path, data

    def test_exact_text_and_order_only_are_projected(self):
        value = contract()
        value['native_messages'].append({'role': 'user', 'content': 'Second exact block.\n'})
        original = copy.deepcopy(value)
        event = subject.compile_input(value, self.root)
        self.assertEqual(event, {'type': 'user', 'message': {'role': 'user', 'content': [
            {'type': 'text', 'text': x['content']} for x in value['native_messages']]}})
        self.assertEqual(value, original)
        self.assertNotIn('CONTROLLER_', json.dumps(event))

    def test_png_bytes_are_preserved_in_native_image_block(self):
        value, _, data = self.image_contract()
        event = subject.compile_input(value, self.root)
        self.assertEqual(event['message']['content'][0]['text'], value['native_messages'][0]['content'])
        block = event['message']['content'][1]
        self.assertEqual(block['type'], 'image')
        self.assertEqual(block['source']['type'], 'base64')
        self.assertEqual(block['source']['media_type'], 'image/png')
        self.assertEqual(base64.b64decode(block['source']['data'], validate=True), data)

    def test_bad_schema_roles_empty_text_and_treatment_are_rejected(self):
        cases = [({'schema_version': 'forecast.v1'}), ({'arm': 'short'}), ({'duration_request': 60}),
                 ({'experiment_time_cap_seconds': 0}), ({'family_id': 'hle'}), ({'slot_id': 'gpqa-13'}),
                 ({'native_messages': [{'role': 'system', 'content': 'anything'}]}),
                 ({'native_messages': [{'role': 'assistant', 'content': 'previous answer'}]}),
                 ({'native_messages': [{'role': 'user', 'content': '  '}]}),
                 ({'native_messages': [{'role': 'user', 'content': ['text']}]}),
                 ({'native_messages': []}), ({'native_messages': 'oops'})]
        for change in cases:
            with self.subTest(change=change):
                value = contract(); value.update(change)
                with self.assertRaises(ValueError): subject.compile_input(value, self.root)

    def test_forecast_wrapper_and_closed_book_tool_changes_are_rejected(self):
        for text in ['BEGIN NATIVE USER MESSAGE 1\nQuestion\nEND NATIVE MESSAGE 1',
                     'Estimate how long this task will take. Return minutes = N.',
                     'Intended later task environment.\nSome data']:
            with self.subTest(text=text):
                value = contract(); value['native_messages'][0]['content'] = text
                with self.assertRaises(ValueError): subject.compile_input(value, self.root)
        value = contract(); value['intended_target']['intended_subagents']['policy'] = 'enabled'
        with self.assertRaises(ValueError): subject.compile_input(value, self.root)

    def test_missing_required_image_and_extra_materials_are_rejected(self):
        with self.assertRaises(ValueError): subject.compile_input(contract('hle-03'), self.root)
        value, _, _ = self.image_contract()
        value['slot_id'] = 'hle-01'
        with self.assertRaises(ValueError): subject.compile_input(value, self.root)
        value['slot_id'] = 'hle-03'; value['allowed_materials'].append(dict(value['allowed_materials'][0]))
        with self.assertRaises(ValueError): subject.compile_input(value, self.root)

    def test_bad_image_hash_size_dimensions_and_format_are_rejected(self):
        value, path, data = self.image_contract()
        for change in [{'sha256': '0' * 64}, {'bytes': len(data) - 1}, {'width': 2},
                       {'height': True}, {'media_type': 'image/jpeg'}, {'kind': 'text'}]:
            with self.subTest(change=change):
                changed = copy.deepcopy(value); changed['allowed_materials'][0].update(change)
                with self.assertRaises(ValueError): subject.compile_input(changed, self.root)
        path.write_bytes(b'not a png')
        value['allowed_materials'][0].update(sha256=hashlib.sha256(b'not a png').hexdigest(), bytes=9)
        with self.assertRaises(ValueError): subject.compile_input(value, self.root)

    def test_material_path_injection_and_symlinks_are_rejected(self):
        value, path, _ = self.image_contract()
        for change in [{'name': '../private-answer'}, {'path': '/private/answer.png'},
                       {'source_path': '../../answer'}, {'name': 'answer.png'}]:
            with self.subTest(change=change):
                changed = copy.deepcopy(value); changed['allowed_materials'][0].update(change)
                with self.assertRaises(ValueError): subject.compile_input(changed, self.root)
        other = path.with_name('other.png'); path.rename(other); path.symlink_to(other)
        with self.assertRaises(ValueError): subject.compile_input(value, self.root)
        path.unlink(); other.rename(path)
        parent = path.parent; renamed = parent.with_name('private-images'); parent.rename(renamed); parent.symlink_to(renamed)
        with self.assertRaises(ValueError): subject.compile_input(value, self.root)

    def test_candidate_path_cannot_escape_image_allowlist(self):
        value, _, _ = self.image_contract(); value['candidate_id'] = '../../../../private-answer'
        with self.assertRaises(ValueError): subject.compile_input(value, self.root)

    def test_resource_metadata_cannot_smuggle_arbitrary_nested_values(self):
        for key in ('architecture', 'host_class', 'storage'):
            with self.subTest(key=key):
                value = contract(); value['intended_target']['hardware_allocation'][key] = {'answer': 'PRIVATE_REFERENCE'}
                with self.assertRaises(ValueError): subject.compile_input(value, self.root)
        value = contract(); value['intended_target']['hardware_allocation']['gpu']['model'] = {'answer': 'PRIVATE_REFERENCE'}
        with self.assertRaises(ValueError): subject.compile_input(value, self.root)

    def test_roster_is_fixed_order_hash_bound_and_metadata_only(self):
        directory = self.root / 'contracts'; directory.mkdir()
        expected = tuple([f'gpqa-{i:02}' for i in range(1, 13)] + [f'hle-{i:02}' for i in range(1, 21)]
                         + [f'browsecomp-{i:02}' for i in range(1, 13)] + [f'assistant-{i:02}' for i in range(1, 7)])
        self.assertEqual(subject.SELECTED_SLOTS, expected)
        for slot in expected:
            value = contract(slot)
            if slot in {'hle-03', 'hle-07', 'hle-09', 'hle-19'}:
                example, _, _ = self.image_contract(); value['allowed_materials'] = example['allowed_materials']
            (directory / (slot + '.json')).write_text(json.dumps(value))
        rows = subject.build_roster(directory)
        self.assertEqual([r['task_id'] for r in rows], list(expected))
        self.assertEqual(set(rows[0]), {'task_id', 'label', 'benchmark', 'contract_sha256', 'required_resources', 'interface'})
        self.assertEqual(rows[0]['contract_sha256'], hashlib.sha256((directory / 'gpqa-01.json').read_bytes()).hexdigest())
        self.assertEqual(rows[-1]['required_resources']['cpus'], 4)
        self.assertNotIn('CONTROLLER_', json.dumps(rows))
        self.assertNotIn('fictional problem', json.dumps(rows))
        (directory / 'gpqa-01.json').unlink()
        with self.assertRaises(ValueError): subject.build_roster(directory)

    def test_roster_rejects_duplicate_keys_and_filename_identity_mismatch(self):
        directory = self.root / 'contracts'; directory.mkdir()
        p = directory / 'gpqa-01.json'; p.write_text('{"slot_id":"gpqa-01","slot_id":"gpqa-02"}')
        with self.assertRaises(ValueError): subject.build_roster(directory)
        p.write_text(json.dumps(contract('gpqa-02')))
        with self.assertRaises(ValueError): subject.build_roster(directory)


class GradeTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(subject, 'The natural input compiler has not been implemented')

    def test_gpqa_native_final_format_grades_correct_and_wrong_separately(self):
        for text in ['The correct answer is (A)', 'Reasoning here.\n\nThe correct answer is A.',
                     '**The correct answer is (A).**']:
            with self.subTest(text=text):
                result = subject.grade_gpqa(text, 'A')
                self.assertEqual((result['status'], result['score']), ('available', 1.0))
                self.assertNotIn('correct_choice', result)
        result = subject.grade_gpqa('The correct answer is (B)', 'A')
        self.assertEqual((result['status'], result['score']), ('available', 0.0))

    def test_gpqa_missing_malformed_conflicting_and_nonterminal_are_unavailable(self):
        for text in ['', None, 'A', 'Answer: A', 'The correct answer is (A or B)',
                     'The correct answer is (E)', 'The correct answer is (A).\nBut I am unsure.',
                     'The correct answer is (A).\nThe correct answer is (B).',
                     'Answer: B\nThe correct answer is (A).', 'The correct answer is (A']:
            with self.subTest(text=text):
                result = subject.grade_gpqa(text, 'A')
                self.assertEqual((result['status'], result['score']), ('unavailable', None))
        for choice in ['E', '', None, 1, 'A or B']:
            with self.subTest(choice=choice):
                with self.assertRaises(ValueError): subject.grade_gpqa('The correct answer is (A)', choice)

    def test_browsecomp_parser_reads_field_value_not_regex_whole_match(self):
        self.assertEqual(subject.parse_browsecomp_judgement('reasoning: synthetic\ncorrect: yes\nconfidence: 80'), 'yes')
        self.assertEqual(subject.parse_browsecomp_judgement('correct: no'), 'no')
        for value in ('correct: abstained', 'incorrect: yes', ''):
            with self.subTest(value=value), self.assertRaises(ValueError):
                subject.parse_browsecomp_judgement(value)

    def test_browsecomp_ambiguous_or_malformed_judgements_are_not_zero(self):
        for value in ['correct: yes\ncorrect: no', 'correct: yes and no', 'correct: maybe',
                      'correct: yes\ncorrect: yes', None, {'correct': 'yes'}]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError): subject.parse_browsecomp_judgement(value)


if __name__ == '__main__':
    unittest.main()
