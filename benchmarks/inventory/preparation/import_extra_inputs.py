"""Import PINN and normalize the selected PostTrainBench contamination-test data."""
from pathlib import Path
import hashlib
import json
import shutil
from datetime import datetime, timezone
import pyarrow
import pyarrow.parquet as pq

WORK = Path('/private/tmp/agenttime-inventory-20261002/extra-source-inputs')
REPO = Path('/Users/michaelofengenden/Developer/TimeResearch/AgentTime-v1.1')
DEST = REPO / 'benchmarks/cache/extra-inputs-20261002'
receipt = json.loads((WORK / 'extra-source-receipt.json').read_text())
files = []
normalizations = []
for item in receipt['raw_files']:
    origin = Path(item['output_path'])
    relative = origin.relative_to(WORK)
    content = origin.read_bytes()
    assert hashlib.sha256(content).hexdigest() == item['sha256']
    if item.get('expected_sha256'):
        assert item['sha256'] == item['expected_sha256']
    else:
        assert hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest() == item['expected_git_blob_sha1']
    assert len(content) == item['expected_bytes']
    path = DEST / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        assert path.read_bytes() == content
    else:
        shutil.copyfile(origin, path)
    files.append({'path': str(relative), 'bytes': len(content), 'sha256': item['sha256'], 'source_revision': item['source_revision'], 'source_url': item['url']})

for target, columns, count in [('humaneval', {'question': 'prompt', 'answer': 'canonical_solution'}, 164), ('gsm8k', {'question': 'question', 'answer': 'answer'}, 1319)]:
    raw = DEST / 'posttrain' / target / 'raw/test-00000-of-00001.parquet'
    source = pq.read_table(raw, columns=list(columns.values())).to_pylist()
    data = [{out: row[col] for out, col in columns.items()} for row in source]
    assert len(data) == count
    assert all(isinstance(row['question'], str) and row['question'].strip() and isinstance(row['answer'], str) for row in data)
    payload = json.dumps(data, indent=2, ensure_ascii=False).encode()
    assert json.loads(payload) == data
    path = DEST / 'posttrain' / target / 'test_data.json'
    path.write_bytes(payload)
    entry = {'path': str(path.relative_to(DEST)), 'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest(), 'rows': len(data), 'mapping': columns, 'source_sha256': hashlib.sha256(raw.read_bytes()).hexdigest(), 'reader': 'pyarrow==' + pyarrow.__version__, 'order': 'preserved source order'}
    files.append(entry)
    normalizations.append(entry)

for item in receipt['posttrain']:
    if item['task'] not in ['aime2025', 'arenahardwriting']:
        continue
    record = item['normalized_test_data']
    source = Path(record['path'])
    content = source.read_bytes()
    assert hashlib.sha256(content).hexdigest() == record['sha256']
    assert len(json.loads(content)) == record['rows']
    target = DEST / source.relative_to(WORK)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    entry = {'path': str(target.relative_to(DEST)), 'sha256': record['sha256'], 'bytes': len(content), 'rows': record['rows'], 'normalization_receipt': 'origin receipt'}
    files.append(entry)
    normalizations.append(entry)

result = {'source_path': str(DEST), 'origin_receipt_sha256': hashlib.sha256((WORK / 'extra-source-receipt.json').read_bytes()).hexdigest(), 'recorded_at_utc': datetime.now(timezone.utc).isoformat(), 'files': files, 'normalizations': normalizations, 'runtime_validated': False, 'role': 'Private grading inputs. PostTrainBench test_data.json is for contamination checks; this is not the full evaluation or training dataset inventory.', 'remaining': ['Pin and provision Qwen base weights and all native evaluation dependencies.', 'Freeze final judging models and repeated evaluation protocol.', 'Overlay normalized test_data.json into generated verifier tasks without exposing it to the subject.']}
(DEST / 'import-receipt.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({'files': len(files), 'bytes': sum(x['bytes'] for x in files), 'normalized_rows': {x['path']: x['rows'] for x in normalizations}}))
