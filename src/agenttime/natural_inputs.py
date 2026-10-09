"""Project the selected natural contracts without forecasts or private references.

This module does not grant launch authority or read datasets. The caller binds
the full contract hashes to its immutable launch manifest before compilation.
Only the four selected HLE image files may be read during compilation.
"""

import base64
import hashlib
import os
from pathlib import Path
import re
import stat
import struct

from .jsonio import loads_strict


SELECTED_SLOTS = tuple(
    [f'gpqa-{i:02}' for i in range(1, 13)]
    + [f'hle-{i:02}' for i in range(1, 21)]
    + [f'browsecomp-{i:02}' for i in range(1, 13)]
    + [f'assistant-{i:02}' for i in range(1, 7)]
)
_IMAGE_SLOTS = frozenset({'hle-03', 'hle-07', 'hle-09', 'hle-19'})
_BENCHMARKS = {'gpqa': 'GPQA Diamond', 'hle': 'HLE Diamond',
               'browsecomp': 'BrowseComp', 'assistant': 'AssistantBench'}
_SCHEMA = 'agenttime.natural-preparation-contract.v1'
_VISIBILITY = 'controller_only_render_only_native_messages_and_allowlisted_materials_to_subject'
_IMAGE_FIELDS = {'kind', 'name', 'media_type', 'sha256', 'bytes', 'width', 'height'}
_RESOURCE_FIELDS = ('allocation_scope', 'architecture', 'cpus', 'memory_gib', 'gpu', 'host_class', 'storage')
_FORECAST_MARKER = re.compile(
    r'BEGIN NATIVE (?:USER|SYSTEM) MESSAGE|END NATIVE MESSAGE|'
    r'Intended later task environment\.|\bminutes\s*=\s*N\b', re.IGNORECASE)


def _object(value, name):
    if type(value) is not dict:
        raise ValueError(f'{name} must be an object')
    return value


def _text(value, name):
    if type(value) is not str or not value.strip() or '\x00' in value:
        raise ValueError(f'{name} must be nonempty text')
    return value


def _positive_integer(value, name):
    if type(value) is not int or value <= 0:
        raise ValueError(f'{name} must be a positive integer')


