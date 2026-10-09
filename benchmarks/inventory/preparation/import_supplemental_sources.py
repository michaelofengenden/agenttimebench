"""Copy verified small preparation sources into the private v1.1 cache."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone

WORK = Path('/private/tmp/agenttime-inventory-20261002')
REPO = Path('/Users/michaelofengenden/Developer/TimeResearch/AgentTime-v1.1')
CACHE = REPO / 'benchmarks/cache'

def copy_checked(src, dst, expected):
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        part = dst.with_name(dst.name + '.partial')
        assert not part.exists()
        shutil.copyfile(src, part)
        assert hashlib.sha256(part.read_bytes()).hexdigest() == expected
        part.rename(dst)
    assert hashlib.sha256(dst.read_bytes()).hexdigest() == expected

for family in ['osworld21-tasks', 'hle-diamond']:
    origin = WORK / 'gated-sources' / family
    receipt = json.loads((origin / 'download-receipt.json').read_text())
    dest = CACHE / 'gated-inputs-20261002' / family
    for entry in receipt['files']:
        copy_checked(origin / entry['path'], dest / entry['path'], entry['sha256'])
    receipt['source_path'] = str(dest)
    receipt['origin_receipt_sha256'] = hashlib.sha256((origin / 'download-receipt.json').read_bytes()).hexdigest()
    receipt['imported_at_utc'] = datetime.now(timezone.utc).isoformat()
    (dest / 'import-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({'family': family, 'files': len(receipt['files']), 'bytes': sum(e['bytes'] for e in receipt['files'])}), flush=True)

git = Path('/Users/michaelofengenden/AgentTimeSources/benchmarks/pptarena/source-body/repository.git')
commit = 'ea99cc42e7d8f1423e270b425d84d8211d50c494'
prefixes = ['src/llm/', 'src/ppt/', 'src/utils/']
exact = {'agent_bench/README.md', 'agent_bench/judge_predictions.py', 'src/requirements.txt'}
paths = subprocess.check_output(['git', '--git-dir=' + str(git), 'ls-tree', '-r', '--name-only', commit], text=True).splitlines()
dest = CACHE / 'pptarena-grader-20261002'
files = []
for path in paths:
    if not (path in exact or (path.endswith('.py') and any(path.startswith(prefix) for prefix in prefixes))):
        continue
    content = subprocess.check_output(['git', '--git-dir=' + str(git), 'cat-file', 'blob', commit + ':' + path])
    sha256 = hashlib.sha256(content).hexdigest()
    target = dest / path
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        assert hashlib.sha256(target.read_bytes()).hexdigest() == sha256
    else:
        target.write_bytes(content)
    files.append({'path': path, 'bytes': len(content), 'sha256': sha256, 'git_blob_sha1': hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()})
upstream = json.loads((WORK / 'local-sources.json').read_text())['families']['pptarena']['upstream']
(dest / 'import-receipt.json').write_text(json.dumps({'repository': upstream, 'commit': commit, 'local_git_source': str(git), 'source_path': str(dest), 'files': files, 'recorded_at_utc': datetime.now(timezone.utc).isoformat(), 'runtime_validated': False, 'note': 'Selected native grading source only; dependencies and judging configuration require qualification.'}, indent=2) + '\n')
print(json.dumps({'family': 'pptarena-grader', 'files': len(files), 'bytes': sum(f['bytes'] for f in files)}))
