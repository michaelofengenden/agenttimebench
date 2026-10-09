"""Answer-free AssistantBench browser adapter and a serial MCP stdio surface.

The controller supplies only the native task text. This process never imports
AssistantBench's dataset-loading task module or any scorer. BrowserGym's public
task setup and completion boundary are preserved with grading deferred. A fresh
private controller directory is required for every attempt; it is not a subject
mount. A local browser test does not establish Linux study or restore readiness.
"""

import argparse
import base64
import copy
import hashlib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid

from .jsonio import loads_strict


BROWSERGYM_VERSION = '0.14.3'
_VERSIONS = ('2024-11-05', '2025-03-26', '2025-06-18')
# BrowserGym 0.14.3 ACTION_SUBSETS["assistantbench"], plus mandatory noop.
# Observation is a transport operation, not an additional browser capability.
NATIVE_ACTIONS = ('noop', 'scroll', 'fill', 'select_option', 'click', 'press',
                  'go_back', 'goto', 'send_msg_to_user')
_PARAMETERS = {
    'observe': {}, 'noop': {'wait_ms?': 'number'},
    'scroll': {'delta_x': 'number', 'delta_y': 'number'},
    'fill': {'bid': 'string', 'value': 'string', 'enable_autocomplete_menu?': 'boolean'},
    'select_option': {'bid': 'string', 'options': 'strings'},
    'click': {'bid': 'string', 'button?': 'button', 'modifiers?': 'modifiers'},
    'press': {'bid': 'string', 'key_comb': 'string'},
    'go_back': {}, 'goto': {'url': 'url'}, 'send_msg_to_user': {'text': 'string'},
}
_BUTTONS = ('left', 'middle', 'right')
_MODIFIERS = ('Alt', 'Control', 'ControlOrMeta', 'Meta', 'Shift')
_PROFILE = {'locale': 'en-US', 'timezone_id': 'America/New_York',
            'viewport': {'width': 1280, 'height': 720}}



def _schema(name):
    descriptions = {'string': {'type': 'string'}, 'url': {'type': 'string'},
        'boolean': {'type': 'boolean'}, 'number': {'type': 'number'},
        'button': {'type': 'string', 'enum': list(_BUTTONS)},
        'modifiers': {'type': 'array', 'items': {'type': 'string', 'enum': list(_MODIFIERS)}},
        'strings': {'oneOf': [{'type': 'string'}, {'type': 'array', 'items': {'type': 'string'}}]}}
    parameters = _PARAMETERS[name]
    return {'type': 'object', 'properties': {k.rstrip('?'): copy.deepcopy(descriptions[v]) for k, v in parameters.items()},
            'required': [k for k in parameters if not k.endswith('?')], 'additionalProperties': False}


def _validate_action(name, arguments):
    if type(name) is not str or name not in _PARAMETERS or type(arguments) is not dict:
        raise ValueError('Unknown browser action or invalid arguments')
    parameters = _PARAMETERS[name]
    allowed = {key.rstrip('?') for key in parameters}
    required = {key for key in parameters if not key.endswith('?')}
    if not required.issubset(arguments) or set(arguments) - allowed:
        raise ValueError('Browser action has missing or unsupported arguments')
    for key, kind in parameters.items():
        key = key.rstrip('?')
        if key not in arguments:
            continue
        value = arguments[key]
        good = ((kind in ('string', 'url') and type(value) is str and '\x00' not in value)
                or (kind == 'boolean' and type(value) is bool)
                or (kind == 'number' and type(value) in (int, float) and math.isfinite(value))
                or (kind == 'button' and type(value) is str and value in _BUTTONS)
                or (kind == 'modifiers' and type(value) is list and all(type(x) is str and x in _MODIFIERS for x in value))
                or (kind == 'strings' and (type(value) is str or
                    (type(value) is list and all(type(x) is str for x in value)))))
        if not good:
            raise ValueError('Browser action argument has an invalid type or value')
        if kind == 'url':
            try:
                parsed = urlsplit(value)
                if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None or parsed.password is not None:
                    raise ValueError('Browser navigation requires a public HTTP(S) URL')
            except ValueError as error:
                raise ValueError('Browser navigation requires an HTTP(S) URL without embedded credentials') from error
    if name == 'noop' and arguments.get('wait_ms', 0) < 0:
        raise ValueError('Browser wait cannot be negative')