def _validate(contract):
    value = _object(contract, 'contract')
    if value.get('schema_version') != _SCHEMA or value.get('visibility') != _VISIBILITY:
        raise ValueError('Expected a controller-only natural preparation contract')
    if value.get('arm') != 'natural' or any(
        key not in value or value[key] is not None
        for key in ('duration_request', 'experiment_time_cap_seconds')
    ):
        raise ValueError('Natural contracts cannot contain a duration treatment or experiment cap')
    if any(key in value for key in ('forecast_request', 'forecast_minutes', 'historical_response', 'request')):
        raise ValueError('Forecast or session wrappers are not native contracts')
    slot = value.get('slot_id')
    if type(slot) is not str or slot not in SELECTED_SLOTS:
        raise ValueError('Task is outside the frozen fifty-task selection')
    family = slot.split('-')[0]
    if value.get('family_id') != family:
        raise ValueError('Task family disagrees with its selected slot')
    _text(value.get('candidate_id'), 'candidate_id')
    _text(value.get('title'), 'title')
    messages = value.get('native_messages')
    if type(messages) is not list or not messages:
        raise ValueError('native_messages must be a nonempty list')
    for message in messages:
        _object(message, 'native message')
        if set(message) != {'role', 'content'} or message['role'] != 'user':
            raise ValueError('The selected tasks permit only native user text messages')
        text = _text(message['content'], 'native message content')
        if _FORECAST_MARKER.search(text):
            raise ValueError('Forecast wrapper found in native message')
    completion = _object(value.get('completion_rule'), 'completion_rule')
    _text(completion.get('kind'), 'completion kind')
    _text(completion.get('text'), 'completion text')
    materials = value.get('allowed_materials')
    if type(materials) is not list or len(materials) != int(slot in _IMAGE_SLOTS):
        raise ValueError('Selected task has a missing or unexpected image material')
    for material in materials:
        _object(material, 'material')
        if set(material) != _IMAGE_FIELDS:
            raise ValueError('Image material has missing fields or an unexpected path')
        if (material['kind'], material['name'], material['media_type']) != ('image', 'question.png', 'image/png'):
            raise ValueError('Only the selected question.png image is permitted')
        if type(material['sha256']) is not str or not re.fullmatch(r'[0-9a-f]{64}', material['sha256']):
            raise ValueError('Image SHA-256 must be a lowercase hexadecimal digest')
        for name in ('bytes', 'width', 'height'):
            _positive_integer(material[name], f'image {name}')
        if re.fullmatch(r'[0-9a-f]{24}', value['candidate_id']) is None:
            raise ValueError('HLE image identity must be its native record identifier')
    target = _object(value.get('intended_target'), 'intended_target')
    hardware = _object(target.get('hardware_allocation'), 'hardware_allocation')
    _text(target.get('intended_interfaces'), 'intended_interfaces')
    if any(key not in hardware for key in _RESOURCE_FIELDS):
        raise ValueError('Hardware allocation lacks required resource fields')
    for key in ('architecture', 'host_class'):
        if hardware[key] is not None:
            _text(hardware[key], f'hardware {key}')
    if hardware['storage'] is not None:
        raise ValueError('The selected contracts leave task storage unassigned')
    gpu = _object(hardware['gpu'], 'GPU allocation')
    if (set(gpu) != {'count', 'model', 'memory_gib'} or type(gpu['count']) is not int
            or gpu['count'] != 0 or gpu['model'] is not None or gpu['memory_gib'] is not None):
        raise ValueError('The selected fifty tasks do not have a task GPU')
    if family in ('gpqa', 'hle'):
        if (hardware['allocation_scope'] != 'no_task_compute'
                or hardware['cpus'] is not None or hardware['memory_gib'] is not None
                or _object(target.get('intended_network'), 'network').get('policy') != 'model_transport_only'
                or _object(target.get('intended_subagents'), 'subagents').get('policy') != 'disabled'):
            raise ValueError('Closed-book tasks must preserve no-compute, no-tools and no-delegation policies')
    elif (hardware['allocation_scope'] != 'natural_subject' or type(hardware['cpus']) is not int
          or hardware['cpus'] != 4 or type(hardware['memory_gib']) not in (int, float)
          or hardware['memory_gib'] != 16):
        raise ValueError('Selected web tasks require their frozen 4-CPU/16-GiB allocation')
    return value


def _read_regular(path, root):
    """Reject symlink aliases and non-files, returning one observed byte sequence."""
    root = Path(root).resolve(strict=True)
    relative = Path(path).relative_to(root)
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError('Input paths cannot contain symlinks')
    descriptor = os.open(current, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError('Input must be a regular file')
        with os.fdopen(descriptor, 'rb', closefd=False) as source:
            return source.read()
    finally:
        os.close(descriptor)


def compile_input(contract, repo_root):
    """Return a Claude stream-json user event containing only native text/images.

    No dataset, grader, forecast input, response or previous session is opened.
    Metadata validation is not a substitute for the caller's manifest hash check.
    """
    value = _validate(contract)
    blocks = [{'type': 'text', 'text': message['content']} for message in value['native_messages']]
    for material in value['allowed_materials']:
        try:
            root = Path(repo_root).resolve(strict=True)
            path = root / 'benchmarks/cache/hle-question-images-20261002/images' / (value['candidate_id'] + '.png')
            data = _read_regular(path, root)
        except (OSError, RuntimeError) as error:
            raise ValueError('Selected image file is unavailable') from error
        if len(data) != material['bytes'] or hashlib.sha256(data).hexdigest() != material['sha256']:
            raise ValueError('Selected image bytes differ from the contract pin')
        if (len(data) < 33 or data[:8] != b'\x89PNG\r\n\x1a\n'
                or data[8:16] != b'\x00\x00\x00\rIHDR'
                or struct.unpack('>II', data[16:24]) != (material['width'], material['height'])):
            raise ValueError('Selected PNG format or dimensions differ from the contract')
        blocks.append({'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png',
                                                 'data': base64.b64encode(data).decode('ascii')}})
    return {'type': 'user', 'message': {'role': 'user', 'content': blocks}}


