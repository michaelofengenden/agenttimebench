#!/usr/bin/env python3
"""Reproduce a private HLE Diamond draft from a pinned parquet and frozen decisions.

Only the allowlisted metadata/question columns are projected. This script never
reads answer/rationale columns, prints a question, decodes an image, calls a model,
or uses the network. The manual decisions are an auditable draft, not an automatic
subject classifier or a claim that the full source pool was screened.
"""
import argparse
import collections
import hashlib
import json
from pathlib import Path

import pyarrow.parquet as pq

OWNED = Path('/private/tmp/agenttime-inventory-20261002/hle-diamond-draft')
BASE = OWNED.parent
REPO = Path('/Users/michaelofengenden/Developer/TimeResearch/AgentTime-v1.1')
SOURCE = BASE / 'gated-sources/hle-diamond/data/test-00000-of-00001.parquet'
CACHE = REPO / 'benchmarks/cache/gated-inputs-20261002/hle-diamond/data/test-00000-of-00001.parquet'
PLAN = BASE / 'new-candidates/hle-diamond-selection-pending.json'
DOWNLOAD_RECEIPT = BASE / 'gated-sources/hle-diamond/download-receipt.json'
COLUMNS = ('id', 'partition', 'question', 'raw_subject', 'category', 'image')
EXPECTED_HASH = '2b3421909ac47580d8373d4e7e5a23f0f08365d2cfb54a13100ddfac4a961e87'
EXPECTED_BYTES = 224204778
REVISION = '04eeb7efa7e3e4f83a00cbd5ce436a38fd5dda23'


