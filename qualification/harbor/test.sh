#!/bin/bash
set -eu
mkdir -p /logs/verifier
python3 - <<'PY'
import base64, hashlib, json
from pathlib import Path
root = Path('/logs/agent')
events = [json.loads(line) for line in (root/'native-events.jsonl').read_text().splitlines()]
releases = [e for e in events if e['kind']=='prompt_released']
if len(releases) != 1:
    raise ValueError('Expected one prompt release')
if releases[0]['payload']['fixture_mode'] == 'grade_failure':
    raise SystemExit(71)  # Real verifier failure: deliberately emit no reward.
terminals = [e for e in events if e['kind']=='native_terminal']
score = 0
if len(terminals)==1:
    payload = terminals[0]['payload']
    data = base64.b64decode(payload['submission_b64'], validate=True)
    score = int(hashlib.sha256(data).hexdigest()==payload['submission_sha256'] and data==b'42'
                and (root/'sealed-submission.bin').read_bytes()==data)
Path('/logs/verifier/reward.txt').write_text(str(score))
PY