def build_roster(contractdir):
    """Read exactly the selected contracts and return dashboard-safe metadata.

    Images and answer references are not opened. Hashes cover the actual saved
    contract bytes, not a reformatted JSON representation. No readiness is implied.
    """
    try:
        root = Path(contractdir).resolve(strict=True)
        rows = []
        for slot in SELECTED_SLOTS:
            data = _read_regular(root / (slot + '.json'), root)
            value = _validate(loads_strict(data))
            if value['slot_id'] != slot:
                raise ValueError('Contract filename and task identity disagree')
            hardware = value['intended_target']['hardware_allocation']
            resources = {key: hardware[key] for key in _RESOURCE_FIELDS}
            resources['gpu'] = dict(hardware['gpu'])
            rows.append({'task_id': slot, 'label': value['title'], 'benchmark': _BENCHMARKS[value['family_id']],
                         'contract_sha256': hashlib.sha256(data).hexdigest(),
                         'required_resources': resources, 'interface': value['intended_target']['intended_interfaces']})
        return rows
    except (OSError, RuntimeError, UnicodeError) as error:
        raise ValueError('Selected contract is missing or unreadable') from error


_GPQA_FINAL = re.compile(r'The correct answer is\s+(?:\(([ABCD])\)|([ABCD]))\.?', re.IGNORECASE)
_ANSWER_CLAIM = re.compile(r'\bthe correct answer is\b|^\s*(?:final\s+)?answer\s*:', re.IGNORECASE | re.MULTILINE)


def grade_gpqa(final_text, correct_choice):
    """Grade one unambiguous terminal native-format A-D choice.

    ``correct_choice`` is supplied privately by the caller after mapping the
    reference to the already frozen choice order. Neither it nor answer text is
    returned. Unsupported, conflicting or missing answers remain unavailable;
    only a parsed wrong choice is a numeric zero.
    """
    if type(correct_choice) is not str or correct_choice not in 'ABCD' or len(correct_choice) != 1:
        raise ValueError('Private correct_choice must be exactly one of A, B, C, D')
    unavailable = {'status': 'unavailable', 'score': None, 'reason': 'missing_malformed_or_ambiguous_final_answer'}
    if type(final_text) is not str or not final_text.strip() or len(_ANSWER_CLAIM.findall(final_text)) != 1:
        return unavailable
    last_line = final_text.strip().splitlines()[-1].strip()
    if last_line.startswith('**') and last_line.endswith('**'):
        last_line = last_line[2:-2].strip()
    match = _GPQA_FINAL.fullmatch(last_line)
    if match is None:
        return unavailable
    choice = (match.group(1) or match.group(2)).upper()
    return {'status': 'available', 'score': float(choice == correct_choice), 'reason': None}


def parse_browsecomp_judgement(text):
    """Parse the native judge's standalone correct field, never the whole match.

    Missing, duplicate or malformed fields raise ValueError and must be recorded
    as unavailable grading, not as a wrong subject answer. The native judge has
    exactly two supported decisions: yes and no.
    """
    if type(text) is not str:
        raise ValueError('BrowseComp judgement must be text')
    fields = re.findall(r'^\s*correct\s*:\s*([^\r\n]*)', text, re.IGNORECASE | re.MULTILINE)
    if len(fields) != 1 or fields[0].strip().lower() not in {'yes', 'no'}:
        raise ValueError('BrowseComp judgement has ambiguous or malformed correct fields')
    return fields[0].strip().lower()