def sha_file(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def sha_text(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def canonical_hash(value):
    return sha_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')))


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Compare deterministic artifacts without writing them')
    args = parser.parse_args()
    plan = json.loads(PLAN.read_text())
    decisions_file = OWNED / 'screening-decisions.json'
    review = json.loads(decisions_file.read_text())
    seed = plan['selection_method']['seed']
    assert seed == 'agenttime-v1.1-review-20261002'
    assert plan['source']['revision'] == REVISION
    assert set(plan['existing_subject_exclusions']['exclude']) == {'core_biology', 'core_clinical', 'core_cybersecurity'}
    assert SOURCE.stat().st_size == EXPECTED_BYTES
    assert sha_file(SOURCE) == EXPECTED_HASH
    assert CACHE.is_file() and CACHE.stat().st_size == EXPECTED_BYTES
    assert sha_file(CACHE) == EXPECTED_HASH

    parquet = pq.ParquetFile(SOURCE)
    assert parquet.metadata.num_rows == 1000
    assert set(COLUMNS).isdisjoint({'answer', 'rationale', 'rationale_image'})
    rows = parquet.read(columns=list(COLUMNS)).to_pylist()
    assert len(rows) == 1000
    ids = [r['id'] for r in rows]
    assert all(isinstance(i, str) and i and i.strip() == i for i in ids)
    assert len(set(ids)) == 1000
    assert collections.Counter(r['partition'] for r in rows) == {'reasoning': 500, 'knowledge': 500}
    assert all(isinstance(r['question'], str) and r['question'] for r in rows)
    decision_by_id = {r['id']: r for r in review['decisions']}
    assert len(decision_by_id) == len(review['decisions'])
    assert set(decision_by_id) <= set(ids)

    ranked = []
    ledger = []
    selected = []
    counts = {}
    for partition in ('reasoning', 'knowledge'):
        pool = [r for r in rows if r['partition'] == partition]
        pool.sort(key=lambda r: (sha_text('\0'.join((seed, 'hle-diamond', partition, r['id']))), r['id']))
        prefix = review['classified_prefix_end_rank'][partition]
        recorded_ranks = []
        removals = collections.Counter()
        eligible = 0
        pending = 0
        for rank, r in enumerate(pool, 1):
            img = r['image']
            image_meta = {'present': bool(img), 'encoding': 'data-url' if img and img.startswith('data:') else ('other-string' if img else 'absent')}
            if img and img.startswith('data:'):
                image_meta['media_type'] = img[5:].split(';', 1)[0].split(',', 1)[0]
                image_meta['payload_decoded'] = False
                image_meta['rendering_verified'] = False
            meta = {
                'id': r['id'], 'partition': partition, 'rank': rank,
                'rank_sha256': sha_text('\0'.join((seed, 'hle-diamond', partition, r['id']))),
                'raw_subject': r['raw_subject'], 'category': r['category'],
                'image_present': bool(img), 'image_metadata': image_meta,
                'question_sha256': sha_text(r['question']),
                'question_hash_definition': 'SHA256 of exact UTF-8 question field, no normalization',
                'question_character_count': len(r['question']),
                'eligibility_status': 'unreviewed', 'selected': False,
            }
            d = decision_by_id.get(r['id'])
            if d:
                assert d['partition'] == partition and d['rank'] == rank
                assert d['question_sha256'] == meta['question_sha256']
                assert rank <= prefix
                recorded_ranks.append(rank)
                label = d['domain_label']
                assert label in {'none', 'core_biology', 'core_clinical', 'core_cybersecurity', 'borderline'}
                expected_status = 'eligible' if label == 'none' else ('pending' if label == 'borderline' else 'excluded')
                assert d['eligibility_status'] == expected_status
                meta.update({
                    'eligibility_status': expected_status, 'domain_label': label,
                    'eligibility_rationale': d['eligibility_rationale'],
                    'reviewed_required_domain': d['reviewed_required_domain'],
                    'evidence_span': {'field': 'question', 'start_character': 0, 'end_character_exclusive': len(r['question']), 'question_sha256': meta['question_sha256'], 'verbatim_text_omitted': True},
                    'review_basis': 'Question and source subject/category; no answer, rationale or image content inspected',
                    'review_status': 'single_lane_draft_requires_final_review',
                })
                if expected_status == 'eligible':
                    eligible += 1
                    assert eligible <= 10
                    meta['selected'] = True
                elif expected_status == 'pending':
                    pending += 1
                else:
                    removals[label] += 1
                ledger.append(dict(meta))
            else:
                assert rank > prefix
            ranked.append(meta)
            if meta['selected']:
                item = dict(meta)
                item.update({
                    'selection_status': 'draft_for_michael_review',
                    'source_locator': {'path_relative_to_repository': str(CACHE.relative_to(REPO)), 'id_column': 'id', 'id': r['id'], 'question_column': 'question', 'image_column': 'image'},
                    'components': {
                        'prompt': {'status': 'present', 'evidence': 'Exact selected question exists in verified pinned parquet; question SHA256 recorded.', 'adapted_natural_prompt': {'status': 'needs_checking', 'gap': 'No v1.1 prompt wrapper, answer-format contract, or rendering verified.'}},
                        'assets': {'status': 'present', 'evidence': 'Encoded image field present inside pinned parquet.' if img else 'No separate image asset indicated by image field; question text is present.', 'needs_checking': ['Decode and validate image bytes; check complete, legible rendering and domain interpretation before freeze.'] if img else ['Confirm question-only rendering preserves formatting and choices.']},
                        'environment': {'status': 'needs_checking', 'source_definition_status': 'needs_checking', 'installed_environment_checked': False, 'gap': 'Dataset is available, but no v1.1 HLE runtime, model input route, multimodal route or dependency qualification was performed.'},
                        'grader': {'status': 'needs_checking', 'evidence': 'Parquet schema includes an answer column, but answer values and rationales were not read.', 'gap': 'Pin and inspect official grading policy, isolate references from subject input, verify parser/judge and scoring against synthetic fixtures; no score or grader run was attempted.'},
                    },
                    'runtime_ready': False, 'qualification_status': 'not_run', 'scientific_admitted': False,
                })
                selected.append(item)
        assert recorded_ranks == list(range(1, prefix + 1))
        assert eligible == 10
        assert any(s['partition'] == partition and s['rank'] == prefix for s in selected)
        counts[partition] = {
            'original_count': 500, 'unique_id_count': 500, 'unavailable_count': 0,
            'classified_prefix_end_rank': prefix, 'classified_count': prefix,
            'unreviewed_eligibility_count': 500 - prefix,
            'screened_prefix_domain_removals': {k: removals[k] for k in ('core_biology', 'core_clinical', 'core_cybersecurity')},
            'screened_prefix_borderline_pending': pending,
            'screened_prefix_eligible_count': eligible, 'selected_count': eligible,
            'full_pool_eligible_count': None, 'full_pool_domain_removal_counts': None,
            'image_present_count_full_partition': sum(bool(r['image']) for r in pool),
            'selected_image_count': sum(s['image_present'] for s in selected if s['partition'] == partition),
        }

    assert len(ledger) == 21 and len(selected) == 20
    source = {
        'dataset': 'cais/hle-diamond', 'revision': REVISION, 'split': 'test',
        'source_path': str(SOURCE), 'source_sha256': EXPECTED_HASH, 'source_bytes': EXPECTED_BYTES,
        'v11_cache_path': str(CACHE), 'v11_cache_sha256': EXPECTED_HASH,
        'download_receipt_path': str(DOWNLOAD_RECEIPT), 'download_receipt_sha256': sha_file(DOWNLOAD_RECEIPT),
        'v11_import_receipt_path': str(CACHE.parents[1] / 'import-receipt.json'),
        'download_and_cache_hashes_independently_verified': True,
        'projected_columns_read': list(COLUMNS),
        'excluded_from_projection': ['answer', 'rationale', 'rationale_image', 'image_preview', 'author_name', 'canary', 'answer_type'],
    }
    method = {
        'seed': seed, 'rank_expression': 'SHA256(UTF8(seed + NUL + "hle-diamond" + NUL + partition + NUL + exact_id))',
        'sort': ['rank_sha256 ascending', 'exact_id ascending'],
        'selection': 'Hash-rank all 500 rows in each partition; classify the prefix in order and stop after ten eligible rows. Excluded or pending rows are not selected.',
        'conditional_equivalence': 'For a fixed itemwise eligibility rule, scanning ranked candidates to the tenth eligible item gives the same selected IDs as screening the complete pool before ranking. The unreviewed suffix contributes no eligibility-count claim.',
        'domain_policy': plan['existing_subject_exclusions'],
        'classification_scope': 'Only the 21 rows in the classified prefixes have recorded domain decisions. All other rows remain unreviewed for eligibility.',
        'inspection_scope_disclosure': 'A private question preview included ranks 1 through 18 of each partition before the stopping prefixes were determined. The additional 15 previewed rows were not adjudicated and remain eligibility-unreviewed; they were not used to choose the prefix or its domain labels.',
        'previewed_prefix_end_rank': review['previewed_prefix_end_rank'],
        'not_applied_as_filters': ['text-only', 'multiple-choice-only', 'HLE-Verified membership', 'image presence', 'answer content', 'difficulty', 'timing', 'study-model outcomes'],
        'original_plan_path': str(PLAN), 'original_plan_sha256': sha_file(PLAN),
        'original_plan_status_note': 'The old pending plan predates the subsequently authorized source acquisition. Its seed and domain policy are reused; its historical access blocker is resolved by the verified local parquet.',
        'screening_decisions_sha256': sha_file(decisions_file),
        'manual_classification_reproducibility': 'The frozen per-ID decisions and question hashes reproduce this draft. Reclassification requires a revised decision ledger and may change selected IDs.',
    }
    integrity = {
        'parquet_rows': 1000, 'unique_nonempty_ids': 1000, 'partition_counts': {'reasoning': 500, 'knowledge': 500},
        'unique_ids_per_partition': {'reasoning': 500, 'knowledge': 500}, 'cross_partition_duplicate_ids': 0,
        'question_fields_nonempty': 1000, 'full_pool_image_present_count': sum(bool(r['image']) for r in rows),
        'classified_count': len(ledger), 'unreviewed_eligibility_count': 1000 - len(ledger),
        'selected_count': len(selected), 'selected_image_count': sum(s['image_present'] for s in selected),
        'question_or_answer_text_written_to_artifacts': False, 'answer_or_rationale_columns_read': False,
        'models_called': 0, 'benchmark_runs': 0, 'images_decoded_or_rendered': 0,
    }
    gaps = [
        'Draft only: Michael and any required final reviewer lanes have not approved domain decisions or exact IDs.',
        'Only the selected prefixes were classified. Full-pool eligible and excluded totals are unknown, not zero.',
        'Four selected rows have encoded image input. Image decoding, legibility, full prompt rendering and final visual domain review remain unchecked.',
        'No text-only or multiple-choice restriction was imposed; the v1.1 runner must preserve native response contracts.',
        'Source category labels are retained verbatim and can be inaccurate. Eligibility uses required work, not category alone.',
        'No reference answer or rationale was inspected. Official grading implementation, reference isolation, parser/judge behavior and scorer qualification remain unchecked.',
        'No installed environment, benchmark execution, provider/model call, timing observation, grading result, or scientific admission is established.',
    ]
    common = {
        'family_id': 'hle', 'benchmark': 'HLE Diamond', 'status': 'draft_for_michael_review',
        'source': source, 'method': method, 'integrity': integrity, 'counts_by_partition': counts,
        'full_pool_eligible_count': None, 'full_pool_removal_counts': None,
        'runtime_ready': False, 'scientific_admitted': False, 'remaining_gaps': gaps,
    }
    drafts = {
        'draft-selection.json': {**common, 'selected': selected, 'exact_ids': {p: [s['id'] for s in selected if s['partition'] == p] for p in ('reasoning', 'knowledge')}, 'pending_borderlines': [r for r in ledger if r['eligibility_status'] == 'pending']},
        'selection-ledger.json': {**common, 'screened_candidates': ledger, 'selected_manifest_sha256': canonical_hash([{k: s[k] for k in ('id', 'partition', 'rank', 'question_sha256', 'domain_label')} for s in selected]), 'ranked_pool_manifest_sha256': canonical_hash(ranked)},
        'ranked-pools.json': {'source': source, 'seed': seed, 'rank_expression': method['rank_expression'], 'total_count': len(ranked), 'eligibility_unreviewed_meaning': 'No recorded domain adjudication; this does not assert that metadata or preview text was never viewed.', 'rows': ranked},
    }
    for filename, artifact in drafts.items():
        target = OWNED / filename
        if args.check:
            assert json.loads(target.read_text()) == artifact, filename + ' differs'
        else:
            dump(target, artifact)
    print(json.dumps({'mode': 'verified' if args.check else 'written', 'rows': 1000, 'partitions': {'reasoning': 500, 'knowledge': 500}, 'selected': 20, 'classified': len(ledger), 'eligibility_unreviewed': 1000-len(ledger), 'selected_images': sum(s['image_present'] for s in selected), 'output_directory': str(OWNED)}))


if __name__ == '__main__':
    main()