def render_native_action(name, arguments):
    """Generate one allowlisted call; all supplied values become literals."""
    _validate_action(name, arguments)
    if name == 'observe':
        raise ValueError('Observation does not execute a browser action')
    return name + '(' + ', '.join(f'{key}={value!r}' for key, value in arguments.items()) + ')'


def _write_once(path, data):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, 'wb', closefd=False) as destination:
            destination.write(data); destination.flush(); os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False) + '\n').encode()


def _validate_snapshot(value):
    keys = {'schema_version', 'browsergym_version', 'goal_sha256', 'storage_state',
            'pages', 'active_page_index', 'profile'}
    if (type(value) is not dict or set(value) != keys
            or value['schema_version'] != 'agenttime.browsergym-state.v1'
            or value['browsergym_version'] != BROWSERGYM_VERSION
            or value['profile'] != _PROFILE
            or type(value['goal_sha256']) is not str
            or not re.fullmatch('[0-9a-f]{64}', value['goal_sha256'])
            or type(value['storage_state']) is not dict
            or set(value['storage_state']) != {'cookies', 'origins'}
            or type(value['storage_state']['cookies']) is not list
            or type(value['storage_state']['origins']) is not list
            or type(value['pages']) is not list or not value['pages']
            or type(value['active_page_index']) is not int
            or not 0 <= value['active_page_index'] < len(value['pages'])):
        raise ValueError('Invalid private browser snapshot')
    for url in value['pages']:
        if url != 'about:blank':
            _validate_action('goto', {'url': url})
    return value


def load_browser_snapshot(path, expected_sha256):
    """Read controller-held state using its independently retained file digest.

    This rehydrates cookies, localStorage and URLs only. It is not a process,
    JavaScript heap, history, sessionStorage, form-state or download checkpoint.
    """
    if type(expected_sha256) is not str or not re.fullmatch('[0-9a-f]{64}', expected_sha256):
        raise ValueError('An independently retained snapshot SHA-256 is required')
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('A regular private snapshot file is required')
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError('Browser snapshot digest mismatch')
    return _validate_snapshot(loads_strict(data))


