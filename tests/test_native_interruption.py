"""Offline checks for the fixed interrupted-native-turn qualification probe."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import textwrap
import unittest
import uuid

PATH = Path(os.environ.get('INTERRUPTION_PROBE_PATH', Path(__file__).resolve().parents[1] / 'qualification/native_session/interruption_probe.py'))
spec = importlib.util.spec_from_file_location('interruption_probe_tested', PATH)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class InterruptionProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'runtime-root'
        self.sid = str(uuid.uuid4())
        p.prepare(self.root, 'synthetic-interruption-fixture', self.sid)
        self.contract, self.payload = p.verify_prepared(self.root)

    def baseline_ack(self):
        return {'schema_version': p.SCHEMA + '.baseline-ack',
            'session_id': self.sid, 'baseline_sha256': p.file_sha(self.root / 'baseline.json'),
            'independent_archive_verified': True, 'archive_location': '/independent/baseline'}

    def native_rows(self):
        return [
            {'type': 'user', 'sessionId': self.sid, 'cwd': str(self.root / 'work'), 'uuid': 'original-user',
             'message': p.input_value()['message']},
            {'type': 'assistant', 'sessionId': self.sid, 'cwd': str(self.root / 'work'), 'uuid': 'tool-call',
             'message': {'role': 'assistant', 'model': p.ns.MODEL, 'content': [{'type': 'tool_use', 'id': 'once-id',
                 'name': p.TOOL_NAME, 'input': {}}]}},
            {'type': 'user', 'sessionId': self.sid, 'cwd': str(self.root / 'work'), 'uuid': 'tool-result',
             'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'once-id',
                 'content': [{'type': 'text', 'text': p.TOOL_RESULT}]}]}},
        ]

    def test_fixed_contract_rejects_study_input_and_unknown_keys(self):
        data = json.loads((self.root / 'contract.json').read_bytes())
        data['input']['message']['content'][0]['text'] = 'actual study question'
        (self.root / 'contract.json').write_bytes(p.json_bytes(data))
        with self.assertRaisesRegex(p.ProbeError, 'fixed_synthetic_contract_required'):
            p.verify_prepared(self.root)

    def test_baseline_ack_and_pristine_state_required(self):
        p.verify_baseline(self.root, self.contract, self.baseline_ack())
        bad = dict(self.baseline_ack(), independent_archive_verified=False)
        with self.assertRaisesRegex(p.ProbeError, 'baseline_ack_mismatch'):
            p.verify_baseline(self.root, self.contract, bad)
        (self.root / 'work' / 'unrelated').write_text('not pristine')
        with self.assertRaisesRegex(p.ProbeError, 'baseline_state_changed'):
            p.verify_baseline(self.root, self.contract, self.baseline_ack())

    def test_configuration_has_no_prompt_or_secret_in_argv_and_strips_tool_auth(self):
        token = 'synthetic-token-not-real'
        config = p.configuration(self.root, '/runtime/claude', self.contract, token, resume=True)
        cmd, env = config['command'], config['environment']
        self.assertEqual(env['CLAUDE_CODE_OAUTH_TOKEN'], token)
        self.assertEqual(env['CLAUDE_CODE_RESUME_INTERRUPTED_TURN'], '1')
        self.assertNotIn('CLAUDE_CODE_RESUME_PROMPT', env)
        self.assertNotIn('ANTHROPIC_API_KEY', env)
        self.assertNotIn(token, json.dumps(cmd))
        self.assertNotIn(p.PROMPT, cmd)
        self.assertNotIn('--session-id', cmd)
        self.assertEqual(cmd[cmd.index('--resume') + 1], self.sid)
        self.assertEqual(config['stdin_bytes'], b'')
        mcp = json.loads(cmd[cmd.index('--mcp-config') + 1])
        tool = mcp['mcpServers']['interruption']
        self.assertEqual(tool['command'], '/usr/bin/env')
        self.assertEqual(tool['args'][0], '-i')
        self.assertNotIn(token, json.dumps(mcp))
        self.assertNotIn('ANTHROPIC_BASE_URL', env)

    def test_onset_and_resume_intents_never_replay_after_errors(self):
        for phase in ('original', 'resume'):
            p.claim_once(self.root, phase, self.contract)
            with self.assertRaisesRegex(p.ProbeError, 'segment_already_claimed'):
                p.claim_once(self.root, phase, self.contract)

    def test_tool_durably_records_attempts_but_effect_only_once(self):
        self.assertEqual(p.record_once(self.root), p.TOOL_RESULT)
        with self.assertRaisesRegex(p.ProbeError, 'duplicate_tool_effect_attempt'):
            p.record_once(self.root)
        self.assertEqual((self.root / 'work/once-effect.txt').read_text(), p.EFFECT_TEXT)
        self.assertEqual(len(p.read_rows(self.root / 'work/tool-calls.jsonl')), 2)
        with self.assertRaisesRegex(p.ProbeError, 'tool_not_exactly_once'):
            p.verify_effect(self.root)

    def test_checkpoint_requires_one_completed_native_tool_result(self):
        rows = self.native_rows()
        evidence = p.check_native_rows(rows, self.contract, self.root, resumed=False)
        self.assertEqual(evidence['tool_use_id'], 'once-id')
        self.assertEqual(evidence['task_prompt_count'], 1)
        for label, change in [
            ('wrong tool ID', lambda rs: rs[-1]['message']['content'][0].update(tool_use_id='other')),
            ('error result', lambda rs: rs[-1]['message']['content'][0].update(is_error=True)),
            ('wrong session', lambda rs: rs[-1].update(sessionId=str(uuid.uuid4()))),
            ('extra task', lambda rs: rs.append(rs[0])),
        ]:
            with self.subTest(label=label):
                modified = json.loads(json.dumps(rows)); change(modified)
                with self.assertRaises(p.ProbeError):
                    p.check_native_rows(modified, self.contract, self.root, resumed=False)

    def test_final_native_marker_allowed_but_replayed_prompt_or_tool_rejected(self):
        rows = self.native_rows()
        rows += [{'type': 'user', 'sessionId': self.sid, 'cwd': str(self.root / 'work'), 'isMeta': True,
                  'uuid': 'native-continuation', 'message': {'role': 'user', 'content': p.NATIVE_RESUME_MARKER}},
                 {'type': 'assistant', 'sessionId': self.sid, 'cwd': str(self.root / 'work'), 'uuid': 'final',
                  'message': {'role': 'assistant', 'model': p.ns.MODEL,
                              'content': [{'type': 'text', 'text': p.FINAL_MARKER}]}}]
        evidence = p.check_native_rows(rows, self.contract, self.root, resumed=True)
        self.assertEqual(evidence['native_recovery_marker_count'], 1)
        self.assertFalse(evidence['no_added_context_claim'])
        for extra in (self.native_rows()[0], self.native_rows()[1]):
            with self.assertRaises(p.ProbeError):
                p.check_native_rows(rows + [extra], self.contract, self.root, resumed=True)

    def interrupted_receipt(self):
        return {'schema_version': p.SCHEMA + '.interrupted', 'session_id': self.sid,
            'qualification_id': self.contract['qualification_id'], 'boot_id': str(uuid.uuid4()),
            'owner_pid': 100, 'process_identities': {'123': 10001}, 'original_exit_code': -9,
            'original_stopped': True, 'owned_census_empty': True, 'waitpid_echild': True,
            'checkpoint_inventory_sha256': 'a' * 64, 'checkpoint_transcript_sha256': 'b' * 64}

    def recovery_ack(self, receipt):
        return {'schema_version': p.SCHEMA + '.interruption-ack', 'session_id': self.sid,
            'interruption_sha256': p.sha(p.json_bytes(receipt)), 'boot_id': receipt['boot_id'],
            'owner_pid': receipt['owner_pid'], 'process_identities': receipt['process_identities'],
            'checkpoint_inventory_sha256': receipt['checkpoint_inventory_sha256'],
            'original_stopped_verified': True, 'independent_archive_verified': True,
            'archive_location': '/independent/interrupted'}

    def test_resume_gate_requires_external_stop_matching_identity_and_archive(self):
        receipt = self.interrupted_receipt(); ack = self.recovery_ack(receipt)
        p.verify_interruption_ack(receipt, ack, receipt['boot_id'])
        for key, value in [('original_stopped_verified', False), ('independent_archive_verified', False),
                           ('process_identities', {'124': 10001}), ('interruption_sha256', 'c' * 64)]:
            with self.subTest(key=key), self.assertRaises(p.ProbeError):
                p.verify_interruption_ack(receipt, dict(ack, **{key: value}), receipt['boot_id'])
        bad = dict(receipt, original_stopped=False)
        with self.assertRaisesRegex(p.ProbeError, 'original_execution_stop_unproven'):
            p.verify_interruption_ack(bad, self.recovery_ack(bad), bad['boot_id'])

    def test_cross_boot_continuation_and_nonmonotonic_clock_rejected(self):
        receipt = self.interrupted_receipt()
        with self.assertRaisesRegex(p.ProbeError, 'same_boot_continuation_required'):
            p.verify_interruption_ack(receipt, self.recovery_ack(receipt), str(uuid.uuid4()))
        journal = p.Timeline(self.root / 'timeline.jsonl', self.sid, receipt['boot_id'])
        journal.event('original_released', 10)
        with self.assertRaisesRegex(p.ProbeError, 'nonmonotonic_clock'):
            journal.event('checkpoint_fsynced', 9)

    def test_runtime_includes_interruption_archive_and_restart_gap(self):
        journal = p.Timeline(self.root / 'timeline.jsonl', self.sid, str(uuid.uuid4()))
        for kind, ns in [('original_released', 1_000_000_000), ('checkpoint_fsynced', 2_000_000_000),
                         ('original_sigkill', 3_000_000_000), ('original_drained', 4_000_000_000),
                         ('checkpoint_archived', 5_000_000_000), ('independent_recovery_ack', 9_000_000_000),
                         ('resume_released', 10_000_000_000), ('resume_result', 14_000_000_000),
                         ('resume_drained', 15_000_000_000)]:
            journal.event(kind, ns)
        evidence = journal.metrics()
        self.assertEqual(evidence['runtime_seconds_including_interruption'], 14)
        self.assertEqual(evidence['interruption_seconds_including_archival_and_restart'], 7)
        with self.assertRaisesRegex(p.ProbeError, 'duplicate_boundary'):
            journal.event('resume_released', 16_000_000_000)

    def test_stream_requires_native_resume_reason_and_strict_mcp(self):
        state = p.StreamState(self.contract, resumed=True)
        init = {'type': 'system', 'subtype': 'init', 'session_id': self.sid,
                'model': p.ns.CLI_MODEL, 'claude_code_version': p.ns.CLI_VERSION,
                'tools': [p.TOOL_NAME], 'mcp_servers': [{'name': 'interruption', 'status': 'connected'}],
                'plugins': [], 'skills': []}
        state.observe(init)
        result = {'type': 'result', 'subtype': 'success', 'is_error': False,
                  'session_id': self.sid, 'result': p.FINAL_MARKER, 'resume_reason': 'interrupted_turn'}
        state.observe(result)
        self.assertEqual(state.result['resume_reason'], 'interrupted_turn')
        bad = p.StreamState(self.contract, resumed=True); bad.observe(init)
        with self.assertRaisesRegex(p.ProbeError, 'native_resume_reason_unverified'):
            bad.observe({k:v for k,v in result.items() if k != 'resume_reason'})
        bad = p.StreamState(self.contract, resumed=False)
        with self.assertRaisesRegex(p.ProbeError, 'native_capabilities_unverified'):
            bad.observe(dict(init, tools=[p.TOOL_NAME, 'Bash']))
        bad = p.StreamState(self.contract, resumed=False); bad.observe(init)
        with self.assertRaisesRegex(p.ProbeError, 'original_completed_before_interruption'):
            bad.observe(result)

    def test_paid_or_rejected_native_usage_never_qualifies(self):
        for info in ({'isUsingOverage': True}, {'status': 'rejected'}):
            with self.assertRaisesRegex(p.ProbeError, 'subscription_route_unverified'):
                p.StreamState(self.contract, resumed=False).observe({'type': 'rate_limit_event', 'rate_limit_info': info})

    def test_physical_stopped_state_and_pid_starttime_required(self):
        self.assertTrue(p.identities_stopped({'123': 45}, {123: (45, 'T')}))
        self.assertFalse(p.identities_stopped({'123': 45}, {123: (46, 'T')}))
        self.assertFalse(p.identities_stopped({'123': 45}, {123: (45, 'R')}))
        self.assertFalse(p.identities_stopped({'123': 45}, {}))

    def test_checkpoint_archive_mutation_blocks_resume(self):
        p.record_once(self.root)
        p.ns.capture_archive(self.root, 'synthetic-token-not-real')
        (self.root / 'archive').rename(self.root / 'interrupted-archive')
        receipt = p.read_json(self.root / 'archive-receipt.json')
        expected = receipt['inventory_sha256']
        p.verify_checkpoint(self.root, expected)
        (self.root / 'interrupted-archive/work/once-effect.txt').write_text('modified')
        with self.assertRaisesRegex(p.ProbeError, 'checkpoint_archive_changed'):
            p.verify_checkpoint(self.root, expected)

    def test_unknown_contract_key_is_rejected(self):
        contract = p.read_json(self.root / 'contract.json'); contract['custom_prompt'] = 'forbidden'
        (self.root / 'contract.json').write_bytes(p.json_bytes(contract))
        with self.assertRaisesRegex(p.ProbeError, 'fixed_synthetic_contract_required'):
            p.verify_prepared(self.root)

    def test_malformed_rpc_is_rejected_without_effects_or_traceback(self):
        result = subprocess.run([sys.executable, str(PATH), 'tool', '--root', str(self.root)],
            input=b'{broken json}\n', stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['error']['message'], 'synthetic_tool_rejected')
        self.assertEqual(result.stderr, b'')
        self.assertFalse((self.root / 'work/once-effect.txt').exists())

    @unittest.skipUnless(sys.platform == 'linux', 'requires a dedicated local Linux subprocess; no model or network')
    def test_linux_detached_descendants_are_physically_frozen_killed_and_reaped(self):
        script = r"""
