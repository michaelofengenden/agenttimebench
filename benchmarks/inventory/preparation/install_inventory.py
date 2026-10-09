"""Save the completed task-source audit and its reproducible review artifacts."""
from pathlib import Path
import copy
import hashlib
import json
import shutil
from datetime import datetime, timezone

WORK = Path('/private/tmp/agenttime-inventory-20261002')
REPO = Path('/Users/michaelofengenden/Developer/TimeResearch/AgentTime-v1.1')
OUT = REPO / 'benchmarks/inventory'
EVIDENCE = OUT / 'evidence'
EVIDENCE.mkdir(parents=True, exist_ok=True)

def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')

def copy_evidence(relative, name=None):
    origin = WORK / relative
    target = EVIDENCE / (name or relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(origin, target)

for name in ['selection-map.json', 'local-sources.json', 'component-evidence.json', 'reduced-candidates.json', 'addition-candidates.json', 'tau3-candidates.json', 'tau-tags.json']:
    copy_evidence(name)
copy_evidence('new-candidates/recommendations.json', 'new-candidates.json')
for name in ['new-candidates/verification.json', 'new-candidates/download-receipts.json', 'osworld21-check/osworld21-task-definition-check.json', 'osworld21-check/authenticated-mapping.json', 'hle-diamond-draft/draft-selection.json', 'hle-diamond-draft/selection-ledger.json', 'hle-diamond-draft/verification-receipt.json', 'extra-source-inputs/extra-source-receipt.json', 'extra-source-inputs/pinn-figures-receipt.json', 'access-check/access-receipt.json']:
    copy_evidence(name)

# Original upstream/selection snapshots remain unchanged; retain their exact bytes.
selection = json.loads((WORK / 'selection-map.json').read_text())
for key in ['suite', 'selected_candidates', 'v1_roster', 'selected_plan']:
    entry = selection['sources'][key]
    source = Path(entry['path'])
    data = source.read_bytes()
    if key == 'suite' and (EVIDENCE / 'source-snapshots/suite.json').exists():
        continue
    assert hashlib.sha256(data).hexdigest() == entry['sha256'], key
    target = EVIDENCE / 'source-snapshots' / (key + '.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)

# Import the separately verified figures without altering immutable earlier receipts.
figure_receipt = json.loads((WORK / 'extra-source-inputs/pinn-figures-receipt.json').read_text())
figure_root = REPO / 'benchmarks/cache/extra-inputs-20261002/pinn'
figure_files = []
for asset in figure_receipt['assets']:
    data = Path(asset['output_path']).read_bytes()
    assert hashlib.sha256(data).hexdigest() == asset['expected_sha256']
    dest = figure_root / 'assets' / asset['filename']
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    figure_files.append({'path': str(dest.relative_to(figure_root)), 'bytes': len(data), 'sha256': asset['expected_sha256']})
save(figure_root / 'figure-import-receipt.json', {'source_path': str(figure_root), 'files': figure_files, 'origin_receipt_sha256': hashlib.sha256((WORK / 'extra-source-inputs/pinn-figures-receipt.json').read_bytes()).hexdigest(), 'all_markdown_references_resolved': True, 'runtime_validated': False})

# Normalize receipt formats into one file manifest. Keep all original receipts too.
cache = REPO / 'benchmarks/cache'
manifest = {}
receipts = []
def include_receipt(path):
    relative = str(path.relative_to(REPO))
    label = relative.removeprefix('benchmarks/cache/').replace('/', '__')
    dest = EVIDENCE / 'imports' / label
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, dest)
    receipts.append({'original_path': relative, 'saved_path': str(dest.relative_to(OUT)), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    return relative
def add_file(path, size, digest, receipt):
    path = str(path)
    if path in manifest:
        assert manifest[path]['sha256'] == digest and manifest[path]['bytes'] == size
        return
    assert (REPO / path).is_file(), path
    assert (REPO / path).stat().st_size == size, path
    manifest[path] = {'path': path, 'bytes': size, 'sha256': digest, 'receipt': receipt}

p = cache / 'v1-inputs-20261002/import-receipt.json'
label = include_receipt(p)
for group in json.loads(p.read_text())['copies']:
    for path, item in group['files'].items():
        add_file('benchmarks/cache/v1-inputs-20261002/' + group['relative_path'] + '/' + path, item['size'], item['sha256'], label)
p = cache / 'verified-assets-receipt-20261002.json'
label = include_receipt(p)
for group in json.loads(p.read_text())['imports']:
    for item in group['files']:
        add_file(item['path'], item['bytes'], item['sha256'], label)
for p in sorted((cache / 'upstream-20261002').glob('*/receipt.json')):
    data = json.loads(p.read_text()); label = include_receipt(p)
    source = Path(data['source_path']).relative_to(REPO)
    for item in data['files']:
        add_file(source / item['path'], item['bytes'], item['sha256'], label)
    add_file(Path(data['archive_path']).relative_to(REPO), data['archive_bytes'], data['archive_sha256'], label)
for p in [cache / 'public-metadata-20261002/import-receipt.json', cache / 'gated-inputs-20261002/osworld21-tasks/import-receipt.json', cache / 'gated-inputs-20261002/hle-diamond/import-receipt.json', cache / 'pptarena-grader-20261002/import-receipt.json', cache / 'extra-inputs-20261002/import-receipt.json', figure_root / 'figure-import-receipt.json']:
    data = json.loads(p.read_text()); label = include_receipt(p)
    root = Path(data['source_path']).relative_to(REPO) if data.get('source_path') else None
    for item in data['files']:
        path = root / item['path'] if root else item['path']
        add_file(path, item['bytes'], item['sha256'], label)
save(EVIDENCE / 'source-manifest.json', {'schema_version': 1, 'scope': 'Actual copied files in the private preparation cache; source presence only.', 'recorded_at_utc': datetime.now(timezone.utc).isoformat(), 'counts_include': 'Archives, extracted code and overlapping clean reference exports; not unique useful-task payload bytes.', 'receipts': receipts, 'files': sorted(manifest.values(), key=lambda x:x['path'])})

# Build normalized HLE and OSWorld records from their independent source reviews.
resolved = []
def ev(path, **kwargs):
    entry = manifest[str(path)]
    return {'root':'repository', 'path':str(path), 'exists':True, 'bytes':entry['bytes'], 'sha256':entry['sha256'], **kwargs}
def comp(status, refs, gap=None):
    return {'status':status,'evidence':refs,'remaining_gap':gap}
hle = json.loads((WORK / 'hle-diamond-draft/draft-selection.json').read_text())
for c in hle['selected']:
    path = 'benchmarks/cache/gated-inputs-20261002/hle-diamond/data/test-00000-of-00001.parquet'
    ref = ev(path, record_id=c['id'], question_sha256=c['question_sha256'])
    comps = {'prompt':comp('present',[ref]), 'assets':comp('present',[ref], 'Image content is present in the dataset; image decoding and rendering need checking.' if c['image_present'] else 'Verify text and answer-choice formatting in the final prompt.'), 'environment':comp('needs_checking',[], 'Qualify the HLE model input route, dependencies and image handling where required.'), 'grader':comp('needs_checking',[ref], 'Pin the official grading policy and validate answer parsing/judging; keep references outside subject input.')}
    resolved.append({'family':'hle','candidate':c['id'],'decision':'draft_for_review','label':c['partition'].title() + ' · ' + c['raw_subject'],'comps':comps,'pin':hle['source']['revision'],'reason':c['eligibility_rationale'],'source_locator':c['source_locator'],'hle_partition':c['partition'],'image_present':c['image_present'],'screening_status':'draft_screened_prefix_only','evidence_file':'evidence/hle-diamond-draft/draft-selection.json'})
osw = json.loads((WORK / 'osworld21-check/authenticated-mapping.json').read_text())
for c in osw['tasks']:
    path = 'benchmarks/cache/gated-inputs-20261002/osworld21-tasks/task_' + c['task_id'] + '.py'
    ref = ev(path, source_components=c['component_evidence'])
    gaps = c['task_specific_qualification_gaps']
    comps = {'prompt':comp('present',[ref]), 'assets':comp('missing',[ref], 'The source defines setup and asset references, but matching task assets and desktop initial state are not yet materialized.'), 'environment':comp('needs_checking',[ref], 'Acquire the matching OSWorld2.1 VM, prepare task state and qualify setup. ' + ' '.join(gaps)), 'grader':comp('present',[ref], 'Run full native evaluation including visual and judge-dependent components where required. ' + ' '.join(gaps))}
    resolved.append({'family':'osworld','candidate':c['task_id'],'decision':'retained_version_mapping' if c['role']=='retained_candidate' else 'draft_for_review','label':c['label'],'comps':comps,'pin':c['source']['revision'],'reason':c['purpose'],'identity':'verified_upstream_class_id','evidence_file':'evidence/osworld21-check/authenticated-mapping.json'})
save(EVIDENCE / 'resolved-candidates.json', {'status':'inventory_evidence_not_qualification','rows':resolved})

for name in ['build_inventory.py','verify_inventory.py','report-template.html']:
    shutil.copyfile(WORK / name, OUT / name)
preparation = OUT / 'preparation'
preparation.mkdir(exist_ok=True)
for name in ['import_local_inputs.py','import_verified_assets.py','acquire_archives.py','finish_source_import.py','draft_tau.py','check_existing_dataset_access.py','fetch_gated_sources.py','import_supplemental_sources.py','import_extra_inputs.py','install_inventory.py']:
    shutil.copyfile(WORK / name, preparation / name)
for source, dest in [('hle-diamond-draft/select_hle_diamond.py','select_hle_diamond.py'),('extra-source-inputs/normalize-json.py','normalize_posttrain_json.py')]:
    shutil.copyfile(WORK / source, preparation / dest)

config = REPO / 'configs/suite.json'
suite = json.loads(config.read_text())
tau = next(f for f in suite['families'] if f['id']=='tau')
tau.update({'name':'τ³-bench, text','benchmark_release':'v1.0.1','source_repository':'https://github.com/sierra-research/tau2-bench','resolved_code_commit':'fc0055dc4e0a316c3f83133267fbd6faaa770992','action':'Use corrected τ³ text tasks; 12 exact IDs are drafted for review. Native qualification remains pending.'})
save(config,suite)
mix = REPO / 'docs/TASK_MIX.md'
text = mix.read_text().replace('| τ-bench, text | 12 | New text-only family; select and audit all 12 task IDs. |', '| τ³-bench, text | 12 | Use corrected v1.0.1 text tasks; review the 12 drafted IDs. |')
anchor = '\n## Source inventory and draft IDs\n'
if anchor not in text:
    text += anchor + '\nThe [220-place source inventory](../benchmarks/inventory/README.md) records actual local files, exact draft identities and remaining preparation for every family. The [searchable review](../benchmarks/inventory/index.html) keeps drafts separate from earlier choices. These files do not create an executable roster.\n\nThe current τ³ text release is pinned at v1.0.1, including its corrected banking grader. The draft proposes three tasks each from airline, retail, telecom and banking knowledge.\n'
mix.write_text(text)
for path, addition in [(REPO/'benchmarks/README.md','\nThe [task inventory](inventory/README.md) and [searchable review](inventory/index.html) cover all 220 places, with draft IDs, verified cache receipts and per-component gaps.\n'),(REPO/'README.md','\nThe [220-task preparation inventory](benchmarks/inventory/README.md) shows the files now present, draft task IDs and remaining work. [Open the searchable review](benchmarks/inventory/index.html).\n')]:
    text=path.read_text()
    if addition.strip() not in text:
        path.write_text(text+addition)

notebook = {'nbformat':4,'nbformat_minor':5,'metadata':{'kernelspec':{'display_name':'Python3','language':'python','name':'python3'}},'cells':[{'cell_type':'markdown','metadata':{},'source':['# AgentTime v1.1 inventory checks\n','One row per allocated task. This notebook checks source inventory integrity only. It never starts benchmark or model runs. Run from the inventory directory with the local cache available.']},{'cell_type':'code','metadata':{},'execution_count':None,'outputs':[],'source':['from pathlib import Path\n','import json, importlib.util\n','folder = Path.cwd()\n','if not (folder / "tasks.json").exists():\n','    folder = folder / "benchmarks/inventory"\n','spec = importlib.util.spec_from_file_location("inventory_verify", folder / "verify_inventory.py")\n','module = importlib.util.module_from_spec(spec)\n','spec.loader.exec_module(module)\n','result = module.verify(folder.resolve().parents[1], hashes=True)\n','result']},{'cell_type':'code','metadata':{},'execution_count':None,'outputs':[],'source':['data = json.loads((folder / "tasks.json").read_text())\n','[(f["name"], f["places"], f["selection"], f["components"]) for f in data["families"]']} ]}
save(OUT / 'inventory-checks.ipynb',notebook)
print(json.dumps({'files_in_manifest':len(manifest),'bytes_in_manifest':sum(x['bytes'] for x in manifest.values()),'resolved_osworld_and_hle_rows':len(resolved),'output':str(OUT)}))