class AssistantBenchBridge:
    """One fresh native browser, serial actions and a private immutable answer."""

    def __init__(self, backend, state_dir, attempt_id):
        if type(attempt_id) is not str or str(uuid.UUID(attempt_id)) != attempt_id:
            raise ValueError('A canonical fresh attempt UUID is required')
        self.backend = backend
        self.state_dir = Path(state_dir)
        self.attempt_id = attempt_id
        self._lock = threading.RLock()
        self._submitted = False
        self._snapshot_persisted = False
        self._closed = False
        self._executing = None
        self._failed = False
        try:
            self.state_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
        except OSError as error:
            raise ValueError('Attempt directory must be fresh under an existing private parent') from error
        _write_once(self.state_dir / 'identity.json', _encoded({'schema_version': 1, 'attempt_id': attempt_id,
            'pid': os.getpid(), 'browsergym_version': BROWSERGYM_VERSION, 'grader_in_process': False,
            'restore_qualified': False, 'study_qualified': False}))
        try:
            self._observation = backend.reset(self._seal_submission)
        except Exception:
            self._failed = True
            backend.close()
            raise

    @property
    def submitted(self):
        return self._submitted

    def _seal_submission(self, text):
        if (self._submitted or self._executing is None or self._executing[0] != 'send_msg_to_user'
                or type(text) is not str or text != self._executing[1].get('text')):
            raise ValueError('Submission must match the active native submission action exactly once')
        data = text.encode('utf-8')
        receipt = {'schema_version': 1, 'attempt_id': self.attempt_id,
                   'native_submission_monotonic_ns': time.monotonic_ns(),
                   'answer_sha256': hashlib.sha256(data).hexdigest(), 'answer_bytes': len(data),
                   'endpoint': 'send_msg_to_user', 'grade_status': 'not_run'}
        _write_once(self.state_dir / 'final-answer.txt', data)
        _write_once(self.state_dir / 'submission.json', _encoded(receipt))
        self._submitted = True
        # Claude may terminate its MCP child without running Python finally.
        # Seal the private browser state while the native submission callback
        # still owns the live context, before its tool reply can be delivered.
        self._persist_snapshot()

    def list_tools(self):
        return [{'name': name,
                 'description': ('Observe the latest native browser screenshot and accessibility tree.' if name == 'observe'
                     else 'Submit the final answer and finish this task. This action is final.' if name == 'send_msg_to_user'
                     else f'Perform the native BrowserGym {name} action.'),
                 'inputSchema': _schema(name)} for name in _PARAMETERS]

    def _content(self):
        observation = self._observation
        text = observation.get('accessibility')
        png = observation.get('screenshot_png')
        urls, titles = observation.get('open_pages_urls'), observation.get('open_pages_titles')
        active = observation.get('active_page_index')
        if (type(text) is not str or type(png) is not bytes or not png.startswith(b'\x89PNG\r\n\x1a\n')
                or type(urls) not in (list, tuple) or type(titles) not in (list, tuple)
                or len(urls) != len(titles) or not all(type(s) is str for s in (*urls, *titles))
                or type(active) is not int or active < 0 or active >= len(urls)):
            raise ValueError('Native browser observation is malformed')
        public = {'accessibility': text,
                  'tabs': [{'url': url, 'title': title} for url, title in zip(urls, titles)],
                  'active_tab': active, 'last_action_failed': bool(observation.get('last_action_error'))}
        return {'content': [{'type': 'text', 'text': json.dumps(public, ensure_ascii=False)},
                            {'type': 'image', 'mimeType': 'image/png', 'data': base64.b64encode(png).decode('ascii')}]}

    def call_tool(self, name, arguments):
        _validate_action(name, arguments)
        with self._lock:
            if self._closed or self._failed:
                raise ValueError('Browser session is closed or needs controller attention')
            if self._submitted:
                if name == 'observe':
                    return {'content': [{'type': 'text', 'text': '{"submitted":true}'}]}
                raise ValueError('The native final answer has already been submitted')
            if name == 'observe':
                return self._content()
            self._executing = (name, copy.deepcopy(arguments))
            try:
                observation, _reward, terminated, truncated, _info = self.backend.step(name, arguments)
                if truncated or (terminated and not self._submitted):
                    raise ValueError('Unexpected native termination requires controller attention')
                if name == 'send_msg_to_user' and (not self._submitted or not terminated):
                    raise ValueError('The native submission endpoint did not confirm termination')
                self._observation = observation
            except Exception:
                self._failed = True
                raise
            finally:
                self._executing = None
            if self._submitted:
                return {'content': [{'type': 'text', 'text': '{"submitted":true}'}]}
            return self._content()

    def _persist_snapshot(self):
        if self._snapshot_persisted:
            return
        data = _encoded(_validate_snapshot(self.backend.snapshot()))
        _write_once(self.state_dir / 'browser-state.json', data)
        _write_once(self.state_dir / 'browser-state-receipt.json', _encoded({
            'schema_version': 1, 'attempt_id': self.attempt_id,
            'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
            'captured_at_monotonic_ns': time.monotonic_ns(),
            'persisted': ['cookies', 'localStorage', 'open_page_urls', 'active_page_index'],
            'full_session_restore_qualified': False}))
        self._snapshot_persisted = True

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                try:
                    self._persist_snapshot()
                finally:
                    self.backend.close()


