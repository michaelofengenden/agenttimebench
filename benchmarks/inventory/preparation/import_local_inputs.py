"""Copy only manifest-listed benchmark input files, checking every digest."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat

OLD = '/Users/michaelofengenden/Developer/AgentTimeExperiment-data/'
NEW = '/Users/michaelofengenden/Developer/TimeResearch/AgentTimeExperiment-data/'
EXCLUDED_FAMILIES = {'wildclawbench', 'humanitys-last-exam', 'osworld-2-0', 'posttrainbench-v1-1'}


def import_files(receipt_path, destination, program_ids):
    receipt = json.loads(receipt_path.read_text())
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    manifest = {'schema_version': 1, 'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
                'origin_receipt': str(receipt_path),
                'origin_receipt_sha256': hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
                'purpose': 'private benchmark source inventory; never mount this mixed source cache into an evaluated session',
                'qualification_status': 'not_run', 'copies': [], 'errors': []}
    for copy in receipt['copies']:
        source = Path(copy['destination'].replace(OLD, NEW))
        relative = PurePosixPath(copy['destination'].split('/sources/', 1)[1])
        family = relative.parts[0]
        if family in EXCLUDED_FAMILIES:
            continue
        if family == 'programbench' and 'tasks' in relative.parts and relative.parts[2] not in program_ids:
            continue
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('unsafe source root')
        target_root = destination / relative
        entry = {'family_slug': family, 'origin': str(source), 'relative_path': str(relative),
                 'observed_revision_marker': copy.get('observed_revision_marker'),
                 'recorded_tree_sha256': copy['sha256'], 'files': {}, 'errors': []}
        for name, expected in copy['files'].items():
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or '\\' in name:
                raise ValueError('unsafe source file path')
            if '.git' in path.parts or path.name in {'secrets.env', '.env', 'auth.json', 'credentials.json'}:
                continue
            origin = source / name
            target = target_root / name
            try:
                if not stat.S_ISREG(origin.lstat().st_mode):
                    raise ValueError('non-regular source')
                for parent in origin.parents:
                    if parent == source.parent:
                        break
                    if parent.is_symlink():
                        raise ValueError('source symlink rejected')
                if origin.stat().st_size != expected['size']:
                    raise ValueError('source size differs from receipt')
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + '.partial')
                hasher = hashlib.sha256()
                with origin.open('rb') as src, temporary.open('xb') as dst:
                    while chunk := src.read(1024 * 1024):
                        hasher.update(chunk)
                        dst.write(chunk)
                if hasher.hexdigest() != expected['sha256']:
                    temporary.unlink()
                    raise ValueError('source content differs from receipt')
                temporary.chmod(0o600)
                os.replace(temporary, target)
                entry['files'][name] = expected
            except (OSError, ValueError) as exc:
                entry['errors'].append({'path': name, 'error': str(exc)})
        entry['status'] = 'matches_recorded_input_digests' if not entry['errors'] else 'incomplete'
        manifest['copies'].append(entry)
        manifest['errors'].extend({'family': family, **error} for error in entry['errors'])
        print(json.dumps({'source': str(relative), 'files': len(entry['files']), 'errors': len(entry['errors'])}), flush=True)
    (destination / 'import-receipt.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('receipt', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('candidates', type=Path)
    args = parser.parse_args()
    ids = set(json.loads(args.candidates.read_text())['programbench']['programbench_roster'])
    result = import_files(args.receipt, args.destination, ids)
    print(json.dumps({'copies': len(result['copies']), 'errors': len(result['errors']),
                      'bytes': sum(x['size'] for c in result['copies'] for x in c['files'].values())}))