import ctypes, importlib.util, json, os, signal, subprocess, sys, time
from pathlib import Path
spec=importlib.util.spec_from_file_location('probe',sys.argv[1]); p=importlib.util.module_from_spec(spec); spec.loader.exec_module(p)
assert ctypes.CDLL(None).prctl(36,1,0,0,0)==0
assert not p.ns._owned_processes()
child="import os,time; p=os.fork(); os.setsid() if p==0 else None; time.sleep(20)"
proc=subprocess.Popen([sys.executable,'-c',child],start_new_session=True)
end=time.monotonic()+5
while len(p.ns._owned_processes())<2 and time.monotonic()<end: time.sleep(.01)
ids=p.freeze_owned(); assert len(ids)>=2
assert p.identities_stopped(ids,p.process_table())
result=p.kill_and_drain(proc)
assert result=={'root_exit_code':-signal.SIGKILL,'owned_census_empty':True,'waitpid_echild':True}
assert not p.ns._owned_processes()
print(json.dumps({'detached_descendants_fenced':True,'drained':True,'model_calls':0}))
"""
        result = subprocess.run([sys.executable, '-c', script, str(PATH)], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertTrue(json.loads(result.stdout)['detached_descendants_fenced'])


if __name__ == '__main__':
    unittest.main()
