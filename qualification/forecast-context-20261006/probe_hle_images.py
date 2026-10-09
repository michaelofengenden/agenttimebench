"""Local image transport checks only. No real model or subject task prompt."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import probe_capabilities as probe

REPO = Path('/Users/michaelofengenden/Developer/TimeResearch/AgentTime-v1.1')

if __name__ == '__main__':
    os.umask(0o077)
    rows = json.loads((REPO / 'benchmarks/inventory/tasks.json').read_text())['rows']
    reports = []
    for row in rows:
        if row['slot_id'] not in {'hle-03', 'hle-07', 'hle-09', 'hle-19'}:
            continue
        evidence = [e for e in row['components']['assets']['evidence']
                    if e.get('role') == 'original_question_image']
        if len(evidence) != 1:
            raise RuntimeError('Expected exactly one original question image')
        evidence = evidence[0]
        content = (REPO / evidence['path']).read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != evidence['sha256']:
            raise RuntimeError('Question image differs from the inventory pin')
        probe.png = lambda: content
        report = probe.run(row['slot_id'] + '_image_transport')
        report['image_source_sha256'] = digest
        report['source_path'] = evidence['path']
        report['image_source_bytes'] = len(content)
        reports.append(report)
        print(json.dumps(report), flush=True)
    (probe.OUT / 'hle-image-transport-report.json').write_text(json.dumps({
        'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'offline': True, 'synthetic_forecast_prompt': True,
        'visual_understanding_tested': False, 'variants': reports}, indent=2))
