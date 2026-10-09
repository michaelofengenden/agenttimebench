"""Synthetic bridge boundary tests. No task data, model or grader is loaded."""

import ast
import concurrent.futures
import hashlib
import io
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import threading
import time
import unittest
import uuid
import zlib

try:
    from agenttime import assistantbench_bridge as bridge_module
except ImportError:
    bridge_module = None


def png():
    def chunk(kind, body):
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\0\xff\0\0')) + chunk(b'IEND', b''))


class FakeBackend:
    def __init__(self):
        self.calls = []; self.closed = False; self.active = 0; self.peak_active = 0
        self.observation = {'accessibility': "[1] textbox 'Synthetic value'", 'screenshot_png': png(),
            'open_pages_urls': ['https://example.org/'], 'open_pages_titles': ['Synthetic'],
            'active_page_index': 0, 'last_action_error': '/private/controller/state/scorer.py: GOLD_SECRET',
            'gold': 'GOLD_SECRET', 'reward': 0.8, 'info': {'answer': 'GOLD_SECRET'},
            'elapsed_time': 123, 'host_path': '/private/controller/state'}

    def reset(self, on_submission):
        self.on_submission = on_submission
        return self.observation

    def step(self, name, arguments):
        self.active += 1; self.peak_active = max(self.peak_active, self.active)
        try:
            time.sleep(0.005)
            self.calls.append((name, arguments))
            if name == 'send_msg_to_user': self.on_submission(arguments['text'])
            return self.observation, 0.9, name == 'send_msg_to_user', False, {'gold': 'GOLD_SECRET'}
        finally:
            self.active -= 1

    def snapshot(self):
        return {'schema_version': 'agenttime.browsergym-state.v1', 'browsergym_version': '0.14.3',
            'goal_sha256': hashlib.sha256(b'Synthetic goal').hexdigest(),
            'storage_state': {'cookies': [], 'origins': [{'origin': 'https://example.org',
                'localStorage': [{'name': 'probe', 'value': 'synthetic-private-state'}]}]},
            'pages': ['https://example.org/'], 'active_page_index': 0,
            'profile': {'locale': 'en-US', 'timezone_id': 'America/New_York',
                'viewport': {'width': 1280, 'height': 720}}}

    def close(self):
        self.closed = True


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(bridge_module, 'The AssistantBench bridge is not implemented')
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.backend = FakeBackend()
        self.bridge = bridge_module.AssistantBenchBridge(self.backend, self.root / 'attempt', str(uuid.uuid4()))
        self.addCleanup(self.bridge.close)

    def test_observation_has_only_public_ax_tabs_and_image(self):
        result = self.bridge.call_tool('observe', {})
        self.assertEqual([b['type'] for b in result['content']], ['text', 'image'])
        text = json.dumps(result)
        for private in ('GOLD_SECRET', '/private/controller', 'reward', 'elapsed_time', 'scorer'):
            self.assertNotIn(private, text)
        self.assertIn('Synthetic value', text)
        self.assertIn('image/png', text)
        self.assertNotIn('gold', {t['name'] for t in self.bridge.list_tools()})

    def test_step_reward_and_info_never_enter_tool_response(self):
        result = self.bridge.call_tool('click', {'bid': '1'})
        self.assertEqual(self.backend.calls, [('click', {'bid': '1'})])
        self.assertNotIn('GOLD_SECRET', json.dumps(result))
        self.assertNotIn('reward', json.dumps(result))

    def test_arbitrary_python_uploads_and_unknown_arguments_are_rejected(self):
        for name, arguments in [('python', {'code': 'print(1)'}), ('eval', {'code': 'x'}),
                                ('upload_file', {'bid': '1', 'file': '/private/answer'}),
                                ('goto', {'url': 'javascript:alert(1)'}),
                                ('goto', {'url': 'file:///private/answer'}),
                                ('goto', {'url': 'https://user:secret@example.org'}),
                                ('click', {'bid': '1', 'code': 'x'}), ('click', {'bid': 1}),
                                ('scroll', {'delta_x': float('nan'), 'delta_y': 1}),
                                ('tab_focus', {'index': True}), ('tab_focus', {'index': -1}),
                                ('observe', {'path': '/private/answer'})]:
            with self.subTest(name=name, arguments=arguments):
                with self.assertRaises(ValueError): self.bridge.call_tool(name, arguments)
        self.assertEqual(self.backend.calls, [])

    def test_action_string_injection_stays_a_literal_argument(self):
        text = "x'); __import__('os').system('cat /private/answer'); #"
        code = bridge_module.render_native_action('fill', {'bid': '1', 'value': text})
        parsed = ast.parse(code)
        self.assertEqual(len(parsed.body), 1)
        call = parsed.body[0].value
        self.assertIsInstance(call, ast.Call)
        self.assertEqual(call.func.id, 'fill')
        self.assertEqual({k.arg: ast.literal_eval(k.value) for k in call.keywords}, {'bid': '1', 'value': text})

    def test_native_submission_is_sealed_once_and_not_regraded(self):
        answer = 'Synthetic final answer.\n'
        result = self.bridge.call_tool('send_msg_to_user', {'text': answer})
        self.assertTrue(json.loads(result['content'][0]['text'])['submitted'])
        answer_path = self.root / 'attempt/final-answer.txt'
        self.assertEqual(answer_path.read_bytes(), answer.encode())
        receipt = json.loads((self.root / 'attempt/submission.json').read_text())
        self.assertEqual(receipt['answer_sha256'], hashlib.sha256(answer.encode()).hexdigest())
        self.assertIsInstance(receipt['native_submission_monotonic_ns'], int)
        self.assertNotIn('score', receipt)
        with self.assertRaises(ValueError): self.bridge.call_tool('send_msg_to_user', {'text': 'overwrite'})
        with self.assertRaises(ValueError): self.bridge.call_tool('click', {'bid': '1'})
        self.assertEqual(answer_path.read_bytes(), answer.encode())
        self.assertEqual(len(self.backend.calls), 1)

    def test_submission_persists_browser_state_before_mcp_client_can_exit(self):
        self.bridge.call_tool('send_msg_to_user', {'text': 'Synthetic answer'})
        state = self.root / 'attempt/browser-state.json'
        receipt_path = self.root / 'attempt/browser-state-receipt.json'
        self.assertTrue(state.exists(), 'MCP client can exit before close() runs')
        receipt = json.loads(receipt_path.read_text())
        submission = json.loads((self.root / 'attempt/submission.json').read_text())
        self.assertEqual(receipt['sha256'], hashlib.sha256(state.read_bytes()).hexdigest())
        self.assertGreaterEqual(receipt['captured_at_monotonic_ns'], submission['native_submission_monotonic_ns'])
        before = (state.read_bytes(), receipt_path.read_bytes())
        self.backend.snapshot = lambda: (_ for _ in ()).throw(AssertionError('Submission state must not be recaptured'))
        self.bridge.close()
        self.assertEqual(before, (state.read_bytes(), receipt_path.read_bytes()))
        self.assertTrue(self.backend.closed)

    def test_post_submission_observation_failure_keeps_sealed_answer_and_browser_state(self):
        def failed_post_step(name, arguments):
            self.backend.on_submission(arguments['text'])
            raise RuntimeError('Synthetic post-submission observation failure')
        self.backend.step = failed_post_step
        with self.assertRaises(RuntimeError):
            self.bridge.call_tool('send_msg_to_user', {'text': 'Sealed synthetic answer'})
        self.assertEqual((self.root / 'attempt/final-answer.txt').read_text(), 'Sealed synthetic answer')
        self.assertTrue((self.root / 'attempt/browser-state.json').is_file())
        self.assertTrue((self.root / 'attempt/browser-state-receipt.json').is_file())
        self.assertTrue(self.bridge.submitted)

    def test_submission_must_come_from_expected_native_callback(self):
        with self.assertRaises(ValueError): self.backend.on_submission('unsolicited')
        self.assertFalse((self.root / 'attempt/final-answer.txt').exists())
        def bad_step(name, arguments):
            self.backend.on_submission('different answer')
        self.backend.step = bad_step
        with self.assertRaises(ValueError): self.bridge.call_tool('send_msg_to_user', {'text': 'requested'})
        self.assertFalse((self.root / 'attempt/final-answer.txt').exists())

    def test_claimed_directory_cannot_be_reused_and_attempts_are_independent(self):
        with self.assertRaises(ValueError):
            bridge_module.AssistantBenchBridge(FakeBackend(), self.root / 'attempt', str(uuid.uuid4()))
        second = bridge_module.AssistantBenchBridge(FakeBackend(), self.root / 'other', str(uuid.uuid4()))
        self.addCleanup(second.close)
        self.bridge.call_tool('send_msg_to_user', {'text': 'first'})
        second.call_tool('send_msg_to_user', {'text': 'second'})
        self.assertEqual((self.root / 'attempt/final-answer.txt').read_text(), 'first')
        self.assertEqual((self.root / 'other/final-answer.txt').read_text(), 'second')

    def test_concurrent_calls_are_serialized_without_losing_actions(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda i: self.bridge.call_tool('click', {'bid': str(i)}), range(8)))
        self.assertEqual(len(self.backend.calls), 8)
        self.assertEqual(self.backend.peak_active, 1)

    def test_tools_match_native_assistantbench_subset_and_click_options(self):
        expected = {'observe', 'noop', 'scroll', 'fill', 'select_option', 'click',
                    'press', 'go_back', 'goto', 'send_msg_to_user'}
        self.assertEqual({t['name'] for t in self.bridge.list_tools()}, expected)
        self.bridge.call_tool('click', {'bid': '1', 'button': 'middle', 'modifiers': ['Shift']})
        self.assertEqual(self.backend.calls[-1], ('click', {'bid': '1', 'button': 'middle', 'modifiers': ['Shift']}))
        for args in ({'bid': '1', 'button': 'javascript'}, {'bid': '1', 'modifiers': ['__import__']}):
            with self.assertRaises(ValueError): self.bridge.call_tool('click', args)

    def test_close_preserves_private_browser_snapshot_and_restoration_hash(self):
        self.bridge.close()
        path = self.root / 'attempt/browser-state.json'
        self.assertTrue(path.is_file(), 'Browser state must remain after MCP/CLI exit')
        receipt = json.loads((self.root / 'attempt/browser-state-receipt.json').read_text())
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(receipt['sha256'], digest)
        self.assertFalse(receipt['full_session_restore_qualified'])
        snapshot = bridge_module.load_browser_snapshot(path, digest)
        self.assertEqual(snapshot['storage_state']['origins'][0]['localStorage'][0]['value'], 'synthetic-private-state')
        self.assertNotIn('snapshot', {t['name'] for t in self.bridge.list_tools()})
        self.assertTrue(self.backend.closed)
        self.bridge.close()  # Idempotent close must not overwrite the original snapshot.
        self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_native_storage_snapshot_keeps_active_page_when_playwright_visits_origins(self):
        from types import SimpleNamespace
        active = SimpleNamespace(url='https://example.org/')
        temporary = SimpleNamespace(url='https://old-origin.example/')
        backend = bridge_module.NativeBrowserBackend('Synthetic goal')
        history = {id(active): 'synthetic history'}
        env = SimpleNamespace(page=active, page_history=history.copy())
        def storage_state():
            # Playwright visits stored origins in a temporary page, which can
            # trigger BrowserGym focus callbacks before that page is closed.
            env.page = temporary
            env.page_history = {id(temporary): None}
            return {'cookies': [], 'origins': []}
        env.context = SimpleNamespace(pages=[active], storage_state=storage_state)
        backend.env = env
        snapshot = backend.snapshot()
        self.assertEqual(snapshot['pages'], ['https://example.org/'])
        self.assertEqual(snapshot['active_page_index'], 0)
        self.assertIs(env.page, active)
        self.assertEqual(env.page_history, history)

    def test_native_snapshot_restores_focus_even_when_storage_read_fails(self):
        from types import SimpleNamespace
        active = SimpleNamespace(url='https://example.org/')
        temporary = SimpleNamespace(url='https://old-origin.example/')
        backend = bridge_module.NativeBrowserBackend('Synthetic goal')
        history = {id(active): None}
        env = SimpleNamespace(page=active, page_history=history.copy())
        def storage_state():
            env.page = temporary; env.page_history = {id(temporary): None}
            raise RuntimeError('Synthetic storage read failure')
        env.context = SimpleNamespace(pages=[active], storage_state=storage_state)
        backend.env = env
        with self.assertRaises(RuntimeError): backend.snapshot()
        self.assertIs(env.page, active)
        self.assertEqual(env.page_history, history)

    def test_restore_rejects_tampered_state_and_different_task(self):
        self.bridge.close()
        path = self.root / 'attempt/browser-state.json'
        self.assertTrue(path.is_file(), 'Browser snapshot is missing')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaises(ValueError): bridge_module.load_browser_snapshot(path, '0' * 64)
        with self.assertRaises(ValueError):
            bridge_module.NativeBrowserBackend('A different task', restore_snapshot=path, restore_sha256=digest)
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaises(ValueError): bridge_module.load_browser_snapshot(path, digest)

    def test_no_task_work_cap_is_exposed_in_tool_schema(self):
        tools = self.bridge.list_tools()
        self.assertIn('send_msg_to_user', {t['name'] for t in tools})
        self.assertIn('noop', {t['name'] for t in tools})
        for t in tools:
            self.assertFalse(t['inputSchema']['additionalProperties'])
        self.assertNotIn('max_turn', json.dumps(tools))
        self.assertNotIn('deadline', json.dumps(tools))

    def test_mcp_handshake_tool_listing_and_calls_are_standard_json_rpc(self):
        calls = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2024-11-05', 'capabilities': {}, 'clientInfo': {'name': 'synthetic', 'version': '1'}}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'click', 'arguments': {'bid': '1'}}},
            {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'eval', 'arguments': {'code': 'x'}}},
        ]
        output = io.StringIO()
        bridge_module.serve_stdio(self.bridge, io.StringIO('\n'.join(json.dumps(x) for x in calls) + '\n'), output)
        messages = [json.loads(x) for x in output.getvalue().splitlines()]
        self.assertEqual([x['id'] for x in messages], [1, 2, 3, 4])
        self.assertEqual(messages[0]['result']['protocolVersion'], '2024-11-05')
        self.assertIn('tools', messages[1]['result'])
        self.assertFalse(messages[2]['result'].get('isError', False))
        self.assertTrue(messages[3]['result']['isError'])
        self.assertNotIn('GOLD_SECRET', output.getvalue())

    def test_mcp_errors_never_expose_exception_details_or_host_paths(self):
        def broken(*_): raise RuntimeError('/private/controller/GOLD_SECRET')
        self.backend.step = broken
        messages = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2024-11-05'}},
                    {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'click', 'arguments': {'bid': '1'}}}]
        output = io.StringIO()
        bridge_module.serve_stdio(self.bridge, io.StringIO('\n'.join(map(json.dumps, messages))), output)
        self.assertNotIn('GOLD_SECRET', output.getvalue())
        self.assertNotIn('/private', output.getvalue())
        self.assertTrue(json.loads(output.getvalue().splitlines()[-1])['result']['isError'])


class SyntheticCanaryTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(bridge_module.__file__).resolve().parents[2] / 'qualification/assistantbench-pilot/claude_canary.py'
        self.assertTrue(self.path.is_file(), 'The prepare-only synthetic Claude-MCP helper is missing')
        spec = importlib.util.spec_from_file_location('assistantbench_synthetic_canary', self.path)
        self.canary = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.canary)
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'canary'
        self.canary.prepare(self.root)

    def test_canary_input_is_one_complete_stream_json_line(self):
        data = (self.root / 'input.json').read_bytes()
        self.assertTrue(data.endswith(b'\n'))
        self.assertEqual(len(data.splitlines()), 1)
        self.assertEqual(json.loads(data)['type'], 'user')

    def test_prepare_uses_fixed_synthetic_input_and_never_stores_auth(self):
        token = 'SYNTHETIC_TOKEN_NEVER_PERSIST_THIS'
        command, environment = self.canary.configuration(self.root, Path('/synthetic/claude'), token)
        self.assertEqual(environment['CLAUDE_CODE_OAUTH_TOKEN'], token)
        self.assertEqual(command[command.index('--tools') + 1], '')
        self.assertEqual(command[command.index('--effort') + 1], 'max')
        self.assertEqual(command[command.index('--permission-mode') + 1], 'dontAsk')
        self.assertNotIn('--bare', command)
        self.assertNotIn('--max-turns', command)
        self.assertNotIn('--fallback-model', command)
        allowed = command[command.index('--allowedTools') + 1].split(',')
        self.assertEqual(set(allowed), {'mcp__assistantbench__' + name for name in ('observe', *bridge_module.NATIVE_ACTIONS)})
        for path in self.root.rglob('*'):
            if path.is_file(): self.assertNotIn(token.encode(), path.read_bytes())
        mcp = json.loads((self.root / 'mcp.json').read_text())['mcpServers']['assistantbench']
        self.assertEqual(mcp['command'], '/usr/bin/env')
        self.assertEqual(mcp['args'][0], '-i')
        self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN', json.dumps(mcp))
        self.assertEqual(json.loads((self.root / 'input.json').read_text())['message']['content'][0]['text'], self.canary.PROMPT)

    def test_prepare_refuses_attempt_reuse_and_manifest_tampering(self):
        with self.assertRaises(FileExistsError): self.canary.prepare(self.root)
        (self.root / 'input.json').write_text('{}')
        with self.assertRaises(ValueError):
            self.canary.configuration(self.root, Path('/synthetic/claude'), 'SYNTHETIC_TOKEN_NEVER_PERSIST_THIS')

    def synthetic_capture(self):
        contract = json.loads((self.root / 'contract.json').read_text())
        session = contract['session_id']; tools = list(self.canary.TOOLS)
        rows = [{'type': 'system', 'subtype': 'init', 'model': self.canary.MODEL,
            'claude_code_version': '2.1.280', 'tools': tools, 'session_id': session,
            'skills': [], 'plugins': [], 'mcp_servers': [{'name': 'assistantbench', 'status': 'connected'}]},
            {'type': 'assistant', 'session_id': session, 'message': {'type': 'message', 'id': 'synthetic-response', 'model': self.canary.MODEL, 'content': [
                {'type': 'tool_use', 'name': 'mcp__assistantbench__' + name}
                for name in ('observe', 'fill', 'click', 'send_msg_to_user')]}},
            {'type': 'rate_limit_event', 'rate_limit_info': {'status': 'allowed', 'isUsingOverage': False, 'overageStatus': 'rejected'}},
            {'type': 'result', 'session_id': session, 'subtype': 'success', 'is_error': False}]
        (self.root / 'capture/stream.jsonl').write_text('\n'.join(map(json.dumps, rows)) + '\n')
        raw = self.root / 'capture/raw-bodies'; raw.mkdir()
        (raw / 'request.request.json').write_text(json.dumps({'model': self.canary.MODEL,
            'output_config': {'effort': 'max'}, 'tools': [{'name': name} for name in tools],
            'messages': [json.loads((self.root / 'input.json').read_text())['message']]}))
        (raw / 'request.response.json').write_text(json.dumps({'type': 'message', 'id': 'synthetic-response', 'model': self.canary.MODEL}))
        (raw / 'index.jsonl').write_text(json.dumps({'request_file': 'request.request.json',
            'response_file': 'request.response.json', 'message_id': 'synthetic-response',
            'session_id': session, 'model': self.canary.MODEL, 'request_id': 'synthetic-request'}))
        store = self.root / 'config/projects/synthetic'; store.mkdir(parents=True)
        (store / (session + '.jsonl')).write_text('{}')
        browser = self.root / 'browser-state'; browser.mkdir()
        (browser / 'final-answer.txt').write_text('Synthetic answer: blue.')
        snapshot = {'storage_state': {'origins': [{'origin': 'http://127.0.0.1:8765',
            'localStorage': [{'name': 'bridge-probe', 'value': 'blue'}]}]}}
        (browser / 'browser-state.json').write_text(json.dumps(snapshot))
        (browser / 'browser-state-receipt.json').write_text(json.dumps({'sha256': self.canary.sha(browser / 'browser-state.json')}))
        return raw, browser

    def test_canary_requires_actual_browser_change_and_verified_transport_response(self):
        raw, browser = self.synthetic_capture()
        self.assertEqual(self.canary.inspect_capture(self.root)['issues'], [])
        (raw / 'request.response.json').write_text(json.dumps({'model': 'wrong-model'}))
        (browser / 'browser-state.json').write_text('{}')
        issues = self.canary.inspect_capture(self.root)['issues']
        self.assertIn('transport_response_mismatch', issues)
        self.assertIn('browser_state_integrity_or_action_unverified', issues)

    def test_observed_dynamic_mcp_source_is_accepted_without_relaxing_identity(self):
        self.synthetic_capture()
        stream = self.root / 'capture/stream.jsonl'
        rows = [json.loads(line) for line in stream.read_text().splitlines()]
        rows[0]['mcp_servers'][0]['source'] = 'dynamic'
        stream.write_text('\n'.join(map(json.dumps, rows)) + '\n')
        self.assertEqual(self.canary.inspect_capture(self.root)['issues'], [])
        for servers in ([{'name': 'assistantbench', 'status': 'connected', 'source': 'project'}],
                        [{'name': 'other', 'status': 'connected', 'source': 'dynamic'}],
                        [{'name': 'assistantbench', 'status': 'failed', 'source': 'dynamic'}],
                        [{'name': 'assistantbench', 'status': 'connected', 'source': 'dynamic'}] * 2,
                        [{'name': 'assistantbench', 'status': 'connected', 'source': 'dynamic', 'extra': True}]):
            with self.subTest(servers=servers):
                rows[0]['mcp_servers'] = servers
                stream.write_text('\n'.join(map(json.dumps, rows)) + '\n')
                self.assertIn('mcp_startup_unverified', self.canary.inspect_capture(self.root)['issues'])

    def test_native_subreaper_source_is_part_of_frozen_canary_contract(self):
        self.assertIn('native_session.py', self.canary.source_pins())

    def test_missing_owned_census_or_echild_never_becomes_drain_proof(self):
        self.assertTrue(hasattr(self.canary, 'CanaryBoundary'), 'A strict owned-process boundary is required')
        contract, _, _ = self.canary.verify(self.root)
        boundary = self.canary.CanaryBoundary(self.root, contract, 300)
        boundary.root_exit(0, 100)
        boundary.drain(110, census_empty=False, waitpid_echild=True)
        boundary.drain(120, census_empty=True, waitpid_echild=False)
        self.assertIsNone(boundary.metadata()['owned_work_drained_monotonic_ns'])
        self.assertFalse((self.root / 'capture/process-drain.json').exists())
        boundary.drain(130, census_empty=True, waitpid_echild=True)
        proof = json.loads((self.root / 'capture/process-drain.json').read_text())
        self.assertTrue(proof['census_empty']); self.assertTrue(proof['waitpid_echild'])
        self.assertEqual(proof['owned_work_drained_monotonic_ns'], 130)
        self.assertFalse(boundary.metadata()['timing_valid'])

    def test_canary_retains_validated_input_bytes_before_delivery(self):
        verified = self.canary.verify(self.root)
        self.assertIsInstance(verified, tuple, 'Verification must freeze the exact input bytes')
        contract, input_bytes, contract_sha = verified
        expected = (self.root / 'input.json').read_bytes()
        self.assertEqual(input_bytes, expected)
        (self.root / 'input.json').write_text('different payload after validation')
        self.assertEqual(input_bytes, expected)
        self.assertEqual(contract_sha, self.canary.sha(self.root / 'contract.json'))
        self.assertEqual(contract['model'], self.canary.MODEL + '[1m]')

    def test_empty_optional_otel_response_uses_only_matching_complete_native_stream(self):
        raw, _ = self.synthetic_capture()
        (raw / 'request.response.json').write_bytes(b'')
        result = self.canary.inspect_capture(self.root)
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['native_stream_response_ids'], ['synthetic-response'])
        stream = self.root / 'capture/stream.jsonl'
        stream.write_bytes(stream.read_bytes().rstrip(b'\n'))
        self.assertIn('transport_response_mismatch', self.canary.inspect_capture(self.root)['issues'])


