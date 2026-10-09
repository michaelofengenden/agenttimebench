"""Verifier-only local grading of completed, immutable natural submissions.

Freeze ``source_pins`` in the controller's preparation, then construct LocalGrader
with those saved pins. Calling source_pins again is an explicit scorer revision,
not a retry of an existing grade. This module never starts a subject, contacts a
model, or changes controller observations. Gold enters only the local verifier.

Integrity/missing-completion failures raise GradingError and publish nothing.
After completion verifies, unavailable grading is distinct from a numeric zero.
HLE and BrowseComp remain queued for their native API judges. Receipts are
published atomically without overwriting, keyed by attempt and scorer revision.
"""
from __future__ import annotations

import csv
from datetime import datetime
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from uuid import UUID, uuid4

from .evidence import canonical_json
from .jsonio import loads_strict
from . import natural_inputs

GPQA_DATA = 'benchmarks/cache/v1-inputs-20261002/gpqa-diamond/data/gpqa_diamond.csv'
ASSISTANT_DATA = 'benchmarks/cache/v1-inputs-20261002/assistantbench/data/validation.json'
ASSISTANT_EVALUATION = ('benchmarks/cache/v1-inputs-20261002/assistantbench/code/browsergym/assistantbench/'
                        'src/browsergym/assistantbench/evaluation')
_SOURCE = Path(__file__).resolve()
_LOADED_SHA = hashlib.sha256(_SOURCE.read_bytes()).hexdigest()
_INPUT_SOURCE = Path(natural_inputs.__file__).resolve()
_INPUT_LOADED_SHA = hashlib.sha256(_INPUT_SOURCE.read_bytes()).hexdigest()
_QUEUE_KEYS = {'schema_version', 'attempt_id', 'task_id', 'status', 'sealed_answer_sha256',
               'sealed_answer_ref', 'contract_sha256', 'archive_inventory_sha256'}


class GradingError(ValueError):
    """Stable metadata only. Never expose a gold answer or a native exception."""


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _native_json(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False) + '\n').encode()


def _digest(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise GradingError('invalid_evidence_digest')
    return value


def _bytes(path, root):
    path, root = Path(path), Path(root)
    try:
        current = root
        for part in path.relative_to(root).parts:
            current /= part
            if part in ('.', '..') or current.is_symlink():
                raise GradingError('unsafe_verifier_path')
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise GradingError('unsafe_verifier_file')
            with os.fdopen(descriptor, 'rb', closefd=False) as source:
                return source.read()
        finally:
            os.close(descriptor)
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, GradingError): raise
        raise GradingError('verifier_input_missing_or_unreadable') from None


def _object(data):
    try:
        value = loads_strict(data)
        if type(value) is not dict: raise ValueError
        return value
    except (ValueError, TypeError, UnicodeError):
        raise GradingError('malformed_verifier_json') from None


def _python_path(value):
    try:
        supplied = Path(value or sys.executable).absolute()
        # Keep the executable symlink itself: resolving it escapes a virtualenv
        # and silently changes its site-packages while preserving the binary SHA.
        path = supplied.parent.resolve(strict=True) / supplied.name
        if not path.is_file() or not os.access(path, os.X_OK): raise ValueError
        return path
    except (OSError, ValueError, TypeError, RuntimeError):
        raise GradingError('native_scorer_runtime_unavailable') from None


_RUNTIME_PROBE = '''
import importlib.metadata, json, sys
result = {'python':sys.version.split()[0]}
for name in ('numpy','scipy'):
    try: result[name] = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError: result[name] = None
print(json.dumps(result,sort_keys=True))
'''


def _runtime_info(python):
    try:
        result = subprocess.run([str(python), '-I', '-B', '-c', _RUNTIME_PROBE],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env={'PATH': '/usr/bin:/bin'},
            timeout=10, check=False)
        value = _object(result.stdout)
        if (result.returncode != 0 or set(value) != {'python', 'numpy', 'scipy'}
                or type(value['python']) is not str
                or any(value[k] is not None and type(value[k]) is not str for k in ('numpy', 'scipy'))):
            raise ValueError
        return value
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise GradingError('native_scorer_runtime_unavailable') from None


