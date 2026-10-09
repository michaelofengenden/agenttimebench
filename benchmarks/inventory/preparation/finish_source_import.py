"""Import pinned preparation sources; never execute upstream benchmark code."""
from pathlib import Path
import hashlib
import json
import shutil
from datetime import datetime, timezone

WORK = Path('/private/tmp/agenttime-inventory-20261002')
REPO = Path('/Users/michaelofengenden/Developer/TimeResearch/AgentTime-v1.1')
CACHE = REPO / 'benchmarks/cache'


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def copy_checked(src, dst, expected):
    if dst.exists():
        assert dst.is_file() and digest(dst) == expected, str(dst)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        part = dst.with_name(dst.name + '.partial')
        assert not part.exists(), str(part)
        shutil.copyfile(src, part)
        assert digest(part) == expected, str(part)
        part.rename(dst)


receipts = []
for bundle in sorted((WORK / 'acquired').iterdir()):
    if not bundle.is_dir():
        continue
    original = bundle / 'receipt.json'
    data = json.loads(original.read_text())
    dst = CACHE / 'upstream-20261002' / bundle.name
    for f in data['files']:
        copy_checked(bundle / 'source' / f['path'], dst / 'source' / f['path'], f['sha256'])
    archive = next(bundle.glob('*.tar.gz'))
    copy_checked(archive, dst / archive.name, data['archive_sha256'])
    data.update({
        'origin_receipt_sha256': digest(original),
        'origin_source_path': data['source_path'],
        'source_path': str(dst / 'source'),
        'archive_path': str(dst / archive.name),
        'imported_at_utc': datetime.now(timezone.utc).isoformat(),
        'cache_scope': 'Private preparation cache. Contains verifier material and public reference results; never mount this entire directory into an evaluated session.',
    })
    (dst / 'receipt.json').write_text(json.dumps(data, indent=2) + '\n')
    receipts.append({'id': data['id'], 'receipt': str((dst / 'receipt.json').relative_to(REPO)), 'files': len(data['files']), 'source_bytes': sum(f['bytes'] for f in data['files']), 'archive_bytes': data['archive_bytes']})

verification = json.loads((WORK / 'new-candidates/verification.json').read_text())
public = CACHE / 'public-metadata-20261002'
files = []
for entry in verification['checked_downloads']:
    src = Path(entry['path'])
    dst = public / src.name
    copy_checked(src, dst, entry['sha256_verified'])
    files.append({'path': str(dst.relative_to(REPO)), 'origin_path': str(src), 'sha256': entry['sha256_verified'], 'bytes': entry['bytes_verified']})
(public / 'import-receipt.json').write_text(json.dumps({'recorded_at_utc': datetime.now(timezone.utc).isoformat(), 'files': files, 'scope': 'Private upstream metadata and BrowseComp encrypted dataset; no question answers are exposed in the review inventory.', 'runtime_validated': False}, indent=2) + '\n')
print(json.dumps({'upstream_bundles': receipts, 'metadata_files': len(files), 'metadata_bytes': sum(f['bytes'] for f in files)}))
