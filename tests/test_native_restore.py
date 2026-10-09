"""Model-free checks for the bounded, historical native-session restore probe."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

REPO = Path(__file__).resolve().parents[1]
PROBE = REPO / 'qualification/native_session/restore_probe.py'
ORIGINAL = REPO / 'qualification/native-session-20261009/synthetic-native-closed-book-ba4db711e948/runtime/agenttime/native_session.py'
spec = importlib.util.spec_from_file_location('agenttime_restore_probe_tested', PROBE)
rp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rp)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(rp.json_bytes(value))


@unittest.skipUnless(ORIGINAL.is_file(), 'requires the retained approved 5a49 native qualification source')
class RestoreProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.source = self.base / 'source'
        module = self.source / 'runtime/agenttime/native_session.py'
        module.parent.mkdir(parents=True)
        shutil.copy2(ORIGINAL, module)
        self.ns = rp.load_historical(self.source, rp.HISTORICAL_SOURCE_SHA256)
        self.sid = str(uuid.uuid4())
        self.contract = self.ns.qualification_contract('synthetic-restore-fixture', self.sid)
        self.state = self.source / 'final-state'
        self.ns.prepare(self.state, self.contract)
        transcript = self.state / rp.transcript_relative(self.sid)
        transcript.parent.mkdir(parents=True)
        self.original_rows = [
            {'type': 'user', 'sessionId': self.sid, 'cwd': rp.RUNTIME_CWD,
             'uuid': str(uuid.uuid4()), 'message': self.contract['input']['message']},
            {'type': 'assistant', 'sessionId': self.sid, 'cwd': rp.RUNTIME_CWD,
             'uuid': str(uuid.uuid4()), 'message': {'type': 'message', 'id': 'msg_original', 'role': 'assistant', 'model': self.ns.MODEL,
             'content': [{'type': 'text', 'text': rp.EXPECTED_MARKER}]}},
        ]
        transcript.write_bytes(b''.join(rp.json_bytes(r) for r in self.original_rows))
        (self.state / 'capture/sealed-answer.txt').write_text(rp.EXPECTED_MARKER)
        (self.state / 'capture/debug.log').write_text('synthetic fixture\n')
        (self.state / 'capture/latest').symlink_to('debug.log')
        self.write_capture(self.state / 'capture', self.sid, [self.contract['input']['message']], probe=False)
        receipt = self.ns.capture_archive(self.state, 'synthetic-placeholder-secret')
        self.report = {
            'qualification_id': self.contract['qualification_id'], 'session_id': self.sid,
            'qualified': True, 'archive_verified': True,
            'included_subscription_allowance_verified': True, 'requested_route': 'subscription',
            'execution': {'session_id': self.sid, 'timing_valid': True, 'issues': [],
                          'root_exit_code': 0, 'cancelled': False,
                          'prompt_released_monotonic_ns': 1, 'result_monotonic_ns': 2,
                          'root_exit_monotonic_ns': 3, 'owned_work_drained_monotonic_ns': 4,
                          'native_terminal_monotonic_ns': 4},
            'transport': {'issues': [], 'session_ids': [self.sid], 'request_count': 1},
        }
        write(self.state / 'report.json', self.report)
        write(self.source / 'independent-archive-ack.json', {'verified': True,
            'inventory_sha256': receipt['inventory_sha256'], 'location': '/independent/original/archive'})
        write(self.source / 'stop-proof.json', {'container_id': 'synthetic-container', 'stopped': True,
                                             'scope': 'synthetic_qualification_only'})
        write(self.source / 'owned-container.json', {'container_id': 'synthetic-container',
                                                   'purpose': 'synthetic_qualification_only'})
        self.destination = self.base / 'restored'

    def write_capture(self, capture, sid, messages, probe=True, tools=None):
        raw = capture / 'raw-bodies'
        raw.mkdir(parents=True, exist_ok=True)
        body = {'model': self.ns.MODEL, 'output_config': {'effort': 'max'},
                'tools': [] if tools is None else tools, 'messages': messages}
        write(raw / 'first.request.json', body)
        write(raw / 'first.response.json', {'type': 'message', 'id': 'msg_synthetic', 'model': self.ns.MODEL})
        write(raw / 'index.jsonl', {'request_file': 'first.request.json', 'response_file': 'first.response.json',
            'request_id': 'req_synthetic', 'message_id': 'msg_synthetic', 'session_id': sid, 'model': self.ns.MODEL})
        init = {'type': 'system', 'subtype': 'init', 'session_id': sid, 'model': self.ns.CLI_MODEL,
                'claude_code_version': self.ns.CLI_VERSION, 'tools': [] if tools is None else ['Bash'],
                'mcp_servers': [], 'plugins': [], 'skills': []}
        result = {'type': 'result', 'subtype': 'success', 'session_id': sid,
                  'is_error': False, 'result': rp.EXPECTED_MARKER}
        (capture / 'stream.jsonl').write_bytes(rp.json_bytes(init) + rp.json_bytes(result))

    def source_hash(self):
        return rp.tree_digest(self.source)

    def prepare(self, operation='continue', new_id=None):
        return rp.prepare(self.source, self.destination, self.source_hash(), operation,
                          session_id=new_id)

    def reseal(self):
        entries = self.ns.inventory(self.state / 'archive')
        receipt = json.loads((self.state / 'archive-receipt.json').read_bytes())
        receipt['inventory'] = entries
        receipt['inventory_sha256'] = self.ns._sha(self.ns._json_bytes(entries))
        write(self.state / 'archive-receipt.json', receipt)
        write(self.source / 'independent-archive-ack.json', {'verified': True,
            'inventory_sha256': receipt['inventory_sha256'], 'location': '/independent/original/archive'})

    def test_historical_source_is_loaded_without_mutating_it(self):
        before = self.source_hash()
        result = rp.verify_source(self.source, before)
        self.assertEqual(result['session_id'], self.sid)
        self.assertEqual(result['source_sha256'], rp.HISTORICAL_SOURCE_SHA256)
        self.assertEqual(self.source_hash(), before)
        self.assertFalse(list(self.source.rglob('__pycache__')))

    def test_wrong_historical_hash_and_modified_module_rejected(self):
        with self.assertRaisesRegex(rp.ProbeError, 'historical_source_pin_mismatch'):
            rp.load_historical(self.source, '0' * 64)
        module = self.source / 'runtime/agenttime/native_session.py'
        module.write_bytes(module.read_bytes() + b'\n# changed\n')
        with self.assertRaisesRegex(rp.ProbeError, 'historical_source_pin_mismatch'):
            rp.verify_source(self.source)

    def test_tampered_archive_and_ack_rejected(self):
        (self.state / 'archive/work/injected.txt').write_text('unverified')
        with self.assertRaisesRegex(rp.ProbeError, 'archive_inventory_mismatch'):
            rp.verify_source(self.source)
        (self.state / 'archive/work/injected.txt').unlink()
        write(self.source / 'independent-archive-ack.json', {'verified': False})
        with self.assertRaisesRegex(rp.ProbeError, 'independent_archive_unverified'):
            rp.verify_source(self.source)

    def test_frozen_hash_blocks_source_mutation(self):
        before = self.source_hash()
        (self.source / 'extra').write_text('changed')
        with self.assertRaisesRegex(rp.ProbeError, 'source_tree_changed'):
            rp.prepare(self.source, self.destination, before, 'continue')
        self.assertFalse(self.destination.exists())

    def test_study_payload_and_tool_escalation_rejected(self):
        contract = dict(self.contract, input={'type': 'user', 'message': {'role': 'user', 'content': 'study task'}})
        write(self.state / 'contract.json', contract)
        with self.assertRaisesRegex(rp.ProbeError, 'not_fixed_synthetic_payload'):
            rp.verify_source(self.source)
        write(self.state / 'contract.json', self.contract)
        raw = self.state / 'archive/capture/raw-bodies/first.request.json'
        body = json.loads(raw.read_bytes()); body['tools'] = [{'name': 'Bash'}]; write(raw, body)
        self.reseal()
        with self.assertRaisesRegex(rp.ProbeError, 'source_transport_unverified'):
            rp.verify_source(self.source)

    def test_unstopped_source_and_session_mismatch_rejected(self):
        write(self.source / 'stop-proof.json', {'stopped': False})
        with self.assertRaisesRegex(rp.ProbeError, 'original_stop_unverified'):
            rp.verify_source(self.source)
        write(self.source / 'stop-proof.json', {'container_id': 'synthetic-container', 'stopped': True,
                                             'scope': 'synthetic_qualification_only'})
        path = self.state / 'archive' / rp.transcript_relative(self.sid)
        changed = [dict(r, sessionId=str(uuid.uuid4())) for r in self.original_rows]
        path.write_bytes(b''.join(rp.json_bytes(r) for r in changed)); self.reseal()
        with self.assertRaisesRegex(rp.ProbeError, 'native_transcript_identity_mismatch'):
            rp.verify_source(self.source)

    def test_prepare_keeps_original_and_copies_only_sealed_state(self):
        before = self.source_hash()
        manifest = self.prepare()
        self.assertEqual(self.source_hash(), before)
        self.assertEqual(manifest['runtime_cwd'], '/tmp/at-native/work')
        self.assertEqual(manifest['expected_session_id'], self.sid)
        self.assertEqual(self.ns.inventory(self.destination), self.ns.inventory(self.state / 'archive'))
        self.assertFalse((self.destination / 'report.json').exists())
        self.assertNotIn(rp.EXPECTED_MARKER, rp.PROBE_TEXT)
        self.assertFalse(manifest['study_ready'])

    def test_copy_time_source_mutation_is_detected(self):
        original = shutil.copytree
        def altered(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.source / 'unexpected').write_text('mutation')
            return result
        with patch.object(rp.shutil, 'copytree', side_effect=altered):
            with self.assertRaisesRegex(rp.ProbeError, 'source_tree_changed'):
                self.prepare()
        self.assertFalse((self.destination / 'restore-manifest.json').exists())

    def test_destination_must_be_fresh_and_disjoint(self):
        self.prepare()
        with self.assertRaisesRegex(rp.ProbeError, 'destination_not_fresh'):
            self.prepare()
        with self.assertRaisesRegex(rp.ProbeError, 'source_destination_overlap'):
            rp.prepare(self.source, self.source / 'child', self.source_hash(), 'continue')

    def test_resume_arguments_and_stdin_environment_only(self):
        manifest = self.prepare()
        conf = rp.configuration(self.destination, '/opt/pinned/claude', manifest, self.ns, 'synthetic-placeholder-secret')
        self.assertNotIn('--session-id', conf['command'])
        self.assertEqual(conf['command'][-2:], ['--resume', self.sid])
        self.assertEqual(conf['environment']['CLAUDE_CODE_OAUTH_TOKEN'], 'synthetic-placeholder-secret')
        self.assertNotIn('synthetic-placeholder-secret', json.dumps(manifest))
        self.assertNotIn('synthetic-placeholder-secret', ' '.join(conf['command']))

    def test_fork_is_explicit_fresh_identity(self):
        for value in (None, self.sid, 'bad-id'):
            with self.assertRaisesRegex(rp.ProbeError, 'fresh_fork_session_required'):
                rp.prepare(self.source, self.destination, self.source_hash(), 'fork', session_id=value)
        new_id = str(uuid.uuid4())
        manifest = self.prepare('fork', new_id)
        conf = rp.configuration(self.destination, '/opt/pinned/claude', manifest, self.ns, 'synthetic-placeholder-secret')
        self.assertEqual(conf['command'][-5:], ['--resume', self.sid, '--fork-session', '--session-id', new_id])
        self.assertEqual(manifest['expected_session_id'], new_id)

    def append_probe(self, manifest, tools=None, parent_mutated=False, history=True):
        sid = manifest['expected_session_id']
        original = self.destination / rp.transcript_relative(self.sid)
        target = self.destination / rp.transcript_relative(sid)
        if manifest['operation'] == 'fork':
            copied = [dict(r, sessionId=sid) for r in self.original_rows]
            target.write_bytes(b''.join(rp.json_bytes(r) for r in copied))
            if parent_mutated: original.write_bytes(original.read_bytes() + b'{}\n')
        rows = [
            {'type': 'user', 'sessionId': sid, 'cwd': rp.RUNTIME_CWD, 'message': rp.probe_input()['message']},
            {'type': 'assistant', 'sessionId': sid, 'cwd': rp.RUNTIME_CWD, 'requestId': 'req_synthetic',
             'version': self.ns.CLI_VERSION, 'isSidechain': False, 'message': {'type': 'message', 'id': 'msg_synthetic', 'role': 'assistant',
             'model': self.ns.MODEL, 'content': [{'type': 'text', 'text': rp.EXPECTED_MARKER}]}},
        ]
        with target.open('ab') as stream:
            for row in rows: stream.write(rp.json_bytes(row))
        capture = self.destination / 'capture'
        if not (self.destination / 'original-capture').exists():
            capture.rename(self.destination / 'original-capture')
        else:
            shutil.rmtree(capture)
        capture.mkdir()
        (capture / 'sealed-answer.txt').write_text(rp.EXPECTED_MARKER)
        messages = [self.contract['input']['message'], self.original_rows[1]['message'], rp.probe_input()['message']]
        if not history: messages = [rp.probe_input()['message']]
        self.write_capture(capture, sid, messages, tools=tools)

    def test_inherited_context_and_identity_are_verified_from_native_evidence(self):
        manifest = self.prepare(); self.append_probe(manifest)
        evidence = rp.inspect_probe(self.destination, manifest, self.ns)
        self.assertTrue(evidence['inherited_context_verified'])
        self.assertEqual(evidence['observed_session_id'], self.sid)
        self.assertTrue(evidence['original_transcript_preserved'])
        stream = self.destination / 'capture/stream.jsonl'
        stream.write_text(stream.read_text().replace(self.sid, str(uuid.uuid4())))
        with self.assertRaisesRegex(rp.ProbeError, 'native_identity_mismatch'):
            rp.inspect_probe(self.destination, manifest, self.ns)

    def test_extra_tools_and_missing_history_fail(self):
        manifest = self.prepare(); self.append_probe(manifest, tools=[{'name': 'Bash'}])
        with self.assertRaisesRegex(rp.ProbeError, 'probe_transport_unverified'):
            rp.inspect_probe(self.destination, manifest, self.ns)
        shutil.rmtree(self.destination)
        manifest = self.prepare(); self.append_probe(manifest, history=False)
        with self.assertRaisesRegex(rp.ProbeError, 'probe_transport_unverified|inherited_context_unverified'):
            rp.inspect_probe(self.destination, manifest, self.ns)

    def test_fork_preserves_parent_native_transcript(self):
        manifest = self.prepare('fork', str(uuid.uuid4())); self.append_probe(manifest)
        result = rp.inspect_probe(self.destination, manifest, self.ns)
        self.assertTrue(result['original_transcript_preserved'])
        parent = self.destination / rp.transcript_relative(self.sid)
        parent.write_bytes(parent.read_bytes() + b'{}\n')
        with self.assertRaisesRegex(rp.ProbeError, 'parent_transcript_changed'):
            rp.inspect_probe(self.destination, manifest, self.ns)

    def add_matching_stream_response(self, manifest):
        path = self.destination / 'capture/stream.jsonl'
        rows = rp.read_rows(path)
        rows.insert(-1, {'type': 'assistant', 'session_id': manifest['expected_session_id'],
            'message': {'type': 'message', 'id': 'msg_synthetic', 'model': self.ns.MODEL,
                'role': 'assistant', 'content': [{'type': 'text', 'text': rp.EXPECTED_MARKER}]}})
        path.write_bytes(b''.join(rp.json_bytes(row) for row in rows))
        return path, rows

    def test_empty_optional_response_uses_exact_complete_native_stream_without_rewriting(self):
        manifest = self.prepare('fork', str(uuid.uuid4())); self.append_probe(manifest)
        stream, rows = self.add_matching_stream_response(manifest)
        response = self.destination / 'capture/raw-bodies/first.response.json'
        response.write_bytes(b'')
        before = rp.tree_digest(self.destination)
        try: evidence = rp.inspect_probe(self.destination, manifest, self.ns)
        except rp.ProbeError as exc: self.fail('Complete matching native stream must support the empty optional body: ' + str(exc))
        self.assertEqual(evidence['transport']['native_stream_response_ids'], ['msg_synthetic'])
        self.assertEqual(rp.tree_digest(self.destination), before)
        for field, value in [('id', 'wrong-id'), ('model', 'wrong-model')]:
            changed = json.loads(json.dumps(rows)); changed[1]['message'][field] = value
            stream.write_bytes(b''.join(rp.json_bytes(row) for row in changed))
            with self.subTest(field=field):
                with self.assertRaisesRegex(rp.ProbeError, 'probe_transport_unverified'):
                    rp.inspect_probe(self.destination, manifest, self.ns)
        changed = json.loads(json.dumps(rows)); changed[1]['session_id'] = str(uuid.uuid4())
        stream.write_bytes(b''.join(rp.json_bytes(row) for row in changed))
        with self.assertRaisesRegex(rp.ProbeError, 'probe_transport_unverified'):
            rp.inspect_probe(self.destination, manifest, self.ns)
        stream.write_bytes(b''.join(rp.json_bytes(row) for row in rows).rstrip(b'\n'))
        with self.assertRaisesRegex(rp.ProbeError, 'probe_transport_unverified'):
            rp.inspect_probe(self.destination, manifest, self.ns)

    def empty_index_fixture(self):
        manifest = self.prepare('fork', str(uuid.uuid4())); self.append_probe(manifest)
        self.add_matching_stream_response(manifest)
        raw = self.destination / 'capture/raw-bodies'
        (raw / 'index.jsonl').write_bytes(b'')
        (raw / 'first.response.json').rename(raw / 'req_synthetic.response.json')
        (raw / 'req_synthetic.response.json').write_bytes(b'')
        request = raw / 'first.request.json'; body = rp.read_json(request)
        body['metadata'] = {'user_id': json.dumps({'device_id': 'a' * 64,
            'account_uuid': '', 'session_id': manifest['expected_session_id']})}
        body['diagnostics'] = {'previous_message_id': 'msg_original'}
        write(request, body)
        return manifest, raw, request, body

    def test_empty_index_uses_original_native_identities_with_explicit_observation_gap(self):
        manifest, raw, _, _ = self.empty_index_fixture()
        before = rp.tree_digest(self.destination)
        try: evidence = rp.inspect_probe(self.destination, manifest, self.ns)
        except rp.ProbeError as exc: self.fail('Original request and native transcript identities must qualify the synthetic restore: ' + str(exc))
        transport = evidence['transport']
        self.assertEqual(transport['evidence_type'], 'request_metadata_and_original_native_transcript')
        self.assertFalse(transport['index_available'])
        self.assertFalse(transport['retry_count_verified'])
        self.assertIsNone(transport['retry_count'])
        self.assertEqual(transport['request_ids'], ['req_synthetic'])
        self.assertEqual(transport['native_stream_response_ids'], ['msg_synthetic'])
        self.assertEqual(transport['request_count'], 1)
        self.assertEqual(rp.tree_digest(self.destination), before)
        self.assertEqual((raw / 'index.jsonl').read_bytes(), b'')

    def test_empty_index_rejects_unbound_or_noncanonical_request(self):
        manifest, _, request, body = self.empty_index_fixture()
        for change in ('session', 'missing_metadata', 'prior', 'missing_prior', 'model', 'effort', 'tools', 'fallback', 'initial_user'):
            altered = json.loads(json.dumps(body))
            if change == 'session':
                meta = json.loads(altered['metadata']['user_id']); meta['session_id'] = str(uuid.uuid4())
                altered['metadata']['user_id'] = json.dumps(meta)
            elif change == 'missing_metadata': del altered['metadata']
            elif change == 'prior': altered['diagnostics']['previous_message_id'] = 'msg_wrong'
            elif change == 'missing_prior': del altered['diagnostics']
            elif change == 'model': altered['model'] = 'wrong-model'
            elif change == 'effort': altered['output_config']['effort'] = 'low'
            elif change == 'tools': altered['tools'] = [{'name': 'Bash'}]
            elif change == 'fallback': altered['fallbacks'] = ['other-model']
            elif change == 'initial_user': altered['messages'][0]['content'][0]['text'] = 'wrong input'
            write(request, altered)
            with self.subTest(change=change):
                with self.assertRaisesRegex(rp.ProbeError, 'probe_transport_unverified'):
                    rp.inspect_probe(self.destination, manifest, self.ns)
        write(request, body)

    def test_empty_index_requires_exact_native_request_id_and_complete_single_response(self):
        manifest, raw, _, _ = self.empty_index_fixture()
        target = self.destination / rp.transcript_relative(manifest['expected_session_id'])
        native = rp.read_rows(target)
        for change in ('absent_request', 'wrong_request', 'wrong_message', 'wrong_version', 'wrong_session'):
            altered = json.loads(json.dumps(native))
            if change == 'absent_request': del altered[-1]['requestId']
            elif change == 'wrong_request': altered[-1]['requestId'] = 'req_other'
            elif change == 'wrong_message': altered[-1]['message']['id'] = 'msg_other'
            elif change == 'wrong_version': altered[-1]['version'] = 'unreviewed'
            else: altered[-1]['sessionId'] = str(uuid.uuid4())
            target.write_bytes(b''.join(rp.json_bytes(row) for row in altered))
            with self.subTest(change=change):
                with self.assertRaisesRegex(rp.ProbeError, 'probe_transport_unverified'):
                    rp.inspect_probe(self.destination, manifest, self.ns)
        target.write_bytes(b''.join(rp.json_bytes(row) for row in native))
        stream = self.destination / 'capture/stream.jsonl'; rows = rp.read_rows(stream)
        for change in ('second_response', 'incomplete', 'wrong_stream_id'):
            altered = json.loads(json.dumps(rows))
            if change == 'second_response':
                extra = json.loads(json.dumps(altered[1])); extra['message']['id'] = 'msg_other'; altered.insert(2, extra)
            elif change == 'wrong_stream_id': altered[1]['message']['id'] = 'msg_other'
            data = b''.join(rp.json_bytes(row) for row in altered)
            stream.write_bytes(data.rstrip(b'\n') if change == 'incomplete' else data)
            with self.subTest(change=change):
                with self.assertRaisesRegex(rp.ProbeError, 'probe_transport_unverified'):
                    rp.inspect_probe(self.destination, manifest, self.ns)
        stream.write_bytes(b''.join(rp.json_bytes(row) for row in rows))
        extra = raw / 'extra.request.json'; extra.write_bytes((raw / 'first.request.json').read_bytes())
        with self.assertRaisesRegex(rp.ProbeError, 'probe_transport_unverified'):
            rp.inspect_probe(self.destination, manifest, self.ns)
        extra.unlink()
        (raw / 'index.jsonl').write_bytes(b'{}\n')
        with self.assertRaisesRegex(rp.ProbeError, 'probe_transport_unverified'):
            rp.inspect_probe(self.destination, manifest, self.ns)

    def test_nonempty_conflicting_response_is_never_replaced_by_stream_evidence(self):
        manifest = self.prepare(); self.append_probe(manifest); self.add_matching_stream_response(manifest)
        response = self.destination / 'capture/raw-bodies/first.response.json'
        write(response, {'type': 'message', 'id': 'wrong-id', 'model': self.ns.MODEL})
        with self.assertRaisesRegex(rp.ProbeError, 'probe_transport_unverified'):
            rp.inspect_probe(self.destination, manifest, self.ns)

    def source_environment(self):
        text = ('# Environment\nYou have been invoked in the following environment: \n'
            ' - Primary working directory: /tmp/at-native/work\n - Is a git repository: false\n'
            ' - Platform: linux\n - Shell: bash\n - OS Version: Linux 6.12.76-linuxkit\n\n'
            'You are powered by the model named Opus 5.5 (1M context). The exact model ID is claude-opus-5-5[1m]. '
            'Assistant knowledge cutoff is June 2026.\n\nToday\'s date is 2026-10-09.')
        envelope = {'role': 'system', 'content': [{'type': 'text', 'text': text,
            'cache_control': {'type': 'ephemeral', 'ttl': '1h'}}], 'output_config': {'effort': 'max'}}
        request = self.state / 'archive/capture/raw-bodies/first.request.json'
        body = rp.read_json(request); body['messages'].append(envelope); write(request, body); self.reseal()
        return text

    def add_probe_environment(self, text):
        request = self.destination / 'capture/raw-bodies/first.request.json'
        body = rp.read_json(request)
        body['messages'].insert(1, {'role': 'system', 'content': text, 'output_config': {'effort': 'max'}})
        body['messages'][-1]['content'][0]['cache_control'] = {'type': 'ephemeral', 'ttl': '1h'}
        write(request, body)
        return request, body

    def test_native_environment_matches_archived_original_despite_cache_representation(self):
        text = self.source_environment()
        manifest = self.prepare(); self.append_probe(manifest)
        request, body = self.add_probe_environment(text)
        try: evidence = rp.inspect_probe(self.destination, manifest, self.ns)
        except rp.ProbeError as exc: self.fail('The pinned original native environment must survive cache representation changes: ' + str(exc))
        self.assertTrue(evidence['inherited_context_verified'])
        self.assertEqual(evidence['native_environment_sha256'], rp.sha(text.encode()))
        for mutation in ['text', 'extra_field', 'duplicate', 'cache']:
            changed = json.loads(json.dumps(body))
            if mutation == 'text': changed['messages'][1]['content'] += '\nInjected instruction.'
            elif mutation == 'extra_field': changed['messages'][1]['injected'] = 'instruction'
            elif mutation == 'duplicate': changed['messages'].insert(2, changed['messages'][1])
            elif mutation == 'cache': changed['messages'][-1]['content'][0]['cache_control']['ttl'] = 'unreviewed'
            write(request, changed)
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(rp.ProbeError, 'inherited_context_unverified'):
                    rp.inspect_probe(self.destination, manifest, self.ns)
        write(request, body)
        original_request = self.destination / 'original-capture/raw-bodies/first.request.json'
        changed = rp.read_json(original_request); changed['messages'][1]['content'][0]['text'] += '\nInjected instruction.'
        write(original_request, changed)
        with self.assertRaisesRegex(rp.ProbeError, 'original_context_pin_mismatch'):
            rp.inspect_probe(self.destination, manifest, self.ns)

    def test_restore_accepts_only_predeclared_native_host_environment_update(self):
        text = self.source_environment()
        manifest = self.prepare(); self.append_probe(manifest)
        request, body = self.add_probe_environment(text)
        native_text = ('# Environment\nYou have been invoked in the following environment: \n'
            ' - Primary working directory: /tmp/at-native/work\n - Is a git repository: false\n'
            ' - Platform: linux\n - Shell: bash\n - OS Version: ' + os.uname().sysname + ' ' + os.uname().release)
        update = {'role':'system','content':[{'type':'text','text':native_text,
            'cache_control':{'type':'ephemeral','ttl':'1h'}}]}
        body['messages'].append(update); write(request,body)
        evidence = rp.inspect_probe(self.destination, manifest, self.ns)
        self.assertEqual(evidence['native_target_environment_sha256'],rp.sha(native_text.encode()))
        for changed in (native_text+'\nInjected instruction.', native_text.replace('bash','sh')):
            body['messages'][-1]['content'][0]['text']=changed; write(request,body)
            with self.assertRaisesRegex(rp.ProbeError,'inherited_context_unverified'):
                rp.inspect_probe(self.destination,manifest,self.ns)

    def test_resume_cannot_add_a_system_message_absent_from_source(self):
        manifest = self.prepare(); self.append_probe(manifest)
        self.add_probe_environment('Injected system instruction.')
        with self.assertRaisesRegex(rp.ProbeError, 'inherited_context_unverified'):
            rp.inspect_probe(self.destination, manifest, self.ns)

    def fake_supervise(self, command, environment, cwd, input_bytes, boundary, *, mutate_source=False):
        self.assertEqual(input_bytes, rp.json_bytes(rp.probe_input()))
        self.assertNotIn(rp.EXPECTED_MARKER.encode(), input_bytes)
        self.assertNotIn('synthetic-placeholder-secret', ' '.join(command))
        manifest = json.loads((self.destination / 'restore-manifest.json').read_bytes())
        self.append_probe(manifest)
        capture = self.destination / 'capture'
        (capture / 'delivered-input.json').write_bytes(input_bytes)
        (capture / 'sealed-answer.txt').unlink()
        boundary.release(10)
        stream = rp.read_rows(capture / 'stream.jsonl')
        boundary.observe(stream[0], 20)
        boundary.observe({'type': 'rate_limit_event', 'rate_limit_info': {'status': 'allowed',
            'overageStatus': 'rejected', 'isUsingOverage': False}}, 30)
        boundary.observe(stream[1], 40)
        boundary.root_exit(0, 50)
        boundary.drain(60, census_empty=True, waitpid_echild=True)
        if mutate_source:
            (self.source / 'changed-while-running').write_text('mutation')
        return boundary.metadata()

    def mocked_run(self, operation='continue', mutate_source=False):
        self.destination = self.destination.resolve()
        with patch.object(rp, 'RUNTIME_ROOT', str(self.destination)), patch.object(rp.sys, 'platform', 'linux'):
            new_id = str(uuid.uuid4()) if operation == 'fork' else None
            self.prepare(operation, new_id)
            with patch.object(rp, 'load_historical', return_value=self.ns), \
                    patch.object(self.ns, 'verify_binary'), \
                    patch.object(self.ns, 'supervise', side_effect=lambda *a: self.fake_supervise(*a, mutate_source=mutate_source)):
                return rp.run(self.destination, '/opt/pinned/claude', 'synthetic-placeholder-secret')

    def test_mocked_continuation_preserves_source_and_never_replays(self):
        before = self.source_hash()
        report = self.mocked_run()
        self.assertTrue(report['native_restore_verified'])
        self.assertFalse(report['native_fork_verified'])
        self.assertFalse(report['study_ready'])
        self.assertFalse(report['independent_archive_acknowledged'])
        self.assertEqual(self.source_hash(), before)
        self.assertTrue((self.destination / 'original-capture').is_dir())
        with self.assertRaisesRegex(rp.ProbeError, 'restore_already_claimed'):
            rp.run(self.destination, '/opt/pinned/claude', 'synthetic-placeholder-secret')

    def test_mocked_fork_keeps_separate_qualification(self):
        report = self.mocked_run('fork')
        self.assertTrue(report['native_fork_verified'])
        self.assertFalse(report['native_restore_verified'])
        self.assertNotEqual(report['original_session_id'], report['expected_session_id'])

    def test_mutation_after_mocked_run_cannot_be_qualified_or_replayed(self):
        report = self.mocked_run(mutate_source=True)
        self.assertFalse(report['qualified'])
        self.assertFalse(report['source_unchanged'])
        self.assertEqual(report['error_code'], 'source_tree_changed')
        self.assertTrue(report['archive_verified'])
        with self.assertRaisesRegex(rp.ProbeError, 'restore_already_claimed'):
            rp.run(self.destination, '/opt/pinned/claude', 'synthetic-placeholder-secret')

    def test_no_replay_checks_claim_before_any_launch(self):
        self.prepare()
        write(self.destination / 'restore-started.json', {'permanent': True})
        with patch.object(rp, 'load_historical') as load:
            with self.assertRaisesRegex(rp.ProbeError, 'restore_already_claimed'):
                rp.run(self.destination, '/irrelevant/claude', 'synthetic-placeholder-secret')
            load.assert_not_called()

    def test_run_rejects_nonmatching_runtime_path_before_any_launch(self):
        self.prepare()
        with self.assertRaisesRegex(rp.ProbeError, 'runtime_path_mismatch'):
            rp.run(self.destination, '/irrelevant/claude', 'synthetic-placeholder-secret')
        self.assertFalse((self.destination / 'restore-started.json').exists())


if __name__ == '__main__':
    unittest.main()