def source_pins(repo_root, *, assistant_python=None):
    """Read candidate pins only; the caller must save/freeze them before grading.

    This observes package versions but does not qualify the scorer or grant
    launch authority. It never imports BrowserGym, datasets or private references.
    """
    repo = Path(repo_root).resolve(strict=True)
    directory = repo / ASSISTANT_EVALUATION
    sources = {path.relative_to(directory).as_posix(): _sha(_bytes(path, repo))
               for path in sorted(directory.rglob('*.py'))}
    if 'evaluator.py' not in sources: raise GradingError('native_scorer_source_missing')
    python = _python_path(assistant_python)
    return {'schema_version': 'agenttime.local-grader-pins.v1', 'grader_sha256': _LOADED_SHA,
            'natural_inputs_sha256': _INPUT_LOADED_SHA, 'gpqa_data_sha256': _sha(_bytes(repo / GPQA_DATA, repo)),
            'assistant_data_sha256': _sha(_bytes(repo / ASSISTANT_DATA, repo)),
            'assistant_sources': sources, 'assistant_python_sha256': _sha(python.read_bytes()),
            'assistant_runtime': _runtime_info(python)}


def _time(value):
    try:
        if type(value) is not str: raise ValueError
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.utcoffset() is None: raise ValueError
        return parsed
    except (TypeError, ValueError):
        raise GradingError('completed_native_boundaries_unverified') from None


