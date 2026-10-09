"""Exercise real BrowserGym on an answer-free local page; never a study task."""

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import sys
import threading
import uuid

from agenttime.assistantbench_bridge import AssistantBenchBridge, NativeBrowserBackend
import agenttime.assistantbench_bridge as implementation


def public(result):
    return json.loads(next(item['text'] for item in result['content'] if item['type'] == 'text'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    html = Path(__file__).with_name('synthetic.html').read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/synthetic.html':
                self.send_error(404); return
            self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(html))); self.end_headers(); self.wfile.write(html)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    url = f'http://127.0.0.1:{server.server_port}/synthetic.html'
    checks = []
    try:
        goal = 'Synthetic development task: use the local page and submit a short answer.'
        backend = NativeBrowserBackend(goal,
                                       qualification_start_url=url)
        first = AssistantBenchBridge(backend, args.output / 'first', str(uuid.uuid4()))
        try:
            initial = public(first.call_tool('observe', {}))
            (args.output / 'initial-accessibility.txt').write_text(initial['accessibility'])
            assert 'Fresh browser state' in initial['accessibility']
            assert backend.env.task.locale == 'en-US'
            assert backend.env.task.timezone_id == 'America/New_York'
            assert backend.env.task.viewport == {'width': 1280, 'height': 720}
            assert backend.env.task.slow_mo == 1000
            assert backend.env.task.timeout == 5000
            from browsergym.core.action.highlevel import HighLevelActionSet
            from agenttime.assistantbench_bridge import NATIVE_ACTIONS
            assert tuple(HighLevelActionSet(subsets=['assistantbench']).action_set) == NATIVE_ACTIONS
            assert {t['name'] for t in first.list_tools()} == set(NATIVE_ACTIONS) | {'observe'}
            assert 'report_infeasible' not in NATIVE_ACTIONS
            checks.append('exact_native_assistantbench_action_contract')
            checks.append('native_profile_and_public_observation')
            bid = re.search(r"\[([^\]]+)\] textbox 'Synthetic value'", initial['accessibility']).group(1)
            first.call_tool('fill', {'bid': bid, 'value': 'blue'})
            observation = public(first.call_tool('observe', {}))
            button = re.search(r"\[([^\]]+)\] button 'Store value'", observation['accessibility']).group(1)
            changed = public(first.call_tool('click', {'bid': button}))
            assert 'Observed: blue' in changed['accessibility']
            assert backend.env.page.evaluate("localStorage.getItem('bridge-probe')") == 'blue'
            backend.env.context.add_cookies([{'name': 'synthetic-cookie', 'value': 'blue', 'url': url}])
            checks.append('native_fill_click_and_observation')
            original_post_step = backend.env.post_step
            def require_seal_before_post_step(*positional, **keywords):
                assert (args.output / 'first/final-answer.txt').is_file(), 'Native send_msg_to_user returned before the submission was sealed'
                return original_post_step(*positional, **keywords)
            backend.env.post_step = require_seal_before_post_step
            submitted = public(first.call_tool('send_msg_to_user', {'text': 'Synthetic answer: blue.'}))
            assert submitted == {'submitted': True}
            assert (args.output / 'first/final-answer.txt').read_text() == 'Synthetic answer: blue.'
            receipt = json.loads((args.output / 'first/submission.json').read_text())
            assert receipt['endpoint'] == 'send_msg_to_user'
            assert receipt['answer_sha256'] == hashlib.sha256(b'Synthetic answer: blue.').hexdigest()
            checks.append('native_submission_callback_and_immutable_seal')
            checks.append('submission_sealed_before_post_step_observation')
            browser_version = backend.env.browser.version
        finally:
            first.close()

        second = AssistantBenchBridge(NativeBrowserBackend('Independent synthetic development task.',
            qualification_start_url=url), args.output / 'second', str(uuid.uuid4()))
        try:
            observation = public(second.call_tool('observe', {}))
            assert 'Fresh browser state' in observation['accessibility']
            assert 'Prior browser state found' not in observation['accessibility']
            checks.append('independent_browser_storage')
        finally:
            second.close()
        snapshot_path = args.output / 'first/browser-state.json'
        snapshot_hash = hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
        restored_backend = NativeBrowserBackend(goal, qualification_start_url=url,
            restore_snapshot=snapshot_path, restore_sha256=snapshot_hash)
        restored = AssistantBenchBridge(restored_backend, args.output / 'rehydrated', str(uuid.uuid4()))
        try:
            observation = public(restored.call_tool('observe', {}))
            assert 'Prior browser state found' in observation['accessibility']
            assert restored_backend.env.page.evaluate("localStorage.getItem('bridge-probe')") == 'blue'
            assert any(c['name'] == 'synthetic-cookie' and c['value'] == 'blue' for c in restored_backend.env.context.cookies())
            assert restored_backend.env.page.url == url
            assert not restored.submitted
            checks.append('private_state_persists_after_browser_close')
            checks.append('fresh_context_cookie_and_localstorage_rehydration')
            checks.append('rehydration_is_a_new_attempt_without_reused_submission')
        finally:
            restored.close()
        report = {'schema_version': 1, 'qualification': 'real_browser_synthetic_page_only',
            'checks_passed': checks, 'model_calls': 0, 'selected_task_inputs_used': 0,
            'platform': platform.platform(), 'python': sys.version.split()[0], 'browser': browser_version,
            'packages': {name: importlib.metadata.version(name) for name in ['browsergym-core', 'playwright', 'gymnasium', 'numpy', 'pillow']},
            'source_sha256': hashlib.sha256(Path(implementation.__file__).read_bytes()).hexdigest(),
            'study_ready': False, 'claude_tool_path_qualified': False, 'linux_worker_qualified': False, 'linux_dependency_check': platform.system() == 'Linux',
            'partial_browser_state_rehydration': True,
            'archive_restore_qualified': False,
            'remaining': ['Claude MCP tool path on disposable native session', 'intended Linux worker/resources and public web access',
                          'browser/session capture, restoration and recovery', 'private native scorer qualification']}
        (args.output / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))
    finally:
        server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    main()
