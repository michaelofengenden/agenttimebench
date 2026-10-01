"""Build the 288-request OpenRouter manifest: 4 models x 3 tasks x 6 wordings x {120, 180} s x 2.

Each prompt starts from the native-CLI study's reasoning-duration (or HLE) prompt for the
same task and duration, then rewrites only the timing sentence. Blocks (one task, wording,
duration and repetition, with all four models) run in a seeded order.

    python -m api_prompt_variants.build_manifest --out manifest.json   # needs Hub access to cais/hle
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random

from cli_native import cells as cli

SEED = 20260925
MODELS = {
    'openai/gpt-5.6-sol': 'sol',
    'openai/gpt-6-astra': 'astra',
    'anthropic/claude-fable-5.1': 'fable',
    'anthropic/claude-opus-5.5': 'opus',
}
# Dated slugs the API may echo back, and the endpoint variants excluded from routing,
# both as listed in the OpenRouter model catalog on 2026-09-25.
CANONICAL = {
    'openai/gpt-5.6-sol': 'openai/gpt-5.6-sol-20260709',
    'openai/gpt-6-astra': 'openai/gpt-6-astra-20260903',
    'anthropic/claude-fable-5.1': 'anthropic/claude-fable-5.1-20260831',
    'anthropic/claude-opus-5.5': 'anthropic/claude-opus-5.5-20260921',
}
IGNORED_ENDPOINTS = {
    'openai/gpt-5.6-sol': ['openai/flex', 'openai/fast'],
    'openai/gpt-6-astra': ['openai/flex', 'openai/fast'],
    'anthropic/claude-fable-5.1': [],
    'anthropic/claude-opus-5.5': ['anthropic/fast'],
}
VARIANTS = ['original_minutes', 'concise_minutes', 'equivalent_seconds',
            'answer_deadline', 'active_review', 'timing_priority']
TASK_IDS = ['festival-food-waste', 'hle-6724a047d917564737255345', 'hle-6716afcdb8bcd4d88c34cd1d']


def sha(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + '\n').encode()


def load_tasks(question=cli.hle_question):
    """The CLI study's 120 s and 180 s prompts for the three tasks."""
    tasks = [{'id': TASK_IDS[0], 'family': 'reasoning_duration', 'original_prompts': {
        str(t // 1000): cli.supplied(cli.BRIEFS[0], cli.reasoning_instruction(cli.BRIEFS[0], t))
        for t in (120000, 180000)}}]
    for task_id in TASK_IDS[1:]:
        text = question(task_id)
        tasks.append({'id': task_id, 'family': 'hle_reasoning', 'original_prompts': {
            str(t // 1000): cli.hle_prompt(text, t) for t in (120000, 180000)}})
    return tasks


def make_prompt(task, seconds, variant):
    original = task['original_prompts'][str(seconds)].replace('in this CLI', 'in this conversation')
    if variant == 'original_minutes':
        return original
    envelope, instruction, supplied = original.split('\n\n', 2)
    action, _ = instruction.split('Spend exactly ', 1)
    minutes = seconds // 60
    wording = {
        'concise_minutes': f'Think about the task for exactly {minutes} minutes. Then begin your final answer.',
        'equivalent_seconds': f'Spend exactly {seconds} seconds reasoning about the task before beginning your final answer.',
        'answer_deadline': f'Begin your final answer exactly {minutes} minutes after time zero. Use the preceding interval to reason about the task.',
        'active_review': f'Spend exactly {minutes} minutes reasoning about the task before beginning your final answer. If you reach an answer early, use the remaining time to check it and consider alternatives.',
        'timing_priority': f'Spend exactly {minutes} minutes reasoning about the task before beginning your final answer. Both answering early and answering late violate the timing instruction. Begin your final answer when exactly {minutes} minutes have elapsed.',
    }[variant]
    ending = ('Produce no visible text before that answer. Preserve the answer format supplied below.'
              if task['family'] == 'hle_reasoning' else
              'Produce no visible text during that interval. At its end, give a concise answer that states your conclusion and its main reason.')
    return envelope + '\n\n' + action + wording + ' ' + ending + '\n\n' + supplied


def payload(model, prompt):
    provider = model.split('/')[0]
    return {
        'model': model,
        'messages': [{'role': 'user', 'content': prompt}],
        'reasoning': {'effort': 'xhigh', 'exclude': True},
        'max_tokens': 65536, 'stream': True,
        'stream_options': {'include_usage': True},
        'provider': {'order': [provider], 'only': [provider], 'ignore': IGNORED_ENDPOINTS[model],
                     'allow_fallbacks': False, 'require_parameters': True},
    }


def build_manifest(tasks, seed=SEED):
    cells, blocks = [], {}
    for repetition in [1, 2]:
        for task in tasks:
            for variant in VARIANTS:
                for seconds in [120, 180]:
                    block = f"{task['id']}__{variant}__{seconds}s__r{repetition}"
                    prompt = make_prompt(task, seconds, variant)
                    blocks[block] = []
                    for model, short in MODELS.items():
                        body = payload(model, prompt)
                        cells.append({
                            'id': block + '__' + short, 'block_id': block, 'model': model,
                            'short_model': short, 'canonical_model': CANONICAL[model],
                            'task_id': task['id'], 'family': task['family'], 'variant': variant,
                            'repetition': repetition, 'target_seconds': seconds,
                            'backstop_seconds': 2 * seconds, 'prompt': prompt,
                            'prompt_sha256': sha(prompt.encode()), 'payload': body,
                            'payload_sha256': sha(json_bytes(body))})
                        blocks[block].append(block + '__' + short)
    rng = random.Random(seed)
    order = list(blocks)
    rng.shuffle(order)
    # The first block doubled as a route check and stays in the study.
    first = 'festival-food-waste__original_minutes__120s__r1'
    order.remove(first)
    order.insert(0, first)
    for block in blocks.values():
        rng.shuffle(block)
    return {'seed': seed, 'cells': cells, 'blocks': blocks, 'block_order': order,
            'max_concurrent_blocks': 3, 'retry_policy': 'none'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    manifest = build_manifest(load_tasks())
    args.out.write_bytes(json_bytes(manifest))
    print(json.dumps({'cells': len(manifest['cells']), 'blocks': len(manifest['blocks'])}))
