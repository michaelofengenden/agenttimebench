"""Fetch selected pinned datasets with an existing local grant; never execute them."""
import concurrent.futures
from pathlib import Path, PurePosixPath
import hashlib
import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone

WORK = Path('/private/tmp/agenttime-inventory-20261002')
DEST = WORK / 'gated-sources'
DEST.mkdir(exist_ok=True)
TOKEN = (Path.home() / '.cache/huggingface/token').read_text().strip()

class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new and urllib.parse.urlparse(newurl).netloc != 'huggingface.co':
            new.remove_header('Authorization')
        return new

def download(repo, rev, entry, target, expected_sha256=None):
    rel = entry['path']
    assert not PurePosixPath(rel).is_absolute() and '..' not in PurePosixPath(rel).parts
    limit = entry['size']
    assert limit <= 512 * 1024 * 1024
    url = f'https://huggingface.co/datasets/{repo}/resolve/{rev}/{urllib.parse.quote(rel)}'
    request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + TOKEN})
    path = target / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        part = path.with_name(path.name + '.partial')
        total = 0
        with urllib.request.build_opener(SafeRedirect()).open(request, timeout=60) as response, part.open('wb') as output:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                assert total <= limit, rel
                output.write(block)
        assert total == limit, (rel, total, limit)
        part.rename(path)
    content = path.read_bytes()
    sha256 = hashlib.sha256(content).hexdigest()
    blob_sha1 = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()
    lfs_hash = entry.get('lfs', {}).get('oid')
    if lfs_hash:
        assert sha256 == lfs_hash
    else:
        assert blob_sha1 == entry['oid'], rel
    if expected_sha256:
        assert sha256 == expected_sha256, rel
    return {'path': rel, 'bytes': len(content), 'sha256': sha256, 'url': url, 'git_oid': entry['oid'], 'expected_lfs_sha256': lfs_hash, 'release_sha256': expected_sha256}

osrepo, osrev = 'xlangai/osworld_v2_tasks', '0a1aadad95aa79b00b3783e717d865089ab06e26'
tree = json.loads((WORK / 'osworld21-check/dataset-revision-tree.json').read_text())
release = json.loads(Path('/Users/michaelofengenden/Developer/TimeResearch/AgentTime-v1.1/benchmarks/releases/osworld-2.1/osworld-v2.1.task_hashes.json').read_text())['files']
target = DEST / 'osworld21-tasks'
items = [x for x in tree if x['type'] == 'file']
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    records = list(pool.map(lambda e: download(osrepo, osrev, e, target, release.get(e['path'], {}).get('sha256')), items))
(target / 'download-receipt.json').write_text(json.dumps({'repository': osrepo, 'revision': osrev, 'files': records, 'checked_at_utc': datetime.now(timezone.utc).isoformat(), 'verification': 'All Git blob or LFS object hashes verified, plus official task release SHA-256 where declared.', 'benchmark_runs': 0}, indent=2) + '\n')
print(json.dumps({'family': 'osworld', 'files': len(records), 'bytes': sum(x['bytes'] for x in records), 'verified_release_tasks': len(release)}), flush=True)

repo, rev = 'cais/hle-diamond', '04eeb7efa7e3e4f83a00cbd5ce436a38fd5dda23'
url = f'https://huggingface.co/api/datasets/{repo}/tree/{rev}?recursive=true'
req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + TOKEN})
with urllib.request.build_opener(SafeRedirect()).open(req, timeout=30) as response:
    raw = response.read(4 * 1024 * 1024)
tree = json.loads(raw)
target = DEST / 'hle-diamond'
target.mkdir(exist_ok=True)
(target / 'revision-tree.json').write_bytes(raw)
records = [download(repo, rev, e, target) for e in tree if e['type'] == 'file']
(target / 'download-receipt.json').write_text(json.dumps({'repository': repo, 'revision': rev, 'files': records, 'checked_at_utc': datetime.now(timezone.utc).isoformat(), 'verification': 'Git blob and LFS object SHA hashes checked.', 'data_scope': 'Private evaluation preparation only. Do not expose questions, answers or rationales in published inventory files.', 'benchmark_runs': 0}, indent=2) + '\n')
print(json.dumps({'family': 'hle', 'files': len(records), 'bytes': sum(x['bytes'] for x in records)}), flush=True)
