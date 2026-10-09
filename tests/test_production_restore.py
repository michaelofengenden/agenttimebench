"""Model-free checks of production-state restoration; no model/provider access."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import uuid

REPO = Path(os.environ.get('AGENTTIME_PRODUCTION_RESTORE_REPO',str(Path(__file__).resolve().parents[1])))
PROBE = Path(os.environ.get('AGENTTIME_PRODUCTION_RESTORE_MODULE', str(REPO / 'qualification/native_session/production_restore.py')))
CASES = REPO / 'qualification/native-session-20261009/synthetic-production-v2-4b0cbacb5040'
IDS = {'gpqa': '56b40ec7-c771-41de-b732-e36248abbe1c',
       'hle': '5a522c50-f01d-4a81-9055-1f71de9fa0ab',
       'browsecomp': '35578997-be0d-448d-9d80-e0e679f179c4',
       'assistant': '08d5002b-4400-4dca-a130-cd6aa25586b9'}
spec = importlib.util.spec_from_file_location('tested_production_restore', PROBE)
rp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rp)


def write(path, value):
    path.write_bytes(rp.json_bytes(value))


@unittest.skipUnless((CASES / IDS['gpqa'] / 'qualification-result.json').exists(), 'retained synthetic production evidence required')
class ProductionRestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.source = self.base / 'source'
        self.dest = self.base / 'restored'
        self.copy_case('gpqa')

    def copy_case(self, family):
        if self.source.exists(): shutil.rmtree(self.source)
        shutil.copytree(CASES / IDS[family], self.source, symlinks=True)
        self.family = family
        self.spec = rp.read_json(self.source / 'spec.json')
        self.pin = self.spec['runtime_pins']['bridge_sha256']

    def info(self):
        return rp.verify_source(self.source, approved_bridge_sha=self.pin, approved_runtime_worker_sha=rp.worker._LOADED_SOURCE_SHA256)

    def reseal(self):
        report = rp.read_json(self.source / 'worker-report.json')
        inv = rp.ns.inventory(self.source / 'final-state')
        report['archive']['inventory'] = inv
        report['archive']['inventory_sha256'] = rp.sha(rp.json_bytes(inv))
        write(self.source / 'worker-report.json', report)
        ack = rp.read_json(self.source / 'final-archive-ack.json')
        ack['manifest_sha256'] = rp.sha(rp.json_bytes(report['archive']))
        write(self.source / 'final-archive-ack.json', ack)

    def prepare(self, operation='continue', sid=None):
        return rp.prepare(self.source, self.dest, rp.tree_digest(self.source), operation,
                          session_id=sid, approved_bridge_sha=self.pin, approved_runtime_worker_sha=rp.worker._LOADED_SOURCE_SHA256)

    def test_actual_four_sources_validate_without_mutation(self):
        for family in IDS:
            with self.subTest(family=family):
                self.copy_case(family); before = rp.tree_digest(self.source)
                result = self.info()
                self.assertEqual(result['source_tree_sha256'], before)
                self.assertTrue(result['native_sessions_verified'])
                self.assertEqual(result['child_count'], int(family == 'browsecomp'))
                self.assertFalse(result['study_ready'])
                self.assertEqual(rp.tree_digest(self.source), before)

    def test_unapproved_runtime_pin_is_rejected_and_source_runtime_pins_stay_distinct(self):
        with self.assertRaisesRegex(rp.ProbeError,'production_runtime_pin_mismatch'):
            rp.verify_source(self.source,approved_runtime_worker_sha='0'*64)
        manifest = self.prepare()
        self.assertEqual(manifest['source_worker_sha256'],self.spec['runtime_pins']['worker_sha256'])
        self.assertEqual(manifest['approved_runtime_worker_sha256'],rp.worker._LOADED_SOURCE_SHA256)
        self.assertEqual(manifest['source_spec'],self.spec)

    def test_source_hash_changes_fail_before_copy(self):
        digest = rp.tree_digest(self.source)
        (self.source / 'final-state/work/injected.txt').write_text('synthetic mutation')
        with self.assertRaisesRegex(rp.ProbeError, 'source_tree_changed'):
            rp.prepare(self.source, self.dest, digest, 'continue',approved_runtime_worker_sha=rp.worker._LOADED_SOURCE_SHA256)
        self.assertFalse(self.dest.exists())

    def test_independent_ack_and_stop_are_required(self):
        for filename, field in [('final-archive-ack.json', 'verified'), ('external-stop.json', 'stop_verified')]:
            self.copy_case('gpqa')
            value = rp.read_json(self.source / filename); value[field] = False; write(self.source / filename, value)
            with self.assertRaises(rp.ProbeError): self.info()

    def test_archive_hash_and_native_evidence_are_independent_checks(self):
        parent = self.source / 'final-state' / rp.read_json(self.source / 'worker-report.json')['native_session_evidence']['parent']['path']
        parent.write_bytes(parent.read_bytes().replace(b'SYNTHETIC_NATIVE_OK', b'SYNTHETIC_CHANGED'))
        self.reseal()
        with self.assertRaisesRegex(rp.ProbeError, 'native|archive_evidence'):
            self.info()

    def test_missing_observed_browsecomp_child_rejected_even_after_inventory_reseal(self):
        self.copy_case('browsecomp')
        report = rp.read_json(self.source / 'worker-report.json')
        child = next(iter(report['native_session_evidence']['children'].values()))
        (self.source / 'final-state' / child['path']).unlink()
        for path in (self.source / 'final-state').rglob('*'):
            if path.is_symlink() and not path.exists(): path.unlink()
        self.reseal()
        with self.assertRaisesRegex(rp.ProbeError, 'native_child_store_missing'):
            self.info()

    def test_non_synthetic_input_is_refused_even_if_spec_hashes_are_updated(self):
        value = rp.read_json(self.source / 'input.json')
        value['message']['content'][0]['text'] = 'Answer a real benchmark task.'
        data = rp.json_bytes(value); (self.source / 'input.json').write_bytes(data)
        self.spec['input_sha256'] = rp.sha(data); write(self.source / 'spec.json', self.spec)
        with self.assertRaisesRegex(rp.ProbeError, 'synthetic_input_required'):
            self.info()

    def test_v2_browser_missing_snapshot_is_explicit_and_blocks_prepare(self):
        self.copy_case('assistant')
        info = self.info()
        self.assertFalse(info['browser_state_available'])
        with self.assertRaisesRegex(rp.ProbeError, 'browser_snapshot_unavailable'):
            self.prepare()
        self.assertFalse(self.dest.exists())

    def test_assistant_requires_explicit_approved_bridge_pin(self):
        self.copy_case('assistant')
        with self.assertRaisesRegex(rp.ProbeError, 'approved_bridge_pin_required'):
            rp.verify_source(self.source,approved_runtime_worker_sha=rp.worker._LOADED_SOURCE_SHA256)

    def test_prepare_copies_six_roots_exactly_and_preserves_modes_and_links(self):
        state = self.source / 'final-state'
        folder = state / 'work/read-only'; folder.mkdir(); (folder / 'a').write_text('fixture')
        folder.chmod(0o555); (state / 'work').chmod(0o755); self.reseal()
        before = rp.tree_digest(self.source); old = os.umask(0o077)
        try: manifest = self.prepare()
        finally: os.umask(old); folder.chmod(0o755)
        self.assertEqual(rp.ns.inventory(self.dest), manifest['restored_inventory'])
        self.assertEqual((self.dest / 'work/read-only').stat().st_mode & 0o777, 0o555)
        self.assertEqual((self.dest / 'work').stat().st_mode & 0o777, 0o755)
        self.assertTrue((self.dest / 'capture/latest').is_symlink())
        self.assertFalse(manifest['study_ready'])
        (self.dest / 'work/read-only').chmod(0o755)
        folder.chmod(0o555); self.assertEqual(rp.tree_digest(self.source), before); folder.chmod(0o755)

    def test_fork_requires_distinct_canonical_session(self):
        for invalid in (None, self.spec['session_id'], 'bad'):
            with self.assertRaisesRegex(rp.ProbeError, 'fresh_fork_session_required'):
                self.prepare('fork', invalid)
        target = str(uuid.uuid4()); manifest = self.prepare('fork', target)
        self.assertEqual(manifest['expected_session_id'], target)

    def test_existing_or_overlapping_destination_is_refused(self):
        with self.assertRaisesRegex(rp.ProbeError, 'source_destination_overlap'):
            rp.prepare(self.source, self.source / 'new', rp.tree_digest(self.source), 'continue')
        self.dest.mkdir()
        with self.assertRaisesRegex(rp.ProbeError, 'destination_not_fresh'):
            self.prepare()

    def browser_source_with_snapshot(self):
        self.copy_case('assistant'); self.pin = rp.ns._file_sha(rp.worker._BRIDGE_PATH)
        self.spec['runtime_pins']['bridge_sha256'] = self.pin
        write(self.source/'spec.json',self.spec)
        write(self.source/'final-state/capture/prepared-spec.json',self.spec)
        report = rp.read_json(self.source/'worker-report.json')
        report['spec_sha256'] = rp.sha(rp.json_bytes(self.spec)); write(self.source/'worker-report.json',report)
        snapshot = {'schema_version':'agenttime.browsergym-state.v1','browsergym_version':'0.14.3',
            'goal_sha256':rp.sha(rp.ASSISTANT_INPUT.encode()),'storage_state':{'cookies':[],'origins':[]},
            'pages':['https://example.com/'],'active_page_index':0,'profile':rp._bridge(self.pin)._PROFILE}
        path = self.source/'final-state/capture/browser-state/browser-state.json'; write(path,snapshot)
        receipt = {'schema_version':1,'attempt_id':self.spec['attempt_id'],'sha256':rp.ns._file_sha(path),
            'bytes':path.stat().st_size,'captured_at_monotonic_ns':1,
            'persisted':['cookies','localStorage','open_page_urls','active_page_index'],
            'full_session_restore_qualified':False}
        write(path.with_name('browser-state-receipt.json'),receipt); self.reseal()
        return snapshot

    def test_verified_browser_snapshot_is_copied_and_bridge_is_private(self):
        self.browser_source_with_snapshot()
        self.assertTrue(self.info()['browser_state_available'])
        manifest = self.prepare('fork',str(uuid.uuid4()))
        with patch.object(rp.worker,'validate_spec'):
            conf = rp.configuration(self.dest,'/pinned/claude',manifest,'synthetic-auth-not-a-real-token')
        cmd = conf['command']; mcp = json.loads(cmd[cmd.index('--mcp-config')+1])
        bridge = mcp['mcpServers']['assistantbench']
        self.assertEqual(bridge['command'],'/usr/bin/env')
        self.assertEqual(bridge['args'][0],'-i')
        self.assertNotIn('synthetic-auth-not-a-real-token',json.dumps(mcp))
        self.assertIn('bridge',bridge['args'])
        self.assertEqual(cmd[cmd.index('--tools')+1],'')
        self.assertEqual(manifest['browser_snapshot']['sha256'],rp.ns._file_sha(self.dest/'capture/browser-state/browser-state.json'))

    def test_browser_snapshot_digest_and_task_binding_are_required(self):
        for mutation in ('digest','goal'):
            self.browser_source_with_snapshot()
            path = self.source/'final-state/capture/browser-state/browser-state.json'
            value = rp.read_json(path); value['goal_sha256'] = 'a'*64; write(path,value)
            if mutation == 'goal':
                receipt_path = path.with_name('browser-state-receipt.json'); receipt = rp.read_json(receipt_path)
                receipt['sha256'] = rp.ns._file_sha(path); receipt['bytes'] = path.stat().st_size; write(receipt_path,receipt)
            self.reseal()
            with self.assertRaisesRegex(rp.ProbeError,'browser_snapshot_unverified'): self.info()

    def test_permanent_restore_claim_prevents_any_second_launch(self):
        self.prepare(); write(self.dest/'restore-started.json',{'claimed':True})
        with patch.object(rp,'RUNTIME_ROOT',str(self.dest)), patch.object(rp.sys,'platform','linux'), patch.object(rp.ns,'supervise') as launch:
            with self.assertRaisesRegex(rp.ProbeError,'restore_already_claimed'):
                rp.run(self.dest,'/pinned/claude','synthetic-auth-not-a-real-token')
            launch.assert_not_called()

    def test_configuration_uses_native_resume_no_auth_arguments_or_fallback(self):
        manifest = self.prepare('fork', str(uuid.uuid4()))
        token = 'synthetic-token-for-offline-configuration'
        with patch.object(rp.worker, 'validate_spec'):
            conf = rp.configuration(self.dest, '/pinned/claude', manifest, token)
        cmd = conf['command']; env = conf['environment']
        self.assertEqual(cmd[cmd.index('--resume')+1], self.spec['session_id'])
        self.assertEqual(cmd[cmd.index('--session-id')+1], manifest['expected_session_id'])
        self.assertIn('--fork-session', cmd)
        self.assertNotIn(token, repr(cmd))
        self.assertEqual(env['CLAUDE_CODE_OAUTH_TOKEN'], token)
        self.assertNotIn('ANTHROPIC_API_KEY', env)
        self.assertNotIn('ANTHROPIC_BASE_URL', env)
        self.assertEqual(env['CLAUDE_CODE_MAX_RETRIES'], '0')
        self.assertEqual(env['CLAUDE_CODE_NO_MODEL_FALLBACK'], '1')
        self.assertEqual(env['CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS'], '0')
        for limit in ('--max-turns', '--max-budget-usd'): self.assertNotIn(limit, cmd)

    def test_fixed_probe_contains_no_previous_answer_or_original_task(self):
        for family in IDS:
            event = rp.probe_input(family)
            self.assertEqual(event['type'], 'user')
            self.assertEqual(event['message']['role'], 'user')
            self.assertEqual(len(event['message']['content']), 1)
            text = event['message']['content'][0]['text']
            self.assertNotIn('SYNTHETIC_NATIVE_OK', text)
            self.assertNotIn('Example Domain', text)
            self.assertNotIn('minutes', text)

    def test_browser_rehydration_reports_partial_state_only(self):
        expected = {'schema_version': 'agenttime.browsergym-state.v1', 'browsergym_version': '0.14.3',
            'goal_sha256': 'a'*64, 'profile': {}, 'storage_state': {'cookies': [{'name':'x','value':'1'}],
            'origins': [{'origin':'https://example.com', 'localStorage':[{'name':'key','value':'value'}]}]},
            'pages':['https://example.com/'], 'active_page_index':0}
        actual = copy.deepcopy(expected)
        report = rp.compare_browser_state(expected, actual)
        self.assertTrue(report['persisted_state_matches'])
        self.assertFalse(report['full_browser_process_restored'])
        actual['storage_state']['origins'][0]['localStorage'][0]['value'] = 'changed'
        with self.assertRaisesRegex(rp.ProbeError, 'browser_restoration_mismatch'):
            rp.compare_browser_state(expected, actual)

    def test_browser_rehydration_rejects_missing_page_or_added_cookie(self):
        expected = {'storage_state': {'cookies': [], 'origins': []}, 'pages':['https://example.com/'], 'active_page_index':0,
                    'profile':{}, 'goal_sha256':'a'*64, 'browsergym_version':'0.14.3'}
        for field, value in [('pages',['about:blank']), ('storage_state',{'cookies':[{'name':'x'}],'origins':[]})]:
            actual = copy.deepcopy(expected); actual[field] = value
            with self.assertRaisesRegex(rp.ProbeError, 'browser_restoration_mismatch'):
                rp.compare_browser_state(expected, actual)

    def captured_probe(self, operation='continue', family='gpqa'):
        self.copy_case(family)
        manifest = self.prepare(operation, str(uuid.uuid4()) if operation == 'fork' else None)
        (self.dest / 'capture').rename(self.dest / 'original-capture')
        capture = self.dest / 'capture'; capture.mkdir(); raw = capture / 'raw-bodies'; raw.mkdir()
        source_parent = self.source / 'final-state' / manifest['native_session_evidence']['parent']['path']
        original = rp.read_rows(source_parent)
        messages = [r for r in original if r.get('type') in ('user','assistant')]
        sid = manifest['expected_session_id']; request_id = 'req_synthetic_probe'; message_id = 'msg_synthetic_probe'
        user = copy.deepcopy(messages[0]); user.update(sessionId=sid, uuid=str(uuid.uuid4()),parentUuid=messages[-1]['uuid'])
        user['message'] = rp.probe_input(family)['message']
        assistant = copy.deepcopy(messages[-1]); assistant.update(sessionId=sid, uuid=str(uuid.uuid4()),parentUuid=user['uuid'],requestId=request_id)
        answer = (self.dest / 'original-capture/sealed-answer.txt').read_text()
        assistant['message'].update(id=message_id,content=[{'type':'text','text':answer}])
        parent = self.dest / manifest['native_session_evidence']['parent']['path']
        target = parent.with_name(sid+'.jsonl')
        target.write_bytes(source_parent.read_bytes()+rp.json_bytes(user)+rp.json_bytes(assistant))
        body = {'model':rp.ns.MODEL,'output_config':{'effort':'max'},
            'tools':[{'name':name} for name in self.spec['tool_policy']['native_tools']],
            'messages':rp._original_history(self.dest,manifest)+[rp.probe_input(family)['message']],
            'metadata':{'user_id':json.dumps({'session_id':sid})}}
        write(raw/'probe.request.json',body)
        write(raw/(request_id+'.response.json'),{'type':'message','id':message_id,'model':rp.ns.MODEL})
        record = {'request_file':'probe.request.json','response_file':request_id+'.response.json',
            'query_source':'sdk','session_id':sid,'model':rp.ns.MODEL,'request_id':request_id,'message_id':message_id}
        write(raw/'index.jsonl',record)
        init = {'type':'system','subtype':'init','session_id':sid,'model':rp.ns.CLI_MODEL,
            'claude_code_version':rp.ns.CLI_VERSION,'tools':self.spec['tool_policy']['native_tools'],
            'mcp_servers':[],'skills':[],'plugins':[]}
        event = {'type':'assistant','session_id':sid,'uuid':assistant['uuid'],'request_id':request_id,
            'parent_tool_use_id':None,'message':assistant['message']}
        result = {'type':'result','subtype':'success','is_error':False,'session_id':sid,'result':answer}
        (capture/'stream.jsonl').write_bytes(b''.join(rp.json_bytes(r) for r in (init,event,result)))
        (capture/'sealed-answer.txt').write_text(answer)
        return manifest

    def test_offline_continue_and_fork_bind_original_context_and_native_stores(self):
        for operation in ('continue','fork'):
            if self.dest.exists(): shutil.rmtree(self.dest)
            manifest = self.captured_probe(operation)
            result = rp.inspect_probe(self.dest,manifest)
            self.assertTrue(result['inherited_context_verified'])
            self.assertTrue(result['original_parent_preserved'])
            self.assertTrue(result['native_store_verified'])
            self.assertTrue(result['transport']['index_available'])

    def test_parent_fork_preserves_actual_original_child_store(self):
        manifest = self.captured_probe('fork','browsecomp')
        result = rp.inspect_probe(self.dest,manifest)
        self.assertEqual(result['restored_child_count'],1)
        self.assertFalse(result['child_continuation_independently_tested'])
        child = next(iter(manifest['native_session_evidence']['children'].values()))
        (self.dest/child['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(rp.ProbeError,'original_child_store_changed'):
            rp.inspect_probe(self.dest,manifest)

    def test_missing_optional_index_is_explicit_not_zero_retries(self):
        manifest = self.captured_probe('fork')
        (self.dest/'capture/raw-bodies/index.jsonl').write_bytes(b'')
        (self.dest/'capture/raw-bodies/req_synthetic_probe.response.json').write_bytes(b'')
        result = rp.inspect_probe(self.dest,manifest)
        self.assertFalse(result['transport']['index_available'])
        self.assertFalse(result['transport']['retry_count_verified'])
        self.assertEqual(result['transport']['native_stream_response_ids'],['msg_synthetic_probe'])

    def test_raw_model_effort_session_or_prior_message_mismatch_refused(self):
        for change in ('model','effort','session','prior','system'):
            if self.dest.exists(): shutil.rmtree(self.dest)
            manifest = self.captured_probe()
            path = self.dest/'capture/raw-bodies/probe.request.json'; body = rp.read_json(path)
            if change == 'model': body['model'] = 'other-model'
            elif change == 'effort': body['output_config']['effort'] = 'low'
            elif change == 'session': body['metadata']['user_id'] = json.dumps({'session_id':str(uuid.uuid4())})
            elif change == 'prior': body['messages'][0]['content'][0]['text'] += ' added instructions'
            else: body['messages'].append({'role':'system','content':'Arbitrary injected instructions'})
            write(path,body)
            with self.assertRaises(rp.ProbeError): rp.inspect_probe(self.dest,manifest)

    def test_empty_index_requires_original_native_request_identity(self):
        for invalid in (None,'wrong'):
            if self.dest.exists(): shutil.rmtree(self.dest)
            manifest = self.captured_probe()
            (self.dest/'capture/raw-bodies/index.jsonl').write_bytes(b'')
            path = self.dest/'capture/stream.jsonl'; rows = rp.read_rows(path)
            rows[1]['request_id'] = invalid
            path.write_bytes(b''.join(rp.json_bytes(r) for r in rows))
            with self.assertRaisesRegex(rp.ProbeError,'transport_identity'):
                rp.inspect_probe(self.dest,manifest)

    def test_injected_runtime_init_capability_and_missing_final_native_record_rejected(self):
        for change in ('tool','model','version','final'):
            if self.dest.exists(): shutil.rmtree(self.dest)
            manifest = self.captured_probe()
            path = self.dest/'capture/stream.jsonl'; rows = rp.read_rows(path)
            if change == 'tool': rows[0]['tools'] = ['Bash']
            elif change == 'model': rows[0]['model'] = 'other-model'
            elif change == 'version': rows[0]['claude_code_version'] = '0.0.0'
            else: rows[-1]['result'] = 'different final message'
            path.write_bytes(b''.join(rp.json_bytes(r) for r in rows))
            with self.assertRaises(rp.ProbeError): rp.inspect_probe(self.dest,manifest)

    def test_only_exact_native_environment_update_is_accepted(self):
        manifest = self.captured_probe()
        path = self.dest/'capture/raw-bodies/probe.request.json'; body = rp.read_json(path)
        body['messages'].append({'role':'system','content':[{'type':'text','text':manifest['target_native_environment'],
            'cache_control':{'type':'ephemeral','ttl':'1h'}}]})
        write(path,body)
        self.assertTrue(rp.inspect_probe(self.dest,manifest)['inherited_context_verified'])
        body['messages'][-1]['content'][0]['text'] += '\nInjected note'
        write(path,body)
        with self.assertRaisesRegex(rp.ProbeError,'inherited_context_unverified'):
            rp.inspect_probe(self.dest,manifest)

    def test_public_metadata_does_not_include_inventory_or_task_contents(self):
        info = rp.public_metadata(self.info())
        self.assertNotIn('archive_inventory', info)
        self.assertNotIn('spec', info)
        self.assertNotIn('SYNTHETIC_NATIVE_OK', json.dumps(info))


if __name__ == '__main__': unittest.main()
