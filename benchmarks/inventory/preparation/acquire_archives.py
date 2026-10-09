"""Download pinned public source archives without executing their contents."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import urllib.request
from datetime import datetime, timezone


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def acquire(spec, root):
    commit = spec['commit']
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('immutable commit required')
    folder = root / (spec['id'] + '-' + commit[:12])
    folder.mkdir(parents=True, exist_ok=False)
    url = f"https://codeload.github.com/{spec['repository']}/tar.gz/{commit}"
    archive = folder / 'source.tar.gz'
    request = urllib.request.Request(url, headers={'User-Agent': 'AgentTime-source-inventory'})
    size = 0
    with urllib.request.urlopen(request, timeout=40) as response, archive.open('xb') as target:
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > 512 * 1024**2:
                raise ValueError('archive exceeds preparation download bound')
            target.write(chunk)
    source = folder / 'source'
    source.mkdir()
    files, omitted, names = [], [], set()
    expanded = 0
    with tarfile.open(archive, 'r:gz') as bundle:
        members = bundle.getmembers()
        roots = {PurePosixPath(m.name).parts[0] for m in members if PurePosixPath(m.name).parts}
        if len(roots) != 1:
            raise ValueError('archive must have one top-level source directory')
        for member in members:
            parts = PurePosixPath(member.name).parts
            if member.name.startswith('/') or '..' in parts or '\\' in member.name:
                raise ValueError('unsafe archive path')
            if len(parts) < 2 or member.isdir():
                continue
            relative = PurePosixPath(*parts[1:]).as_posix()
            if relative in names:
                raise ValueError('duplicate archive path')
            names.add(relative)
            if not member.isfile():
                omitted.append({'path': relative, 'reason': 'non_regular_member_not_extracted'})
                continue
            expanded += member.size
            if expanded > 2 * 1024**3 or len(files) >= 100000:
                raise ValueError('expanded source exceeds preparation bound')
            destination = source / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            data = bundle.extractfile(member).read()
            destination.write_bytes(data)
            destination.chmod(0o755 if member.mode & 0o111 else 0o644)
            files.append({'path': relative, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                          'git_lfs_pointer': data.startswith(b'version https://git-lfs.github.com/spec/v1')})
    result = {**spec, 'download_url': url, 'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
              'archive_sha256': digest(archive), 'archive_bytes': size, 'source_path': str(source),
              'files': files, 'omitted': omitted, 'runtime_validated': False,
              'verification': 'public archive addressed by pinned commit; file digests recorded; benchmark not executed'}
    (folder / 'receipt.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('spec', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    for spec in json.loads(args.spec.read_text()):
        try:
            result = acquire(spec, args.destination)
            print(json.dumps({'id': spec['id'], 'files': len(result['files']), 'bytes': result['archive_bytes'],
                              'omitted': len(result['omitted']), 'source_path': result['source_path']}), flush=True)
        except Exception as exc:
            print(json.dumps({'id': spec['id'], 'error': type(exc).__name__ + ': ' + str(exc)}), flush=True)
