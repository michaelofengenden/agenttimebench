"""Draft a deterministic text-only sample without reading model outcomes."""
from pathlib import Path
import hashlib
import json
from datetime import datetime, timezone

WORK = Path('/private/tmp/agenttime-inventory-20261002')
ROOT = WORK / 'acquired/tau3-text-fc0055dc4e0a/source'
SEED = 'agenttime-v1.1-tau3-review-20261002-v1'
COMMIT = 'fc0055dc4e0a316c3f83133267fbd6faaa770992'

def file_ref(path):
    return {'relative_path': str(path.relative_to(ROOT)), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size}

def rank(domain, identity):
    return hashlib.sha256((SEED + '\0' + domain + '\0' + identity).encode()).hexdigest()

domains = []
for domain in ['airline', 'retail', 'telecom', 'banking_knowledge']:
    folder = ROOT / 'data/tau2/domains' / domain
    source = folder / 'tasks.json'
    tasks = json.loads(source.read_text())
    assert len({t['id'] for t in tasks}) == len(tasks)
    by_id = {str(t['id']): t for t in tasks}
    split_path = folder / 'split_tasks.json'
    split = 'base' if split_path.exists() else 'all_text_tasks'
    pool = [str(x) for x in json.loads(split_path.read_text())['base']] if split_path.exists() else list(by_id)
    assert len(pool) == len(set(pool)) and set(pool) <= set(by_id)
    ranked = sorted(pool, key=lambda i: (rank(domain, i), i))
    chosen = ranked[:3]
    refs = [file_ref(source)]
    if split_path.exists():
        refs.append(file_ref(split_path))
    candidates = []
    for identity in chosen:
        task = by_id[identity]
        payload = json.dumps(task, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        candidates.append({
            'family_id': 'tau', 'task_id': domain + ':' + identity,
            'identity_kind': 'upstream_domain_and_task_id', 'domain': domain, 'upstream_task_id': identity,
            'mode': 'text', 'source_split': split, 'status': 'draft_for_review', 'admitted': False,
            'selection_rank_sha256': rank(domain, identity), 'canonical_record_sha256': hashlib.sha256(payload).hexdigest(),
            'title': domain.replace('_', ' ').title() + ' service workflow ' + identity,
            'reason': 'Three tasks per text domain, selected with a fixed hash seed for balanced service coverage. No model outcomes used.',
            'source_refs': refs,
            'pending_checks': ['Choose and pin the user simulator, retrieval configuration where applicable, model and seeds.', 'Verify full native tool-state grading and isolate evaluator material from the agent.', 'Audit native turn limits and ending conditions for AgentTime natural runs.'],
        })
    domains.append({'domain': domain, 'pool_count': len(pool), 'split': split, 'ranked_pool_ids': ranked, 'source_refs': refs, 'candidates': candidates})

data = {
    'schema_version': 'agenttime.v1.1.tau3-draft.v1',
    'created_at_utc': datetime.now(timezone.utc).isoformat(), 'selection_status': 'draft_for_review_not_selected',
    'release_choice': 'User selected current corrected tau3 text tasks.',
    'release': 'tau3-bench, repository tag v1.0.1', 'source_repository': 'https://github.com/sierra-research/tau2-bench',
    'source_commit': COMMIT, 'release_url': 'https://github.com/sierra-research/tau2-bench/releases/tag/v1.0.1',
    'method': {'seed': SEED, 'quotas': {d['domain']: 3 for d in domains}, 'ranking': 'SHA256(seed + NUL + domain + NUL + upstream task id), ascending; no alternate seeds tried.', 'pool': 'Official base split for airline, retail and telecom; all published text banking_knowledge tasks. Never voice tasks or mock domain.', 'qualification_status': 'pending'},
    'domains': domains, 'count': 12, 'runtime_qualified': False,
}
(WORK / 'tau3-candidates.json').write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
print(json.dumps({'count': 12, 'candidates': [c['task_id'] for d in domains for c in d['candidates']]}))
