"""Source-presence checks. These cannot grant permission to execute a task."""
from pathlib import Path
import csv
import hashlib
import json
import os

POINTER_PREFIX = b'version https://git-lfs.github.com/spec/v1'
VISIBILITIES = {'preparation_only', 'subject', 'simulator', 'env_internal', 'verifier'}

# Immutable origin of the current roster history; later decisions append receipts.
SELECTION_ORIGIN_PATH = 'selection-changes/2026-10-05-automationbench.json'
SELECTION_ORIGIN_SHA256 = 'e6f4e5df316c19322467204100b8c30f6c7b83b8798840a03ae108e1521c40fd'


def check_record_identity(path, ref):
    if 'record_sha256' not in ref:
        return
    if 'data_row_index_zero_based' in ref:
        with path.open(encoding='utf-8-sig', newline='') as source:
            records = list(csv.DictReader(source))
        index = ref['data_row_index_zero_based']
        if type(index) is not int or not 0 <= index < len(records):
            raise ValueError(f'Invalid source record index: {ref["path"]}')
        record = records[index]
    elif 'record_id' in ref:
        records = json.loads(path.read_text())
        records = records if isinstance(records, list) else [records]
        matches = [r for r in records if isinstance(r, dict) and str(r.get('id')) == str(ref['record_id'])]
        if len(matches) != 1:
            raise ValueError(f'Source record ID is absent or ambiguous: {ref["path"]}')
        record = matches[0]
    else:
        raise ValueError(f'Source record has no locator: {ref["path"]}')
    digest = hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if digest != ref['record_sha256']:
        raise ValueError(f'Source record differs from the chosen record: {ref["path"]}')


def check_slot_identities(rows, frozen_rows):
    keys = ('slot_id', 'family_id', 'candidate_id')
    actual = {tuple(row[k] for k in keys) for row in rows}
    expected = {tuple(row[k] for k in keys) for row in frozen_rows}
    if actual != expected or len(actual) != len(rows):
        raise ValueError('Task slots no longer match the saved slot-to-identity map')


def check_selection_transition(rows, change):
    """Preserve unrelated identities when a recorded user decision changes a roster."""
    if change.get('decision_source') != 'user':
        raise ValueError('A selection transition requires a recorded user decision')

    def identities(records):
        result = {}
        for row in records:
            slot, family, candidate = (row[k] for k in ('slot_id', 'family_id', 'candidate_id'))
            if not all(isinstance(v, str) and v for v in (slot, family, candidate)) or slot in result:
                raise ValueError('A selection transition contains invalid or duplicate slot identities')
            result[slot] = (family, candidate)
        return result

    before = identities(change['before'])
    removed = identities(change['removed'])
    added = identities(change['added'])
    actual = identities(rows)
    if any(before.get(slot) != identity for slot, identity in removed.items()):
        raise ValueError('A retired task must match its previous slot exactly')
    retained = {slot: identity for slot, identity in before.items() if slot not in removed}
    if set(added) & set(before):
        raise ValueError('New selections must not reuse existing or retired slot identities')
    if actual != retained | added:
        raise ValueError('Selected tasks differ from the recorded replacement decision')
    return {slot: identity[1] for slot, identity in removed.items()}


def check_selection_history(rows, changes, *, origin_rows):
    """Validate ordered roster decisions without rewriting earlier evidence."""
    if not changes:
        raise ValueError('A selection history must contain at least one decision')
    current = origin_rows
    seen_slots = {row['slot_id'] for row in current}
    retired = {}
    for change in changes:
        check_slot_identities(change['before'], current)
        removed_slots = {row['slot_id'] for row in change['removed']}
        after = [row for row in current if row['slot_id'] not in removed_slots] + change['added']
        removed = check_selection_transition(after, change)
        added_slots = {row['slot_id'] for row in change['added']}
        if added_slots & seen_slots:
            raise ValueError('A selection history cannot reuse a previously occupied slot')
        seen_slots.update(added_slots)
        retired.update(removed)
        current = after
    check_slot_identities(rows, current)
    return retired


def read_selection_history(evidence):
    """The frozen identity manifest names the complete, ordered decision history."""
    frozen = json.loads((evidence / 'slot-identities.json').read_text())
    paths = frozen.get('selection_changes')
    if paths is None:
        paths = [frozen['selection_change']] if frozen.get('selection_change') else []
    if not isinstance(paths, list) or not paths or any(not isinstance(p, str) for p in paths) or len(paths) != len(set(paths)):
        raise ValueError('Selection history must name distinct ordered receipt paths')
    if paths[0] != SELECTION_ORIGIN_PATH:
        raise ValueError('Selection history must begin with its preserved origin receipt')
    changes = []
    previous_path = previous_hash = None
    for relative in paths:
        path = evidence / relative
        if not path.resolve().is_relative_to((evidence / 'selection-changes').resolve()):
            raise ValueError('Selection history receipt is outside its evidence directory')
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        change = json.loads(payload)
        if not changes and digest != SELECTION_ORIGIN_SHA256:
            raise ValueError('Selection history origin receipt changed')
        if changes and (change.get('previous_change') != previous_path or change.get('previous_change_sha256') != previous_hash):
            raise ValueError('Selection history predecessor receipt is missing or changed')
        changes.append(change)
        previous_path, previous_hash = relative, digest
    return changes[0]['before'], changes