class NativeBrowserBackend:
    """Pinned BrowserGym core with an answer-free copy of native task behavior.

    Public task behavior follows cached AssistantBench task.py: en-US locale,
    America/New_York timezone, Google start page and termination when the last
    chat message is from the assistant. Native per-action/setup timeouts remain;
    no experiment duration, turn or token cap is added.
    """

    def __init__(self, goal, seed=0, qualification_start_url=None, *, restore_snapshot=None, restore_sha256=None):
        if type(goal) is not str or not goal.strip() or type(seed) is not int:
            raise ValueError('Native task text and an integer seed are required')
        self.goal = goal; self.seed = seed; self.env = None; self._started = False
        self._thread = threading.get_ident()
        self._restore = None
        if (restore_snapshot is None) != (restore_sha256 is None):
            raise ValueError('Restoration requires a snapshot and independent SHA-256')
        if restore_snapshot is not None:
            self._restore = load_browser_snapshot(restore_snapshot, restore_sha256)
            if self._restore['goal_sha256'] != hashlib.sha256(goal.encode()).hexdigest():
                raise ValueError('Browser snapshot belongs to a different task')
        self.start_url = 'https://google.com'
        self.qualification_only = qualification_start_url is not None
        if qualification_start_url is not None:
            parsed = urlsplit(qualification_start_url)
            if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.username or parsed.password:
                raise ValueError('Synthetic qualification may only start at a loopback HTTP page')
            self.start_url = qualification_start_url

    def _same_thread(self):
        if threading.get_ident() != self._thread:
            raise ValueError('Native BrowserGym must remain on its owning stdio thread')

    def reset(self, on_submission):
        self._same_thread()
        if self._started:
            raise ValueError('A native backend cannot be reused across attempts')
        self._started = True
        if importlib.metadata.version('browsergym-core') != BROWSERGYM_VERSION:
            raise ValueError('Unexpected BrowserGym core version')
        from browsergym.core.env import BrowserEnv
        from browsergym.core.task import AbstractBrowserTask
        from browsergym.core.action.highlevel import HighLevelActionSet

        goal, start_url = self.goal, self.start_url

        class AnswerFreeTask(AbstractBrowserTask):
            def __init__(self, seed):
                super().__init__(seed)
                self.locale = 'en-US'; self.timezone_id = 'America/New_York'

            def setup(self, page):
                page.goto(start_url, timeout=10000)
                return goal, {}

            def validate(self, page, chat_messages):
                done = bool(chat_messages and chat_messages[-1]['role'] == 'assistant')
                return 0.0, done, '', {}

        action_set = HighLevelActionSet(subsets=['assistantbench'], strict=True,
                                       multiaction=False, demo_mode='off')
        if tuple(action_set.action_set) != NATIVE_ACTIONS:
            raise ValueError('Installed native AssistantBench action set does not match the pin')
        context_options = {'storage_state': self._restore['storage_state']} if self._restore else {}
        self.env = BrowserEnv(AnswerFreeTask, action_mapping=action_set.to_python_code,
                              pw_context_kwargs=context_options)
        native_pre_step = self.env.pre_step

        def capture_pre_step():
            info, native_send, native_infeasible = native_pre_step()

            def capture_native_send(text):
                native_send(text)
                on_submission(text)

            return info, capture_native_send, native_infeasible

        self.env.pre_step = capture_pre_step
        observation, _info = self.env.reset(seed=self.seed)
        if self._restore is not None:
            pages = [self.env.page]
            for _ in self._restore['pages'][1:]:
                pages.append(self.env.context.new_page())
            for page, url in zip(pages, self._restore['pages']):
                page.goto(url, timeout=10000)
            self.env._activate_page_from_js(pages[self._restore['active_page_index']])
            observation = self.env._get_obs()
        return self._public_observation(observation)

    @staticmethod
    def _public_observation(observation):
        from PIL import Image
        from browsergym.utils.obs import flatten_axtree_to_str
        output = io.BytesIO(); Image.fromarray(observation['screenshot']).save(output, format='PNG')
        return {'accessibility': flatten_axtree_to_str(observation['axtree_object']),
                'screenshot_png': output.getvalue(),
                'open_pages_urls': list(observation['open_pages_urls']),
                'open_pages_titles': list(observation['open_pages_titles']),
                'active_page_index': int(observation['active_page_index'].item()),
                'last_action_error': bool(observation['last_action_error'])}

    def step(self, name, arguments):
        self._same_thread()
        if self.env is None:
            raise ValueError('Browser environment is not initialized')
        observation, reward, terminated, truncated, info = self.env.step(render_native_action(name, arguments))
        return self._public_observation(observation), reward, terminated, truncated, info

    def snapshot(self):
        """Capture a private, explicitly partial browser checkpoint before close."""
        self._same_thread()
        if self.env is None:
            raise ValueError('Browser environment is not initialized')
        pages = list(self.env.context.pages)
        active_page = self.env.page
        active_index = pages.index(active_page)
        page_history = self.env.page_history.copy()
        urls = [page.url for page in pages]
        try:
            # Playwright visits stored origins in a temporary page. Its focus
            # callbacks can replace BrowserGym's active page with that already
            # closed page. Preserve the subject's original pages and focus.
            storage = self.env.context.storage_state()
        finally:
            self.env.page = active_page
            self.env.page_history = page_history
        if list(self.env.context.pages) != pages:
            raise ValueError('Browser pages changed during private snapshot')
        return _validate_snapshot({'schema_version': 'agenttime.browsergym-state.v1',
            'browsergym_version': BROWSERGYM_VERSION,
            'goal_sha256': hashlib.sha256(self.goal.encode()).hexdigest(),
            'storage_state': storage,
            'pages': urls,
            'active_page_index': active_index,
            'profile': copy.deepcopy(_PROFILE)})

    def close(self):
        self._same_thread()
        if self.env is not None:
            self.env.close(); self.env = None


