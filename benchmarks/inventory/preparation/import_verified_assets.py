"""Import the already acquired selected benchmark assets with object checks."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import os
import shutil
import stat

WORK = Path('/private/tmp/agenttime-inventory-20261002')
REPO = Path('/Users/michaelofengenden/Developer/TimeResearch/AgentTime-v1.1')
CACHE = REPO / 'benchmarks/cache'
audit = json.loads((WORK / 'local-sources.json').read_text())
selection = json.loads((WORK / 'selection-map.json').read_text())
receipts = []


def copy_checked(source, destination, expected_size, sha256=None, git_sha1=None):
    if not stat.S_ISREG(source.lstat().st_mode) or source.stat().st_size != expected_size:
        raise ValueError(f'not a regular expected-size file: {source}')
    if destination.exists():
        raise ValueError(f'refusing overwrite: {destination}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256()
    git = hashlib.sha1(b'blob ' + str(expected_size).encode() + b'\0')
    partial = destination.with_name(destination.name + '.partial')
    with source.open('rb') as src, partial.open('xb') as dst:
        while chunk := src.read(1024 * 1024):
            sha.update(chunk)
            git.update(chunk)
            dst.write(chunk)
    if (sha256 and sha.hexdigest() != sha256) or (git_sha1 and git.hexdigest() != git_sha1):
        partial.unlink()
        raise ValueError(f'digest mismatch: {source}')
    partial.chmod(0o600)
    os.replace(partial, destination)
    return {'path': str(destination.relative_to(REPO)), 'bytes': expected_size,
            'sha256': sha.hexdigest(), 'origin': str(source), 'expected_sha256': sha256,
            'expected_git_blob_sha1': git_sha1}


tests = audit['families']['programbench']['test_archive_source']
files = []
for task in tests['tasks']:
    for f in task['files']:
        files.append(copy_checked(Path(tests['path']) / f['path'], CACHE / 'programbench-tests-20261002' / f['path'], f['bytes'], sha256=f['sha256']))
receipts.append({'family': 'program', 'role': 'native_tests_verifier_only', 'revision': tests['commit'], 'files': files})
print(json.dumps({'family': 'program', 'copied_files': len(files)}), flush=True)

core = audit['families']['core-bench-v1-1']
core_ids = set(next(f for f in selection['families'] if f['family_id'] == 'core')['mapped_task_ids'])
files = []
for group in core['native_capsule_verification']:
    for f in group['files']:
        source = Path(f['path'])
        if source.name.removesuffix('.tar.gz') in core_ids:
            files.append(copy_checked(source, CACHE / 'core-capsules-20261002' / group['dataset_split'] / source.name, f['expected_bytes'], sha256=f['expected_sha256']))
grader = core['native_grader_verification']
p = Path(grader['path'])
files.append(copy_checked(p, CACHE / 'core-capsules-20261002/verifier/corebench.py', p.stat().st_size, sha256=grader['sha256']))
receipts.append({'family': 'core', 'role': 'native_capsules_and_verifier', 'files': files})
print(json.dumps({'family': 'core', 'copied_files': len(files)}), flush=True)

ale = audit['families']['agents-last-exam']
plan = json.loads(Path(ale['selected_source_verification']['manifest']['path']).read_text())
input_root = Path(ale['additional_roots'][0]['path']).parent
reference_root = Path(ale['additional_roots'][1]['path']).parent
files = []
for record in plan['records']:
    for f in record['files']:
        relative = PurePosixPath(f['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('unsafe ALE input path')
        partition = 'public-input' if f['source'] == 'input' else 'private-reference'
        source = (input_root if f['source'] == 'input' else reference_root) / relative
        files.append(copy_checked(source, CACHE / 'ale-assets-20261002' / partition / relative, f['size'],
                                  sha256=f.get('lfs_sha256'), git_sha1=None if f.get('lfs_sha256') else f['git_blob_sha1']))
receipts.append({'family': 'ale', 'role': 'public_inputs_and_separate_verifier_references', 'files': files})
print(json.dumps({'family': 'ale', 'copied_files': len(files)}), flush=True)

(CACHE / 'verified-assets-receipt-20261002.json').write_text(json.dumps({'schema_version': 1, 'qualification_status': 'not_run', 'imports': receipts}, indent=2) + '\n')
print(json.dumps({'total_files': sum(len(x['files']) for x in receipts), 'bytes': sum(f['bytes'] for x in receipts for f in x['files'])}), flush=True)