def bind_reference(repo, ref, files_by_path):
    """Require a manifested file or manifested directory tree; preserve role metadata."""
    if ref.get('root') != 'repository':
        raise ValueError('Source presence requires a local repository-cache reference')
    rel = ref['path']
    path = repo / rel
    cache = (repo / 'benchmarks/cache').resolve()
    if not path.resolve().is_relative_to(cache):
        raise ValueError(f'Evidence escapes the private cache: {rel}')
    if not path.exists():
        raise ValueError(f'Required source is absent: {rel}')
    result = dict(ref)
    result['exists'] = True
    result.setdefault('visibility', 'preparation_only')
    if result['visibility'] not in VISIBILITIES:
        raise ValueError(f'Unknown evidence visibility: {rel}')
    if path.is_symlink():
        entry = files_by_path.get(rel)
        if not entry or entry.get('type') != 'symlink':
            raise ValueError(f'Symlink has no matching manifest entry: {rel}')
        target = os.readlink(path).encode()
        if len(target) != entry['bytes'] or hashlib.sha256(target).hexdigest() != entry['sha256']:
            raise ValueError(f'Symlink target changed: {rel}')
        for key in ('bytes', 'sha256'):
            if key in ref and ref[key] != entry[key]:
                raise ValueError(f'Component {key} disagrees with source manifest: {rel}')
        target_path = 'benchmarks/cache/' + str(path.resolve().relative_to(cache))
        target_evidence = bind_reference(repo, {'root': 'repository', 'path': target_path}, files_by_path)
        result.update(evidence_kind='symlink', bytes=entry['bytes'], sha256=entry['sha256'],
                      target_path=target_path, target_evidence=target_evidence)
    elif path.is_dir():
        prefix = rel.rstrip('/') + '/'
        entries = [e for p, e in files_by_path.items() if p.startswith(prefix)]
        if not entries:
            raise ValueError(f'Directory has no manifested source files: {rel}')
        actual = {str(p.relative_to(repo)) for p in path.rglob('*') if not p.is_dir() or p.is_symlink()}
        if actual != {e['path'] for e in entries}:
            raise ValueError(f'Directory contents differ from the source manifest: {rel}')
        tree = []
        for entry in sorted(entries, key=lambda e: e['path']):
            item = repo / entry['path']
            if not item.exists():
                raise ValueError(f'Manifested directory member is missing: {entry["path"]}')
            if item.is_file() and not item.is_symlink() and item.stat().st_size < 1024:
                with item.open('rb') as f:
                    if f.read(100).startswith(POINTER_PREFIX):
                        raise ValueError(f'Unhydrated pointer in required directory: {entry["path"]}')
            tree.append({k: entry[k] for k in ('path', 'bytes', 'sha256')})
        result.update(evidence_kind='manifested_directory', file_count=len(tree),
                      bytes=sum(e['bytes'] for e in tree),
                      tree_sha256=hashlib.sha256(json.dumps(tree, sort_keys=True, separators=(',', ':')).encode()).hexdigest())
        result.pop('sha256', None)
    else:
        entry = files_by_path.get(rel)
        if not entry:
            raise ValueError(f'Required file has no source-manifest entry: {rel}')
        for key in ('bytes', 'sha256'):
            if key in ref and ref[key] != entry[key]:
                raise ValueError(f'Component {key} disagrees with source manifest: {rel}')
        if not path.is_symlink() and path.stat().st_size < 1024:
            with path.open('rb') as f:
                if f.read(100).startswith(POINTER_PREFIX):
                    raise ValueError(f'Unhydrated pointer is not source content: {rel}')
        result.update(evidence_kind=entry.get('type', 'file'), bytes=entry['bytes'], sha256=entry['sha256'])
    if 'record_sha256' in ref:
        if not path.is_file():
            raise ValueError(f'Record evidence must name a file: {rel}')
        check_record_identity(path, ref)
    return result


def check_present_components(repo, rows, files_by_path):
    checked = 0
    for row in rows:
        if row['selection_status'] not in {'earlier_candidate', 'retained_version_mapping', 'draft_for_review', 'approved_replacement', 'unresolved'}:
            raise ValueError('Unknown task selection status')
        for name, component in row['components'].items():
            if component['status'] != 'present':
                continue
            evidence = component.get('evidence', [])
            if not evidence:
                raise ValueError(f'Present component has no evidence: {row["slot_id"]}/{name}')
            for ref in evidence:
                bound = bind_reference(repo, ref, files_by_path)
                if 'tree_sha256' in ref and bound.get('tree_sha256') != ref['tree_sha256']:
                    raise ValueError(f'Component directory manifest changed: {ref["path"]}')
                checked += 1
    return checked