def _publish_once(path, value):
    """A fully synced temporary file is hard-linked once into its final name."""
    path = Path(path)
    if any(parent.is_symlink() for parent in path.parents):
        raise GradingError('unsafe_grade_receipt_path')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink() or path.parent.resolve() != path.parent:
        raise GradingError('unsafe_grade_receipt_path')
    data = canonical_json(value)
    if path.exists() or path.is_symlink():
        if _bytes(path, path.parent) != data: raise GradingError('immutable_grade_receipt_changed')
        return
    temporary = path.with_name('.pending-' + uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(data); output.flush(); os.fsync(output.fileno())
        try: os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            if _bytes(path, path.parent) != data: raise GradingError('immutable_grade_receipt_changed')
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


_SCORER_CHILD = r'''
import contextlib, importlib.metadata, importlib.util, io, json, math, pathlib, socket, sys, types
def blocked(*a, **k): raise RuntimeError("network_disabled")
socket.socket = socket.create_connection = socket.getaddrinfo = blocked
request = json.loads(sys.stdin.buffer.read())
root = pathlib.Path(request['source_root'])
result = {'status':'unavailable','score':None,'has_answer':None,'reason':'native_scorer_failed'}
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    try:
        package = types.ModuleType('_agenttime_native_assistant_evaluation')
        package.__path__ = [str(root)]
        sys.modules[package.__name__] = package
        spec = importlib.util.spec_from_file_location(package.__name__+'.evaluator', root/'evaluator.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        score, has_answer = module.question_scorer(request['prediction'], request['gold'])
        if isinstance(score, (str, bool)) or isinstance(has_answer, (str, bool)): raise ValueError()
        score, has_answer = float(score), float(has_answer)
        if not math.isfinite(score) or not 0 <= score <= 1 or has_answer not in (0.0,1.0): raise ValueError()
        result = {'status':'available','score':score,'has_answer':has_answer,'reason':None}
    except ImportError:
        result['reason'] = 'native_scorer_dependencies_unavailable'
    except Exception:
        pass
result['runtime'] = {'python':sys.version.split()[0]}
for name in ('numpy','scipy'):
    try: result['runtime'][name] = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError: result['runtime'][name] = None
sys.stdout.write(json.dumps(result, sort_keys=True, allow_nan=False))
'''


class LocalGrader:
    """Read trusted controller evidence; publish verifier-only grade receipts.

    ``controller_root`` must be controller-owned, never writable by a subject.
    This validates the controller's immutable completion evidence without
    replaying the subject or re-interpreting its transcript. No receipt is written
    when integrity or completion is unknown. Native scorer errors are retained
    as unavailable grades and never coerced to zero.
    """
    def __init__(self, controller_root, repo_root, pins, *, assistant_python=None):
        self.root = Path(controller_root).resolve(strict=True)
        self.repo = Path(repo_root).resolve(strict=True)
        self.python = _python_path(assistant_python)
        self.pins = _object(canonical_json(pins))
        self.pins_sha256 = _sha(canonical_json(self.pins))
        expected = {'schema_version', 'grader_sha256', 'natural_inputs_sha256', 'gpqa_data_sha256',
                    'assistant_data_sha256', 'assistant_sources', 'assistant_python_sha256', 'assistant_runtime'}
        if (set(self.pins) != expected or self.pins['schema_version'] != 'agenttime.local-grader-pins.v1'
                or type(self.pins['assistant_sources']) is not dict or 'evaluator.py' not in self.pins['assistant_sources']
                or type(self.pins['assistant_runtime']) is not dict
                or set(self.pins['assistant_runtime']) != {'python', 'numpy', 'scipy'}):
            raise GradingError('invalid_grader_pins')
        for key, value in self.pins.items():
            if key.endswith('_sha256'): _digest(value)
        for name, digest in self.pins['assistant_sources'].items():
            if type(name) is not str or re.fullmatch(r'[A-Za-z0-9_/]+\.py', name) is None or '..' in name:
                raise GradingError('invalid_native_scorer_source_pin')
            _digest(digest)

    def receipt_path(self, attempt_id):
        try:
            if type(attempt_id) is not str or str(UUID(attempt_id)) != attempt_id: raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise GradingError('invalid_attempt_id') from None
        return self.root / 'grade-receipts' / attempt_id / (self.pins_sha256 + '.json')

    def _read_json(self, path):
        raw = _bytes(path, self.root)
        return _object(raw), raw

    def _completed(self, attempt_id):
        queue, queue_raw = self._read_json(self.root / 'grade-queue' / (attempt_id + '.json'))
        if (set(queue) != _QUEUE_KEYS or queue['schema_version'] != 'agenttime.natural-grade-queue.v1'
                or queue['attempt_id'] != attempt_id or queue['status'] != 'queued'
                or queue['task_id'] not in natural_inputs.SELECTED_SLOTS):
            raise GradingError('invalid_grade_queue_record')
        for name in ('sealed_answer_sha256', 'contract_sha256', 'archive_inventory_sha256'): _digest(queue[name])
        manifest, manifest_raw = self._read_json(self.root / 'manifest.json')
        binding, _ = self._read_json(self.root / 'binding.json')
        if (binding.get('manifest_sha256') != _sha(canonical_json(manifest)) or manifest.get('executor') != 'claude-natural-v1'
                or manifest.get('arm') != 'natural' or type(manifest.get('tasks')) is not list):
            raise GradingError('frozen_campaign_unverified')
        tasks = [x for x in manifest['tasks'] if type(x) is dict and x.get('id') == queue['task_id']]
        if len(tasks) != 1 or tasks[0].get('contract_sha256') != queue['contract_sha256']:
            raise GradingError('frozen_task_binding_mismatch')
        contract, contract_raw = self._read_json(self.root / 'contracts' / (queue['contract_sha256'] + '.json'))
        if _sha(contract_raw) != queue['contract_sha256'] or contract.get('slot_id') != queue['task_id']:
            raise GradingError('frozen_contract_binding_mismatch')
        directory = self.root / 'attempts' / attempt_id
        final, final_raw = self._read_json(directory / 'final-report.json')
        ack, ack_raw = self._read_json(directory / 'final-archive-ack.json')
        stop, stop_raw = self._read_json(directory / 'external-stop.json')
        worker, worker_raw = self._read_json(directory / 'worker-final-report.json')
        receipt, receipt_raw = self._read_json(directory / 'final-archive-receipt.json')
        if (final.get('attempt_id') != attempt_id or final.get('outcome') != 'completed'
                or final.get('timing_status') != 'valid' or final.get('native_sessions_verified') is not True
                or final.get('independent_archive_verified') is not True or final.get('failure_reason_codes') != []):
            raise GradingError('immutable_completion_unverified')
        for key in ('contract_sha256', 'sealed_answer_sha256', 'archive_inventory_sha256'):
            if final.get(key) != queue[key]: raise GradingError('completed_submission_binding_mismatch')
        if final.get('input_sha256') != tasks[0].get('input_sha256'): raise GradingError('completed_input_binding_mismatch')
        boundaries = final.get('native_boundaries', {})
        for key in ('prompt_released_at', 'root_process_exited_at', 'owned_work_drained_at', 'native_terminal_at'):
            _time(boundaries.get(key))
        # These are separately written wall-clock annotations. The controller
        # already validates ordering/duration on the worker's monotonic clock;
        # neither observer latency nor a wall-clock correction changes the grade.
        runtime = final.get('runtime_seconds')
        if type(runtime) not in (int, float) or not math.isfinite(runtime) or runtime < 0:
            raise GradingError('completed_native_runtime_unverified')
        evidence = final.get('stop_evidence', {})
        if (stop.get('stop_verified') is not True or stop.get('container_running') is not False
                or stop.get('external_verification') is not True or evidence.get('stop_verified') is not True
                or evidence.get('owned_work_drained') is not True or evidence.get('identity_sha256') != final.get('identity_sha256')
                or _sha(canonical_json(stop.get('identity'))) != final.get('identity_sha256')
                or _sha(canonical_json(stop)) != evidence.get('proof_sha256')):
            raise GradingError('external_stop_unverified')
        _time(stop.get('observed_at'))
        for key in ('attempt_id', 'identity_sha256', 'input_sha256', 'worker_report_sha256', 'archive_manifest_sha256',
                    'archive_inventory_sha256', 'native_session_evidence_sha256'):
            if ack.get(key) != final.get(key): raise GradingError('independent_archive_ack_mismatch')
        if (ack.get('schema_version') != 'agenttime.natural-final-archive-ack.v1'
                or ack.get('independent_archive_verified') is not True or ack.get('native_sessions_verified') is not True
                or _sha(canonical_json(worker)) != final.get('worker_report_sha256')
                or worker.get('attempt_id') != attempt_id or worker.get('state') != 'captured'
                or worker.get('native_sessions_verified') is not True
                or _sha(canonical_json(worker.get('native_session_evidence'))) != final.get('native_session_evidence_sha256')):
            raise GradingError('completed_worker_evidence_mismatch')
        if (receipt.get('schema_version') != 'agenttime.native-local-archive.v1' or receipt.get('verified') is not True
                or worker.get('archive') != receipt or _sha(receipt_raw) != final.get('archive_manifest_sha256')
                or receipt.get('inventory_sha256') != queue['archive_inventory_sha256']
                or _sha(_native_json(receipt.get('inventory'))) != queue['archive_inventory_sha256']):
            raise GradingError('archive_inventory_unverified')
        archive = directory / 'final-archive'
        answer_path = archive / 'capture/sealed-answer.txt'
        if queue['sealed_answer_ref'] != str(answer_path): raise GradingError('sealed_answer_path_mismatch')
        # Read each byte sequence once. These immutable bytes are used below.
        answer = _bytes(answer_path, self.root)
        delivered = _bytes(archive / 'capture/delivered-input.json', self.root)
        for path, data, digest in (('capture/sealed-answer.txt', answer, queue['sealed_answer_sha256']),
                                   ('capture/delivered-input.json', delivered, final['input_sha256'])):
            item = receipt['inventory'].get(path, {})
            if (item.get('type') != 'file' or item.get('sha256') != digest or item.get('bytes') != len(data)
                    or _sha(data) != digest):
                raise GradingError('sealed_archive_bytes_changed')
        try:
            if loads_strict(delivered) != natural_inputs.compile_input(contract, self.repo):
                raise GradingError('delivered_contract_input_mismatch')
            answer = answer.decode('utf-8', errors='strict')
        except (UnicodeError, TypeError, ValueError) as exc:
            if isinstance(exc, GradingError): raise
            raise GradingError('native_input_or_submission_malformed') from None
        bound = {'queue_sha256': _sha(queue_raw), 'contract_sha256': queue['contract_sha256'],
            'sealed_answer_sha256': queue['sealed_answer_sha256'], 'input_sha256': final['input_sha256'],
            'manifest_sha256': _sha(manifest_raw), 'final_report_sha256': _sha(final_raw),
            'archive_ack_sha256': _sha(ack_raw), 'archive_inventory_sha256': queue['archive_inventory_sha256'],
            'worker_report_sha256': _sha(worker_raw), 'external_stop_sha256': _sha(stop_raw)}
        return queue, contract, answer, bound

    def _verify_sources(self):
        if (_sha(_SOURCE.read_bytes()) != _LOADED_SHA or self.pins['grader_sha256'] != _LOADED_SHA
                or _sha(_INPUT_SOURCE.read_bytes()) != _INPUT_LOADED_SHA
                or self.pins['natural_inputs_sha256'] != _INPUT_LOADED_SHA):
            raise GradingError('loaded_grading_source_changed')

    def _data(self, path, pin):
        data = _bytes(self.repo / path, self.repo)
        if _sha(data) != self.pins[pin]: raise GradingError('private_reference_pin_mismatch')
        return data

    @staticmethod
    def _unavailable(reason):
        return {'status': 'unavailable', 'score': None, 'reason': reason}

    def _gpqa(self, contract, answer):
        raw = self._data(GPQA_DATA, 'gpqa_data_sha256')
        try:
            reader = csv.DictReader(io.StringIO(raw.decode('utf-8')))
            if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames): raise ValueError
            rows = [row for row in reader if row.get('Record ID') == contract['candidate_id']]
            if len(rows) != 1: raise ValueError
            row = rows[0]
            options = [row[key] for key in ('Correct Answer', 'Incorrect Answer 1', 'Incorrect Answer 2', 'Incorrect Answer 3')]
            if any(type(x) is not str or not x for x in options) or len(set(options)) != 4: raise ValueError
            messages = contract['native_messages']
            if len(messages) != 1: raise ValueError
            text = messages[0]['content']
            prefix = 'What is the correct answer to this question: ' + row['Question'] + '\n\nChoices:\n'
            suffix = '\n\nFormat your response as follows: "The correct answer is (insert answer here)"'
            if not text.startswith(prefix) or not text.endswith(suffix): raise ValueError
            body = text[len(prefix):-len(suffix)]
            # Match each frozen option exactly, including embedded line breaks.
            # Four choices make exhaustive matching small, and no new shuffle is applied.
            from itertools import permutations
            orders = [order for order in permutations(options) if body == '\n'.join(
                '(' + label + ') ' + option for label, option in zip('ABCD', order))]
            if len(orders) != 1: raise ValueError
            correct = 'ABCD'[orders[0].index(row['Correct Answer'])]
            return natural_inputs.grade_gpqa(answer, correct)
        except (ValueError, KeyError, TypeError, UnicodeError):
            return self._unavailable('private_reference_or_delivered_choices_unverified')

    def _assistant_score(self, prediction, gold):
        if _sha(self.python.read_bytes()) != self.pins['assistant_python_sha256']:
            raise GradingError('native_scorer_runtime_pin_mismatch')
        source_root = self.repo / ASSISTANT_EVALUATION
        actual = {p.relative_to(source_root).as_posix() for p in source_root.rglob('*.py')}
        if actual != set(self.pins['assistant_sources']): raise GradingError('native_scorer_source_set_changed')
        sources = {}
        for name, digest in self.pins['assistant_sources'].items():
            data = _bytes(source_root / name, self.repo)
            if _sha(data) != digest: raise GradingError('native_scorer_source_pin_mismatch')
            sources[name] = data
        # Execute verified code copies only. Neither gold nor the answer is put
        # in argv, files, inherited environment, exception logs or the receipt.
        with tempfile.TemporaryDirectory(prefix='agenttime-private-grader-') as tmp:
            stage = Path(tmp)
            for name, data in sources.items():
                path = stage / 'source' / name; path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            request = canonical_json({'prediction': prediction, 'gold': gold, 'source_root': str(stage / 'source')})
            try:
                result = subprocess.run([str(self.python), '-I', '-B', '-c', _SCORER_CHILD], input=request,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=stage,
                    env={'HOME': str(stage), 'TMPDIR': str(stage), 'PATH': '/usr/bin:/bin'}, timeout=30, check=False)
                if result.returncode != 0: return self._unavailable('native_scorer_process_failed')
                value = _object(result.stdout)
                if (set(value) != {'status', 'score', 'has_answer', 'reason', 'runtime'}
                        or value['status'] not in ('available', 'unavailable')
                        or type(value['runtime']) is not dict):
                    return self._unavailable('native_scorer_output_malformed')
                if value['runtime'] != self.pins['assistant_runtime']:
                    raise GradingError('native_scorer_dependency_pin_mismatch')
                if value['status'] == 'available':
                    if (type(value['score']) not in (int, float) or not math.isfinite(value['score'])
                            or not 0 <= value['score'] <= 1 or type(value['has_answer']) not in (int, float)
                            or value['has_answer'] not in (0.0, 1.0) or value['reason'] is not None):
                        return self._unavailable('native_scorer_output_malformed')
                elif (value['score'] is not None or value['has_answer'] is not None
                        or value['reason'] not in ('native_scorer_failed', 'native_scorer_dependencies_unavailable')):
                    return self._unavailable('native_scorer_output_malformed')
                return value
            except subprocess.TimeoutExpired:
                return self._unavailable('native_scorer_timeout')
            except GradingError:
                raise
            except (OSError, ValueError, TypeError):
                return self._unavailable('native_scorer_process_failed')

    def _assistant(self, contract, answer):
        raw = self._data(ASSISTANT_DATA, 'assistant_data_sha256')
        try:
            candidate = contract['candidate_id']
            if type(candidate) is not str or re.fullmatch(r'validation:(0|[1-9][0-9]*)', candidate) is None: raise ValueError
            index = int(candidate.split(':')[1]); dataset = loads_strict(raw)
            rows = [x['row'] for x in dataset['rows'] if type(x.get('row_idx')) is int and x['row_idx'] == index]
            if len(rows) != 1 or len(contract['native_messages']) != 1: raise ValueError
            row = rows[0]; gold = row['answer']
            if (row['task'] != contract['native_messages'][0]['content'] or type(gold) is not str or not gold.strip()):
                raise ValueError
        except (ValueError, KeyError, TypeError, AttributeError):
            return self._unavailable('private_reference_or_native_question_unverified')
        return self._assistant_score(answer, gold)

    def grade(self, attempt_id):
        path = self.receipt_path(attempt_id)
        self._verify_sources()
        if _sha(canonical_json(self.pins)) != self.pins_sha256:
            raise GradingError('frozen_grader_pins_changed')
        try:
            queue, contract, answer, bound = self._completed(attempt_id)
        except GradingError:
            raise
        except (ValueError, TypeError, KeyError, AttributeError):
            raise GradingError('malformed_completion_evidence') from None
        family = contract['family_id']
        if family == 'gpqa':
            result = self._gpqa(contract, answer); scorer = 'gpqa_native_terminal_choice'
        elif family == 'assistant':
            result = self._assistant(contract, answer); scorer = 'assistantbench_native_question_scorer'
        else:
            result = {'status': 'queued', 'score': None, 'reason': 'native_judge_api_access_pending'}
            scorer = family + '_native_api_judge_pending'
        receipt = {'schema_version': 'agenttime.natural-local-grade.v1', 'attempt_id': attempt_id,
            'task_id': queue['task_id'], 'family_id': family, 'scorer': scorer,
            'scorer_pins_sha256': self.pins_sha256, 'evidence': bound, 'scale': [0, 1], **result}
        _publish_once(path, receipt)
        return receipt