@unittest.skipUnless(sys.platform == 'linux', 'Linux child-subreaper qualification requires an isolated Linux process')
class CanaryLinuxDrainTests(unittest.TestCase):
    def fixture(self, mode):
        path = Path(bridge_module.__file__).resolve().parents[2] / 'qualification/assistantbench-pilot/claude_canary.py'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'canary'
            child = r'''
import json, os, sys, time
from pathlib import Path
session, tools, marker, mode = sys.argv[1:]
sys.stdin.buffer.readline()
print(json.dumps({'type':'system','subtype':'init','session_id':session,'model':'claude-opus-5-5[1m]',
 'claude_code_version':'2.1.280','tools':json.loads(tools),'skills':[],'plugins':[],
 'mcp_servers':[{'name':'assistantbench','status':'connected','source':'dynamic'}]}),flush=True)
pid = os.fork()
if pid == 0:
 os.setsid()
 grandchild = os.fork()
 if grandchild == 0:
  if mode == 'watchdog':
   time.sleep(60)
  else:
   time.sleep(.3)
   Path(marker).write_text('owned grandchild finished')
  os._exit(0)
 os._exit(0)
if mode == 'watchdog':
 time.sleep(60)
else:
 print(json.dumps({'type':'result','subtype':'success','is_error':False,'session_id':session,'result':'synthetic native final'}),flush=True)
os._exit(0)
'''
            driver = r'''
import importlib.util, json, os, sys
from pathlib import Path
path, root, child, mode = sys.argv[1:]
spec=importlib.util.spec_from_file_location('synthetic_canary',path)
canary=importlib.util.module_from_spec(spec);spec.loader.exec_module(canary)
if not callable(getattr(canary,'supervise_process',None)):
 print(json.dumps({'implemented':False}));raise SystemExit(0)
root=Path(root);canary.prepare(root);contract,payload,_=canary.verify(root)
marker=root/'owned-finished.txt'
result=canary.supervise_process(root,contract,[sys.executable,'-c',child,contract['session_id'],json.dumps(canary.TOOLS),str(marker),mode],
 {'PATH':'/usr/local/bin:/usr/bin:/bin'},payload,.2 if mode=='watchdog' else 10)
events=[json.loads(line) for line in (root/'capture/events.jsonl').read_text().splitlines()]
proof=json.loads((root/'capture/process-drain.json').read_text()) if (root/'capture/process-drain.json').exists() else None
print(json.dumps({'implemented':True,'result':result,'grandchild_finished':marker.exists(),'proof':proof,
 'reaped':len([e for e in events if e['kind']=='owned_process_reaped'])}))
'''
            done = subprocess.run([sys.executable, '-c', driver, str(path), str(root), child, mode],
                                  capture_output=True, text=True, env=os.environ.copy(), timeout=20)
            self.assertEqual(done.returncode, 0, done.stderr)
            return json.loads(done.stdout)

    def test_setsid_orphan_grandchild_is_reaped_before_drain(self):
        result = self.fixture('drain')
        self.assertTrue(result['implemented'], 'Use the qualified owned-process supervisor')
        self.assertTrue(result['grandchild_finished'])
        self.assertGreaterEqual(result['reaped'], 3)
        self.assertTrue(result['proof']['census_empty']); self.assertTrue(result['proof']['waitpid_echild'])
        execution = result['result']['execution']
        self.assertGreater(execution['owned_work_drained_monotonic_ns'], execution['root_exit_monotonic_ns'])
        self.assertTrue(result['result']['owned_processes_drained'])
        self.assertFalse(result['result']['synthetic_watchdog_expired'])

    def test_synthetic_watchdog_stops_and_reaps_owned_work_without_a_success_claim(self):
        result = self.fixture('watchdog')
        self.assertTrue(result['implemented'], 'Use the qualified owned-process supervisor')
        self.assertTrue(result['result']['synthetic_watchdog_expired'])
        self.assertTrue(result['result']['owned_processes_drained'])
        self.assertFalse(result['result']['execution']['timing_valid'])
        self.assertFalse(result['grandchild_finished'])


if __name__ == '__main__':
    unittest.main()
