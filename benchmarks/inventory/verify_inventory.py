"""Verify inventory structure and private-cache content, without executing benchmarks."""
from pathlib import Path
from collections import Counter
import hashlib
import json
import argparse
import os
from evidence_checks import bind_reference, check_present_components, check_slot_identities, check_selection_history, read_selection_history


def check_historical_candidates(rows, base, origin, history):
    """Preserve the original hundred and subtract only recorded exact retirements."""
    original = [(f['family_id'], c['task_id'])
                for f in base['families'] for c in f['known_candidates']]
    if len(original) != 100 or len(set(original)) != 100:
        raise ValueError('Historical selection must contain its original 100 distinct candidates')
    origin_ids = {(r['family_id'], r['candidate_id']) for r in origin}
    if not set(original).issubset(origin_ids):
        raise ValueError('Historical candidates disagree with the preserved roster origin')
    check_selection_history(rows, history, origin_rows=origin)
    removed = {(r['family_id'], r['candidate_id']) for change in history for r in change['removed']}
    expected = set(original) - removed
    actual = [(r['family_id'], r['candidate_id']) for r in rows
              if r['selection_status'] == 'earlier_candidate']
    if set(actual) != expected or len(actual) != len(expected):
        raise ValueError('Active earlier candidates differ from the recorded historical retirements')
    return {'historical': 100, 'active': len(actual), 'retired': len(set(original) & removed)}


def verify(repo, hashes=True):
    folder = repo / 'benchmarks/inventory'
    data = json.loads((folder / 'tasks.json').read_text())
    sources = json.loads((folder / 'evidence/source-manifest.json').read_text())
    suite = json.loads((repo / 'configs/suite.json').read_text())
    rows = data['rows']
    for relative, expected_hash in data.get('build_input_sha256', {}).items():
        source = repo / relative
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != expected_hash:
            raise ValueError(f'Inventory input changed since it was built: {relative}')
    frozen = json.loads((folder / 'evidence/slot-identities.json').read_text())
    check_slot_identities(rows, frozen['rows'])
    origin, history = read_selection_history(folder / 'evidence')
    check_selection_history(rows, history, origin_rows=origin)
    assert len(rows) == sum(f['count'] for f in suite['families']) == len({r['slot_id'] for r in rows})
    assert Counter(r['family_id'] for r in rows) == {f['id']:f['count'] for f in suite['families'] if f['count']}
    ids = [(r['family_id'],r['candidate_id']) for r in rows if r['candidate_id']]
    assert len(set(ids)) == len(ids)
    base = json.loads((folder / 'evidence/selection-map.json').read_text())
    historical = check_historical_candidates(rows, base, origin, history)
    assert all(not r['admitted'] and not r['runtime_qualified'] for r in rows)
    draft = json.loads((folder / 'recommendations.json').read_text())
    assert {r['slot_id'] for r in draft['rows']} == {r['slot_id'] for r in rows if r['selection_status']=='draft_for_review'}
    assert len(draft['rows']) == data['totals']['selection']['draft_for_review']
    assert all(r['components']['environment']['status']!='present' for r in rows)
    assert all(r['natural_prompt']['status']=='needs_checking' for r in rows)
    for row in rows:
        if row['family_id']=='yc':
            assert row['components']['assets']['seed']==int(row['candidate_id'].rsplit('seed',1)[1])
        for note in row.get('candidate_review_notes',[]):
            assert not note.startswith(('Hydrate the paper, PDF, addendum','Materialize mandatory test_data.json'))
    files = sources['files']
    assert len(files)==len({f['path'] for f in files})
    files_by_path = {f['path']: f for f in files}
    checked_bytes = 0
    errors = []
    for entry in files:
        path = repo / entry['path']
        if not path.resolve().is_relative_to((repo / 'benchmarks/cache').resolve()):
            errors.append({'path':entry['path'],'error':'outside cache'})
            continue
        if entry.get('type') == 'symlink':
            if not path.is_symlink() or not path.resolve().is_relative_to((repo / 'benchmarks/cache').resolve()):
                errors.append({'path':entry['path'],'error':'invalid source symlink'})
                continue
            target = os.readlink(path).encode()
            if len(target) != entry['bytes'] or hashlib.sha256(target).hexdigest() != entry['sha256']:
                errors.append({'path':entry['path'],'error':'source symlink target changed'})
            bind_reference(repo, {'root':'repository','path':entry['path']}, files_by_path)
            checked_bytes += len(target)
            continue
        if not path.is_file() or path.is_symlink() or path.stat().st_size != entry['bytes']:
            errors.append({'path':entry['path'],'error':'missing, symlink or size mismatch'})
            continue
        if hashes:
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024*1024),b''):
                    digest.update(block)
            if digest.hexdigest() != entry['sha256']:
                errors.append({'path':entry['path'],'error':'hash mismatch'})
        checked_bytes += entry['bytes']
    for row in rows:
        for component in row['components'].values():
            assert component['status'] in ['present','missing','needs_checking']
            for ref in component.get('evidence',[]):
                if ref.get('root')=='repository' and ref.get('exists') and not (repo / ref['path']).exists():
                    errors.append({'slot':row['slot_id'],'path':ref['path'],'error':'stale component reference'})
    assert not errors, errors
    evidence_count = check_present_components(repo, rows, files_by_path)
    assert checked_bytes == data['totals']['verified_cached_bytes']
    assert len(files)==data['totals']['verified_cached_files']
    return {'status':'passed_for_inventory_only','slots':len(rows),'families':len(Counter(r['family_id'] for r in rows)),'historical_earlier_candidates':historical['historical'],'unchanged_earlier_candidates':historical['active'],'retired_earlier_candidates':historical['retired'],'approved_replacements':sum(r['selection_status']=='approved_replacement' for r in rows),'drafts':len(draft['rows']),'verified_files':len(files),'verified_bytes':checked_bytes,'content_hashes_checked':hashes,'present_component_references_checked':evidence_count,'slot_identities_checked':True,'runtime_qualified':0,'benchmark_runs':0}

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument('--metadata-only',action='store_true')
    args=parser.parse_args()
    result=verify(args.repo.resolve(),not args.metadata_only)
    filename = 'verification-metadata-only.json' if args.metadata_only else 'verification.json'
    (args.repo / 'benchmarks/inventory' / filename).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))