def serve_stdio(bridge, input_stream=None, output_stream=None):
    """Serve newline-delimited MCP JSON-RPC, one action at a time, no HTTP API."""
    input_stream = input_stream if input_stream is not None else sys.stdin
    output_stream = output_stream if output_stream is not None else sys.stdout
    initialized = False
    for line in input_stream:
        identifier = None
        try:
            request = loads_strict(line)
            if type(request) is not dict or request.get('jsonrpc') != '2.0' or type(request.get('method')) is not str:
                raise ValueError('Malformed JSON-RPC request')
            identifier = request.get('id')
            if identifier is not None and type(identifier) not in (str, int):
                raise ValueError('Malformed JSON-RPC identifier')
            method, params = request['method'], request.get('params', {})
            if type(params) is not dict:
                raise ValueError('Malformed JSON-RPC parameters')
            if 'id' not in request:
                continue
            if method == 'initialize':
                version = params.get('protocolVersion')
                result = {'protocolVersion': version if version in _VERSIONS else _VERSIONS[0],
                          'capabilities': {'tools': {'listChanged': False}},
                          'serverInfo': {'name': 'agenttime-assistantbench', 'version': '1'}}
                initialized = True
            elif not initialized:
                raise ValueError('MCP initialization is required')
            elif method == 'ping':
                result = {}
            elif method == 'tools/list':
                result = {'tools': bridge.list_tools()}
            elif method == 'tools/call':
                try:
                    if set(params) - {'name', 'arguments', '_meta'}:
                        raise ValueError('Unexpected tool-call parameters')
                    result = bridge.call_tool(params.get('name'), params.get('arguments', {}))
                except Exception:
                    result = {'isError': True, 'content': [{'type': 'text',
                        'text': 'Browser action unavailable or failed. No action retry was performed.'}]}
            else:
                response = {'jsonrpc': '2.0', 'id': identifier, 'error': {'code': -32601, 'message': 'Method not found'}}
                output_stream.write(json.dumps(response) + '\n'); output_stream.flush(); continue
            response = {'jsonrpc': '2.0', 'id': identifier, 'result': result}
        except Exception:
            response = {'jsonrpc': '2.0', 'id': identifier, 'error': {'code': -32600, 'message': 'Invalid request'}}
        output_stream.write(json.dumps(response, ensure_ascii=False, allow_nan=False) + '\n')
        output_stream.flush()


def main():
    parser = argparse.ArgumentParser(description='Answer-free native AssistantBench MCP bridge')
    parser.add_argument('--goal-file', required=True)
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--attempt-id', required=True)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--qualification-start-url')
    parser.add_argument('--restore-snapshot')
    parser.add_argument('--restore-sha256')
    args = parser.parse_args()
    goal = Path(args.goal_file).read_text()
    backend = NativeBrowserBackend(goal, args.seed, args.qualification_start_url,
        restore_snapshot=args.restore_snapshot, restore_sha256=args.restore_sha256)
    bridge = AssistantBenchBridge(backend, args.state_dir, args.attempt_id)
    try:
        serve_stdio(bridge)
    finally:
        bridge.close()


if __name__ == '__main__':
    main()
