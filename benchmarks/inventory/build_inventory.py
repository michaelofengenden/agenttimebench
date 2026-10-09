"""Build the review-only task inventory from saved evidence, without running tasks."""
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone
import argparse
import copy
import hashlib
import html
import json
from evidence_checks import bind_reference, check_present_components, check_slot_identities, check_selection_transition, check_selection_history, read_selection_history


def apply_recorded_replacements(rows, history, evidence):
    """Replace exact retired identities using source rows bound by a user receipt."""
    current = list(rows)
    for change in history:
        reference = change.get('replacement_rows')
        if reference is None:
            continue
        path = evidence / reference['path']
        if not path.resolve().is_relative_to(evidence.resolve()):
            raise ValueError('Replacement row evidence is outside its evidence directory')
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != reference['sha256']:
            raise ValueError('Replacement row evidence differs from its decision receipt')
        replacement = json.loads(payload)['rows']
        check_slot_identities(current, change['before'])
        check_slot_identities(replacement, change['added'])
        for row in replacement:
            if row.get('selection_status') != 'approved_replacement':
                raise ValueError('Replacement rows require an approved selection status')
            if row.get('runtime_qualified') is not False or row.get('admitted') is not False:
                raise ValueError('A replacement selection cannot grant runtime admission')
        removed = {r['slot_id'] for r in change['removed']}
        after = [r for r in current if r['slot_id'] not in removed] + copy.deepcopy(replacement)
        check_selection_transition(after, change)
        identities = [(r['family_id'], r['candidate_id']) for r in after]
        if len(identities) != len(set(identities)):
            raise ValueError('A replacement decision duplicates an active task identity')
        current = after
    return current


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    repo = args.repo.resolve()
    out = repo / 'benchmarks/inventory'
    if Path(__file__).resolve() != (out / 'build_inventory.py').resolve():
        raise ValueError('Run the builder inside the target repository so its provenance matches the executed code')
    evidence = out / 'evidence'
    load = lambda name: json.loads((evidence / name).read_text())
    suite = json.loads((repo / 'configs/suite.json').read_text())
    selected = load('selection-map.json')
    components = {(x['family_id'], x['task_id']): x for x in load('component-evidence.json')['tasks']}
    reduced = {x['family_id']: x for x in load('reduced-candidates.json')['families']}
    additions = load('addition-candidates.json')['candidates']
    public = {x['family_id']: x for x in load('new-candidates.json')['families']}
    tau = load('tau3-candidates.json')
    extended = load('resolved-candidates.json')
    sources = load('source-manifest.json')
    by_path = {x['path']: x for x in sources['files']}
    families = {f['id']: f for f in suite['families'] if f['count']}
    old = {f['family_id']: f for f in selected['families']}

    def ev(path, role=None, **extra):
        path = str(path)
        file = repo / path
        result = {'root': 'repository', 'path': path, 'exists': file.exists(), **extra}
        if path in by_path:
            result.update({k: by_path[path][k] for k in ['bytes', 'sha256']})
        elif file.is_file():
            result.update({'bytes': file.stat().st_size, 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()})
        if role:
            result['role'] = role
        return result

    def component(status, paths=(), gap=None, **extra):
        return {'status': status, 'evidence': [ev(p) if isinstance(p, str) else p for p in paths], 'remaining_gap': gap, **extra}

    def environment(paths=(), gap='Build or acquire the pinned environment, verify initial state and qualify the native execution boundary.'):
        return component('needs_checking', paths, gap, installed_environment_checked=False)

    def natural():
        return {'status': 'needs_checking', 'remaining_gap': 'Freeze the natural task prompt and audit native timers, turn limits and endpoints before execution.'}

    def defaults():
        return {k: component('missing', gap='Full runnable task package has not been acquired.') for k in ['prompt', 'assets', 'environment', 'grader']}

    rows = []
    def add(family, candidate, decision, label=None, comps=None, pin=None, reason=None, identity='upstream_task_id', **extra):
        assert family in families
        assert not set(extra) & {'slot_id', 'family_id', 'family_name', 'candidate_id', 'selection_status', 'source_pin', 'runtime_qualified', 'admitted', 'components'}
        row = {
            'slot_id': family + '-' + str(1 + sum(r['family_id'] == family for r in rows)).zfill(2),
            'family_id': family, 'family_name': families[family]['name'],
            'candidate_id': candidate, 'identity_kind': identity,
            'selection_status': decision, 'title': label or candidate or 'Identity unresolved',
            'source_pin': pin, 'recommendation_reason': reason,
            'components': copy.deepcopy(comps or defaults()),
            'natural_prompt': natural(), 'runtime_qualified': False, 'admitted': False,
            **extra,
        }
        row['gaps'] = list(dict.fromkeys(v['remaining_gap'] for v in row['components'].values() if v.get('remaining_gap')))
        rows.append(row)
        return row

    for f in selected['families']:
        for c in f['known_candidates']:
            proof = components[(f['family_id'], c['task_id'])]
            add(f['family_id'], c['task_id'], 'earlier_candidate', c.get('label'), proof['components'], proof['source_pin'], provenance=c['provenance'], replaces_task_id=c.get('replaces_task_id'))
    assert len(rows) == 100
    for family, f in reduced.items():
        pool = {c['task_id']: c for c in f['candidate_pool']}
        for identity in f['recommended_ids']:
            proof = components[(family, identity)]
            c = pool[identity]
            add(family, identity, 'draft_for_review', c.get('label'), proof['components'], proof['source_pin'], 'Fixed-seed sample within descriptive groups from the old cohort. This sampling step uses no outcomes; it does not establish how the historical pool was formed.', selection_group=c.get('draft_group'))

    base = 'benchmarks/cache/v1-inputs-20261002'
    ptb = 'benchmarks/cache/upstream-20261002/posttrain12-code-c5b4e9795e3a/source'
    extra = 'benchmarks/cache/extra-inputs-20261002'
    for c in additions:
        family, identity = c['family_id'], c['task_id']
        if family == 'deepswe':
            root = f'{base}/deepswe-v1-1-current-task-schema-1-3/task/tasks/{identity}'
            paths = [str(p.relative_to(repo)) for p in (repo / root).rglob('*') if p.is_file()]
            instruction = [p for p in paths if p.endswith('/instruction.md')]
            tests = [p for p in paths if '/tests/' in p]
            config = [p for p in paths if p.endswith(('/task.toml', '/Dockerfile'))]
            comps = {'prompt': component('present' if instruction else 'missing', instruction),
                     'assets': component('missing', config, 'The task definition is present, but the pinned target repository checkout and its dependencies have not been materialized.'),
                     'environment': environment(config),
                     'grader': component('present' if tests else 'missing', tests, 'Qualify native positive and adversarial incorrect submissions; list screening alone does not prove validity.')}
        elif family == 'paper':
            paper = f'{base}/paperbench/code/project/paperbench'
            comps = {'prompt': component('present', [f'{paper}/paperbench/solvers/basicagent/prompts/instructions_iterative.txt', f'{extra}/pinn/paper.md', f'{extra}/pinn/addendum.md'], 'Adapt the original duration wording while retaining full executed reproduction.'),
                     'assets': component('needs_checking', [f'{extra}/pinn/paper.pdf', f'{extra}/pinn/paper.md'], 'Bind hydrated paper figures and provision external experiment data in the task package.'),
                     'environment': environment([f'{paper}/data/papers/pinn/config.yaml']),
                     'grader': component('present', [f'{paper}/data/papers/pinn/rubric.json'], 'Preserve the full reproduction stage and freeze the native rubric judging protocol.')}
        elif family == 'posttrain':
            target = c['parameter_mapping']['adapter_benchmark_id']
            comps = {'prompt': component('present', [f'{ptb}/src/eval/general/prompt.txt', f'{ptb}/src/eval/tasks/{target}/benchmark.txt']),
                     'assets': component('needs_checking', [f'{extra}/posttrain/{target}/test_data.json'], 'Contamination-test data is present; base weights and the complete native train/evaluation resources still need provisioning.'),
                     'environment': environment([f'{ptb}/src/harbor_adapter/template/environment/Dockerfile'], 'Provision GPU resources and pinned images; remove effective natural-work cutoffs and verify full capture.'),
                     'grader': component('needs_checking', [f'{ptb}/src/eval/tasks/{target}/evaluate.py'], 'Freeze final judge models, repeated evaluation seeds, score handling and native controls.')}
        elif family == 'yc':
            template = components[('yc', 'yc-bench.default.seed1')]['components']
            comps = copy.deepcopy(template)
            comps['assets']['status'] = 'needs_checking'
            comps['assets']['seed'] = 4
            comps['assets']['remaining_gap'] = 'Generate and verify the seed-4 market-task instance. Employees and clients retain internal seed 1; distinct-world verification remains pending.'
        else:
            raise AssertionError(family)
        notes = list(c['pending_checks'])
        if family == 'paper':
            notes = [n for n in notes if not n.startswith('Hydrate the paper, PDF, addendum')]
            notes.append('Paper, PDF, addendum and all referenced figures have been hydrated and hash-verified. Bind those files into the final task package and provision external experiment data.')
            comps['assets']['evidence'].append(ev(f'{extra}/pinn/figure-import-receipt.json'))
        if family == 'posttrain':
            notes = [n for n in notes if not n.startswith('Materialize mandatory test_data.json')]
            notes.append('Normalized test_data.json is present in the private cache. Bind it only to verifier-side contamination checks and finish the separate evaluation-resource inventory.')
        add(family, identity, 'draft_for_review', c['title'], comps, c['source_commit'], c['reason'], source_url=c['canonical_url'], candidate_review_notes=notes)

    csv = 'benchmarks/cache/public-metadata-20261002/browse_comp_test_set.csv'
    evaluator = 'benchmarks/cache/public-metadata-20261002/browsecomp_eval-pinned.py'
    for c in public['browsecomp']['candidates']:
        record = ev(csv, data_row_index_zero_based=c['data_row_index_zero_based'], record_sha256=c['canonical_encrypted_record_sha256'])
        comps = {'prompt': component('present', [record], 'Official encrypted prompt record is present. Decode in preparation and audit domain eligibility.'),
                 'assets': component('needs_checking', [record], 'Verify live web access and record the evaluated web conditions; the external web is not a frozen local asset.'),
                 'environment': environment(),
                 'grader': component('present', [evaluator, record], 'Freeze the model grader and keep answer records outside the subject environment.')}
        add('browsecomp', c['review_candidate_key'], 'draft_for_review', c['source_topic'] + ' web research', comps, public['browsecomp']['source']['dataset_sha256'], 'Fixed-seed topic-stratified sample; no difficulty or model outcomes used.', identity='csv_sha256_and_zero_based_row', source_locator=c)

    tau_root = 'benchmarks/cache/upstream-20261002/tau3-text-fc0055dc4e0a/source'
    for domain in (tau['domains'] if 'tau' in families else []):
        paths = [tau_root + '/' + x['relative_path'] for x in domain['source_refs']]
        domain_root = repo / tau_root / 'data/tau2/domains' / domain['domain']
        db_paths = [str(p.relative_to(repo)) for p in sorted(domain_root.glob('*db.*')) if p.is_file()]
        policy_paths = [str(p.relative_to(repo)) for p in sorted(domain_root.glob('*policy*.md')) if p.is_file()]
        for c in domain['candidates']:
            record = ev(paths[0], record_id=c['upstream_task_id'], record_sha256=c['canonical_record_sha256'])
            comps = {'prompt': component('present', [record] + policy_paths, 'Native policy and simulated-user task record are present; bind the agent-visible policy without exposing private user goals or evaluation criteria.'),
                     'assets': component('present', [record] + db_paths, 'Pin the initial database, user simulator and banking retrieval configuration during packaging.'),
                     'environment': environment(paths),
                     'grader': component('present', [record, tau_root + '/src/tau2/evaluator'], 'Qualify native state/action/communication grading; freeze judging dependencies and effective turn limits.')}
            title = c['title']
            if c['domain'] == 'telecom':
                problem = 'Restore MMS' if '[mms_issue]' in c['upstream_task_id'] else 'Restore mobile service'
                persona = 'Easy user' if '[PERSONA:Easy]' in c['upstream_task_id'] else 'Hard user'
                title = f'Telecom: {problem} · {persona}'
            add('tau', c['task_id'], 'draft_for_review', title, comps, tau['source_commit'], c['reason'], identity=c['identity_kind'], source_locator={'domain': c['domain'], 'task_id': c['upstream_task_id'], 'split': c['source_split'], 'mode': 'text'})

    for c in (public['weirdml']['candidates'] if 'weirdml' in families else []):
        comps = defaults()
        for v in comps.values():
            v['remaining_gap'] = 'Published v3 metadata identifies this task, but its full runnable prompt, assets, environment and grader package has not been located or verified.'
        add('weirdml', c['published_task_key'], 'draft_for_review', c['display_name'], comps, public['weirdml']['source'].get('website_commit', public['weirdml']['source']), c['selection_reason'], identity='published_results_key_only', published_metadata=c)

    for c in extended['rows']:
        add(**c)

    automation_path = evidence / 'automationbench-candidates.json'
    if 'automation' in families:
        automation = json.loads(automation_path.read_text())
        root = automation['source_cache']
        for c in automation['candidates']:
            record = ev(c['record_path'], record_id=c['task_id'], record_sha256=c['record_sha256'])
            comps = {
                'prompt': component('present', [record], 'Exact removal of the native turn-budget wording is tested; qualify prompt delivery through each real agent harness.'),
                'assets': component('present', [record], 'Private simulator state is cached. Keep it outside the subject container and qualify isolated per-attempt worlds.'),
                'environment': environment([root + '/pyproject.toml', root + '/uv.lock'], 'Standalone API/MCP checks pass. Real agent sessions, controller integration and full native archives remain pending.'),
                'grader': component('present', [record, root + '/automationbench/rubric'], 'Native API witnesses and adversarial controls are recorded. Check exact terminal-state capture and grader equivalence through each live harness before admission.'),
            }
            add('automation', c['task_id'], 'draft_for_review', c['title'], comps,
                automation['source_commit'], c['reason'], source_url=automation['source_url'],
                candidate_review_notes=c['review_notes'])

    # New acquisitions resolve source gaps but never confer runtime admission.
    for row in rows:
        if row['family_id'] == 'pptarena':
            row['components']['grader']['evidence'] = [ref for ref in row['components']['grader']['evidence'] if ref.get('exists')]
            row['components']['grader']['evidence'] += [ev('benchmarks/cache/pptarena-grader-20261002/agent_bench/judge_predictions.py'), ev('benchmarks/cache/pptarena-grader-20261002/src/llm/judge.py')]
            row['components']['grader']['status'] = 'present'
            row['components']['grader']['remaining_gap'] = 'Native judging source is now present. Freeze rendering dependencies, the judge model and rubric handling; run positive and negative controls.'
        row['gaps'] = list(dict.fromkeys(v['remaining_gap'] for v in row['components'].values() if v.get('remaining_gap')))

    # Dated acquisition receipts update source components, never task selection.
    followup_path = evidence / 'asset-followup.json'
    followup = json.loads(followup_path.read_text()) if followup_path.exists() else {}
    origin, history = read_selection_history(evidence)
    rows = apply_recorded_replacements(rows, history, evidence)
    retired = check_selection_history(rows, history, origin_rows=origin)
    by_slot = {r['slot_id']: r for r in rows}
    seen_updates = set()
    for update in followup.get('rows', []):
        slot = update['slot_id']
        assert slot not in seen_updates, ('duplicate acquisition update', slot)
        seen_updates.add(slot)
        if slot in retired:
            assert update['candidate_id'] == retired[slot], ('retired task identity changed', slot)
            continue
        row = by_slot[slot]
        assert update['candidate_id'] == row['candidate_id'], ('task identity changed', slot)
        for name, value in update.get('component_updates', {}).items():
            assert name in {'prompt', 'assets', 'environment', 'grader'}
            assert value['status'] in {'present', 'missing', 'needs_checking'}
            row['components'][name] = copy.deepcopy(value)
        prefixes = tuple(update.get('review_notes_to_remove_prefixes', []))
        if prefixes and 'candidate_review_notes' in row:
            row['candidate_review_notes'] = [n for n in row['candidate_review_notes'] if not n.startswith(prefixes)]
        if update.get('acquisition_notes'):
            row['acquisition_notes'] = update['acquisition_notes']
        row['gaps'] = list(dict.fromkeys(v['remaining_gap'] for v in row['components'].values() if v.get('remaining_gap')))

    # Preserve suite order and ensure one slot per allocated place.
    order = {f: i for i, f in enumerate(families)}
    rows.sort(key=lambda x: (order[x['family_id']], x['slot_id']))
    check_slot_identities(rows, load('slot-identities.json')['rows'])
    expected_slots = sum(f['count'] for f in families.values())
    assert len(rows) == expected_slots == len({r['slot_id'] for r in rows})
    counts = Counter(r['family_id'] for r in rows)
    assert counts == {f: x['count'] for f, x in families.items()}, counts
    identities = [(r['family_id'], r['candidate_id']) for r in rows if r['candidate_id']]
    assert len(identities) == len(set(identities))
    assert all(not r['runtime_qualified'] and not r['admitted'] for r in rows)
    invalid = []
    for row in rows:
        for name, comp in row['components'].items():
            assert comp['status'] in ['present', 'missing', 'needs_checking']
            for ref in comp.get('evidence', []):
                ref.setdefault('visibility', 'preparation_only')
                if ref.get('root') == 'repository' and ref.get('exists'):
                    ref.update(bind_reference(repo, ref, by_path))
                if ref.get('root') == 'repository' and ref.get('exists') and not (repo / ref['path']).exists():
                    invalid.append([row['slot_id'], name, ref['path']])
            if comp['status'] == 'present':
                assert any(e.get('exists') for e in comp['evidence']), (row['slot_id'], name)
    assert not invalid, invalid
    check_present_components(repo, rows, by_path)

    build_inputs = {}
    for path in sorted(evidence.rglob('*.json')):
        if 'imports' in path.relative_to(evidence).parts:
            continue
        build_inputs[str(path.relative_to(repo))] = hashlib.sha256(path.read_bytes()).hexdigest()
    for path in [repo / 'configs/suite.json', Path(__file__).resolve(), out / 'report-template.html', out / 'evidence_checks.py', out / 'verify_inventory.py']:
        build_inputs[str(path.relative_to(repo))] = hashlib.sha256(path.read_bytes()).hexdigest()

    summaries = []
    for family, f in families.items():
        group = [r for r in rows if r['family_id'] == family]
        summaries.append({'id': family, 'name': f['name'], 'places': len(group), 'selection': dict(Counter(r['selection_status'] for r in group)), 'components': {k: dict(Counter(r['components'][k]['status'] for r in group)) for k in ['prompt', 'assets', 'environment', 'grader']}})
    result = {
        'schema_version': 'agenttime.v1.1.task-preparation-inventory.v1',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'scope': 'One row per allocated study task, including draft identities. Preparation evidence only; never use this file to dispatch runs.',
        'status_definitions': {'present': 'Source bytes or the exact source record exist locally. This does not mean an executable environment or a validated task.', 'needs_checking': 'Partial source is present, or binding, installation, adaptation or qualification remains.', 'missing': 'Required bytes were not found in the inspected v1.1 cache.'},
        'selection_definitions': {'earlier_candidate': 'Named in an earlier proposal. This does not establish individual user approval or task qualification.', 'retained_version_mapping': 'An earlier OSWorld candidate mapped to the selected 2.1 source; task qualification remains.', 'draft_for_review': 'Suggested exact identity or published task key, not approved as a task selection.', 'approved_replacement': 'Selected by the user in a recorded replacement decision; runtime qualification and admission remain pending.', 'unresolved': 'Allocation only; exact identity remains unresolved.'},
        'totals': {'slots': len(rows), 'families': len(families), 'selection': dict(Counter(r['selection_status'] for r in rows)), 'runtime_qualified': 0, 'admitted': 0, 'verified_cached_files': len(sources['files']), 'verified_cached_bytes': sum(f['bytes'] for f in sources['files']), 'components': {k: dict(Counter(r['components'][k]['status'] for r in rows)) for k in ['prompt', 'assets', 'environment', 'grader']}},
        'families': summaries, 'rows': rows,
        'source_receipt': 'evidence/source-manifest.json',
        'build_input_sha256': build_inputs,
        'privacy': 'Task answers, rationales, full gated question text and credentials are omitted from this review artifact. Mixed private caches must never be mounted wholesale into subject sessions.',
        'preparation_update': {**followup.get('summary', {}), **(load('revision-20261007.json').get('summary', {}) if (evidence / 'revision-20261007.json').exists() else {})},
    }
    (out / 'tasks.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    draft = {'status': 'draft_for_user_review', 'rows': [{k: r[k] for k in ['slot_id','family_id','candidate_id','title','identity_kind','source_pin','recommendation_reason','gaps']} for r in rows if r['selection_status'] == 'draft_for_review']}
    (out / 'recommendations.json').write_text(json.dumps(draft, ensure_ascii=False, indent=2) + '\n')
    display_fields = ['slot_id','family_id','family_name','candidate_id','title','selection_status','identity_kind','recommendation_reason','source_pin','gaps','provenance','replaces_task_id','replaces_slot_id','selection_group','candidate_review_notes','acquisition_notes']
    display = {**result, 'rows': [{k: r[k] for k in display_fields if k in r} | {'components': {k: {'status':v['status'], 'remaining_gap':v.get('remaining_gap')} for k,v in r['components'].items()}} for r in rows]}
    template = (out / 'report-template.html').read_text()
    payload = json.dumps(display, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    (out / 'index.html').write_text(template.replace('__INVENTORY_JSON__', payload))
    write_readme(out, result)
    print(json.dumps(result['totals']))


def write_readme(out, result):
    t = result['totals']
    lines = ['# AgentTime v1.1 task inventory', '', f"This is a preparation inventory for all {t['slots']} places, with {t['selection'].get('approved_replacement', 0)} approved replacements kept separate from earlier choices and draft IDs. No study runs were launched.", '', f"[Open the searchable review](index.html) · [All {t['slots']} records](tasks.json) · [Draft recommendations](recommendations.json) · [Source manifest](evidence/source-manifest.json)", '', f"The cache contains {t['verified_cached_files']:,} verified files ({t['verified_cached_bytes']/1e9:.2f} GB, including source archives and overlapping reference exports). These are actual local copies. They are not {t['slots']} qualified execution packages.", '', '| Family | Places | Earlier / mapped | Draft for review | Approved replacements | Original prompt source present |', '| --- | ---: | ---: | ---: | ---: | ---: |']
    for f in result['families']:
        s = f['selection']
        lines.append(f"| {f['name']} | {f['places']} | {s.get('earlier_candidate',0)+s.get('retained_version_mapping',0)} | {s.get('draft_for_review',0)} | {s.get('approved_replacement',0)} | {f['components']['prompt'].get('present',0)} |")
    update = result.get('preparation_update', {})
    lines += ['', '## Selected allocation', '', update.get('selection_decision', 'See the selected task mix.'), '', '## Latest asset preparation', '']
    lines += ['- ' + text for text in update.get('completed', ['See each task for current component evidence.'])]
    lines += ['', '## What still needs work', '']
    lines += ['- ' + text for text in update.get('remaining', ['Every family needs its natural prompt, environment and grading qualified.'])]
    if update.get('review'):
        lines += ['', '## Independent review', '', update['review']]
    lines += ['', '## Your decisions', ''] + ['- ' + text for text in update.get('user_decisions', ['Review the proposed exact task choices.'])]
    lines += ['', '## Review and reproduce', '', 'Use the search page to filter suggestions or missing components. Open a task for its reason and remaining work. Recommendations were not promoted into an executable roster. Dated selection-change records preserve all retired identities. The 7 October receipt replaces five tasks on fresh slots and holds the other 195 task identities constant. The original 100 earlier candidates remain in historical evidence; 95 remain active after the five recorded retirements.', '', 'The evidence folder preserves selection methods, source pins, access results and file hashes. Early access failures and acquisition gaps are historical snapshots; the latest receipts and task rows reflect subsequent source preparation.', '', 'Run `python benchmarks/inventory/build_inventory.py` to rebuild the report from saved evidence and the local cache. Run `python benchmarks/inventory/verify_inventory.py` for count, identity, reference and file-integrity checks. The companion notebook exposes the same checks without executing any benchmark.', '', 'Keep `benchmarks/cache` private. It includes answer keys and reference material as well as agent inputs. Task packaging must expose only the intended agent inputs.']
    (out / 'README.md').write_text('\n'.join(lines) + '\n')


if __name__ == '__main__':
    main()
