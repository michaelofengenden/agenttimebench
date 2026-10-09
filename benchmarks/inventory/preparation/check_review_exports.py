"""Bounded checks of review exports, without decoding private task payloads."""
from pathlib import Path
import argparse
import csv
import hashlib
import json
import re

parser=argparse.ArgumentParser()
parser.add_argument('--repo',type=Path,required=True)
args=parser.parse_args()
folder=args.repo/'benchmarks/inventory'
paths=[folder/n for n in ['tasks.json','recommendations.json','index.html','README.md']]
texts={p.name:p.read_text() for p in paths}
prohibited={'answer','answers','question','question_text','rationale','solution','canary','api_key','access_token','refresh_token','authorization','password'}
issues=[]
def walk(value,where):
    if isinstance(value,dict):
        for key,item in value.items():
            if key.lower() in prohibited:issues.append({'artifact':where,'check':'protected field name'})
            walk(item,where)
    elif isinstance(value,list):
        for item in value:walk(item,where)
for name in ['tasks.json','recommendations.json']:walk(json.loads(texts[name]),name)
patterns=[r'\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{24,}|hf_[A-Za-z0-9]{30,}|ghp_[A-Za-z0-9]{30,})\b',r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----']
for name,text in texts.items():
    if any(re.search(pattern,text) for pattern in patterns):issues.append({'artifact':name,'check':'credential-shaped string'})
source=args.repo/'benchmarks/cache/public-metadata-20261002/browse_comp_test_set.csv'
with source.open(encoding='utf-8-sig',newline='') as stream:
    rows=list(csv.DictReader(stream))
private_fields={'problem','question','answer','canary'}
count=0
for row in rows:
    for key,value in row.items():
        if key.lower() in private_fields and value and len(value)>35:
            count+=1
            for name,text in texts.items():
                if value in text:issues.append({'artifact':name,'check':'copied BrowseComp payload'})
if issues:raise ValueError(issues)
result={'status':'passed_bounded_review_export_checks','artifacts':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},'checks':['No raw question, answer, rationale or credential field names in JSON exports','No common credential-shaped values in the four review exports','No copied full BrowseComp private payload values'],'browsecomp_payload_values_checked':count,'limits':'This is not an exhaustive secret or answer detector and does not qualify future subject packages. The private cache remains separate from the served review directory.'}
(folder/'review/export-checks.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'status':result['status'],'artifacts':len(paths),'browsecomp_payload_values_checked':count}))
