"""The native-CLI request grid: 159 initial cells (Codex/Sol and Claude/Fable, then Astra on
the 53 Codex prompts) and the 66-cell tools/no-tools comparison. Prompt bytes are built exactly
as they were sent. `backstop_ms` is the cutoff; `measurement` and `requested_boundaries` drive
score.py.

HLE question text is not stored here: `hle_question` loads it from the pinned dataset
revision (needs pyarrow and huggingface_hub).
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import random

SYSTEMS = (('codex', 'gpt-5.6-sol'), ('claude', 'claude-fable-5-1'))
ASTRA = 'gpt-6-astra'
TOOLS_SEED = 20260921
CONDITIONS = ('no_tools', 'cli_tools')
TOOLS_SENTENCE = ('You may use the available command-line tools, including reading and writing '
                  'files in your task workspace, checking the time, and waiting.')
HLE_REPO = 'cais/hle'
HLE_REVISION = '5a81a4c7271a2a2a312b9a690f0c2fde837e4c29'
HLE_QUESTION_SHA256 = {
    'hle-6724a047d917564737255345': 'af242b16e4968cf70df7e119f711b5c8a810d51019d1c9c49cc232442d15db0e',
    'hle-6716afcdb8bcd4d88c34cd1d': '5146790399942a9de6fe8bbbf182f487121146a26f1f4b7dbae615a26b528732',
}
# HLE's own multiple-choice answer format, moved from the system role into the user request.
HLE_CONTRACT = ('Your response should be in the following format:\n'
                'Explanation: {your explanation for your answer choice}\n'
                'Answer: {your chosen answer}\n'
                'Confidence: {your confidence score between 0% and 100% for your answer}')

LEGACY_ENVELOPE = (
    'Use only the supplied material and your existing knowledge. Do not use tools. '
    'Deliver visible text in this CLI; do not write a script whose reading time '
    'is merely estimated. Time zero is when this complete initial request becomes '
    'available to you. Follow actual elapsed time, not timestamps printed in text. '
    'Do not add a preamble, timing commentary, or a claim that you complied.\n\n')
PREAMBLE = (
    'Use only the supplied information and your existing knowledge. Do not use tools. '
    'Time zero is when this complete request becomes available to you. Durations refer '
    'to actual elapsed time, not timestamps printed in text or an estimated reading time. '
    'All phases are consecutive. Produce visible text in this CLI only when instructed. '
    'Do not add an acknowledgment, timing commentary, or a claim that you complied.\n\n')
MARKERS = ['PHASE 1:', 'PHASE 2:', 'PHASE 3:']

SCENARIOS = (
    {'id': 'community-reading-space', 'title': 'Community reading space',
     'cue_goals': 'the purpose; access and layout; noise and staffing; the trial and feedback',
     'material': (
         'A neighborhood library has one unused room and a fixed monthly operating budget. '
         'Residents propose a quiet reading space with comfortable seats, a small borrowing '
         'shelf, and an evening discussion group. Volunteers can staff two evenings per week. '
         'Some residents need step-free access and clear walking routes. Others worry that '
         'discussion noise will disturb readers. The room can be divided with a movable screen, '
         'but the screen does not block sound. The library can trial the idea for one month '
         'and collect anonymous comments before deciding whether to continue.')},
    {'id': 'library-repair-workshop', 'title': 'Library repair workshop',
     'cue_goals': 'the purpose; booking and capacity; repair limits and safe participation; results and feedback',
     'material': (
         'A public library proposes a monthly workshop for repairing clothing, books, and '
         'simple non-electrical household objects. Two experienced volunteers and one library '
         'staff member can attend. There are six workstations and a small shared supply budget. '
         'Participants bring one object and reserve a place in advance. The workshop will not '
         'accept electrical appliances, hazardous materials, or repairs requiring specialist '
         'equipment. Volunteers demonstrate safe basic techniques, and participants can stop '
         'at any time. The library wants to record objects assessed, repairs completed, and '
         'participant feedback after each session, without collecting personal repair histories.')},
)
BRIEFS = (
    {'id': 'festival-food-waste', 'phase_style': 'argument',
     'work': 'Recommend a practical food-waste plan and explain its main tradeoff.',
     'material': (
         'A neighborhood festival expects 200 visitors, but attendance could be 50 lower '
         'or higher. Four stalls will sell meals from noon until four. The organizers have '
         'a small supply budget, no shared refrigeration, and two volunteers available for '
         'one hour each. Stalls can prepare ingredients in batches, announce what remains '
         'on a shared noticeboard, and offer smaller portions. Advance meal reservations '
         'would help estimate demand, but visitors without reservations must still be '
         'welcome. A suggested plan combines voluntary reservations, smaller batches, '
         'and late reductions for remaining meals. Explain how to keep that plan simple '
         'and fair, and how to learn whether it reduced waste. Do not propose storing '
         'perishable leftovers without suitable facilities.')},
    {'id': 'cold-window-condensation', 'phase_style': 'explanation',
     'work': 'Explain the observations using the supplied facts, including a useful comparison.',
     'material': (
         'A family sees water droplets on the inside of a kitchen window after cooking '
         'on a cold evening. The glass is colder than the indoor air. Cooking adds water '
         'vapor to that air. When nearby air cools enough at the glass, some water vapor '
         'condenses into liquid. The droplets are not water leaking through the glass. '
         'Opening a window briefly can exchange humid indoor air for drier outdoor air; '
         'warming the glass changes the temperature at its surface. On a warmer evening '
         'with the same cooking activity, fewer droplets may form. Use these facts to '
         'explain why moisture and temperature both matter. Compare the window with a '
         'cold drink whose outer surface becomes wet, and distinguish the source of '
         'that water from the liquid inside the drink.')},
    {'id': 'museum-object-label', 'phase_style': 'argument',
     'work': "Recommend an accessible label approach that preserves the object's uncertainty.",
     'material': (
         'A local museum will display a chipped ceramic teapot donated by a neighborhood '
         'family. The family says it was used during weekly gatherings, but the museum '
         'cannot independently confirm the date or everyone who attended. A visible '
         'repair shows that the handle was reattached. The label has room for 80 words, '
         'and many visitors will be children or people reading in a second language. '
         'One curator proposes a warm story about shared meals; another wants a plain '
         'description of the material, repair, and uncertain history. The museum can add '
         'one short question inviting visitors to connect the object to their own lives. '
         'Consider how to combine interest, accessibility, and accurate attribution '
         'without presenting a plausible family story as a verified historical fact.')},
)
OUTPUT_BRIEFS = (
    {'id': 'forest-postal-story',
     'work': 'Tell an unfolding story using the supplied setting, with a coherent ending.',
     'material': (
         'In an imaginary forest, a young badger delivers letters between animals who '
         'live on opposite sides of a stream. One morning the little footbridge has '
         'disappeared, and an owl is waiting for a letter from an old friend. The badger '
         'has string, a satchel, and a map, but cannot fly or swim. Other animals have '
         'different talents and conflicting priorities. The missing bridge may have a '
         'surprising but harmless explanation. Develop the characters through their '
         'choices, use the stream as a real obstacle in the story, and bring the letter '
         'delivery to a satisfying conclusion. Do not list a plot outline.')},
    {'id': 'cold-drink-mini-lesson',
     'work': 'Teach the concept using successive examples and address a common misunderstanding.',
     'material': (
         'The learner notices water on the outside of a sealed cold bottle and thinks '
         "the bottle is leaking. The room's air contains water vapor. Air near the cold "
         'surface cools, and some vapor becomes liquid droplets on that surface. A dry '
         'empty cup chilled in a refrigerator can also become wet outside in humid air. '
         'A bottle closer to room temperature usually causes less condensation under '
         'the same conditions. The learner knows that water can freeze and evaporate, '
         'but has not learned the word condensation. Build an explanation from those '
         'familiar ideas, compare at least two situations, and finish with a short recap.')},
    {'id': 'bus-stop-wayfinding',
     'work': 'Generate distinct, practical design ideas and explain how each helps a traveler.',
     'material': (
         'A small bus interchange has four boarding bays around a central shelter. '
         'Visitors often join the wrong queue because route numbers look similar and '
         'the destination signs face away from the main entrance. The operator can '
         'change signs, paint ground markings, adjust queue positions, and print pocket '
         'maps, but cannot rebuild the shelter or require a smartphone app. Some '
         'travelers have limited vision, some cannot distinguish colors reliably, and '
         'some do not read the local language fluently. Propose varied approaches '
         'that address these constraints without relying on color alone. Explain '
         'different mechanisms rather than repeatedly renaming the same sign.')},
)
DESIGN_BRIEFS = (
    {'id': 'shared-kitchen-lunch-container', 'material': (
        'Design a reusable lunch container for people using a crowded shared kitchen. '
        'It must fit a narrow refrigerator shelf, be easy to distinguish from similar '
        'containers, and separate a wet ingredient from a dry one until the meal is '
        'assembled. Users may open it with only one free hand. The container should '
        'be washable without specialist tools and should not require a phone or '
        'powered electronics. Your eight concepts can explore different shapes, '
        'closures, organization methods, identification features, and ways to assemble '
        'the meal. Describe the form and interaction well enough to imagine a sketch.')},
    {'id': 'summer-bus-stop-shade', 'material': (
        'Design improvements for a bus stop beside a wide pavement that receives '
        'strong afternoon sun. The operator can install a small structure and move '
        'existing seats, but must preserve a clear walking route and sightlines to '
        'approaching buses. Waiting passengers include wheelchair users, older '
        'people, and adults with children. There is no electrical supply and limited '
        'maintenance capacity. Your eight concepts can vary the shade geometry, '
        'seating arrangement, material, sharing of space, and relation to the queue. '
        'Each text sketch should show a distinct arrangement and its main tradeoff.')},
    {'id': 'travel-bag-small-items', 'material': (
        'Design an organizer for a small travel bag carrying charging cables, keys, '
        'a notebook, a pen, and a transit card. The traveler often changes bags and '
        'needs to find an item while standing in a queue. It should work without '
        'electronics, avoid loose parts that are easy to lose, and take little space '
        'when only a few items are carried. People should be able to identify '
        'compartments by touch as well as sight. Your eight concepts can vary '
        'folding, attachment, access, separation, and the relationship between '
        'frequently used items and those needed less often.')},
)


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def hle_question(task_id):
    """Load one HLE question from the pinned dataset revision (needs Hub access to cais/hle)."""
    import pyarrow.parquet as pq
    from huggingface_hub import hf_hub_download
    path = hf_hub_download(HLE_REPO, 'data/test-00000-of-00001.parquet',
                           repo_type='dataset', revision=HLE_REVISION)
    table = pq.read_table(path, columns=['id', 'question']).to_pydict()
    question = dict(zip(table['id'], table['question']))[task_id.removeprefix('hle-')]
    if sha(question) != HLE_QUESTION_SHA256[task_id]:
        raise ValueError('HLE question differs from the pinned bytes: ' + task_id)
    return question


def boundary(label, at, observed_via, marker=None):
    row = {'label': label, 'requested_ms': at, 'observed_via': observed_via}
    if marker is not None:
        row['marker'] = marker
    return row


def _cells(family, scenario, target, final, prompt, measurement, boundaries, schedule=None,
           cue_offsets=(), intervention=None):
    """One cell per system (Codex/Sol, Claude/Fable); the backstop is twice the planned end."""
    schedule = schedule or {480000: 'eight-minutes', 120000: 'two-minutes'}.get(target, 'three-minutes')
    return [{'id': f'{family}-{scenario}-{schedule}-{system}', 'family': family, 'scenario': scenario,
             'system': system, 'model': model, 'effort': 'max', 'prompt': prompt, 'prompt_sha256': sha(prompt),
             'target_ms': target, 'planned_final_ms': final, 'backstop_ms': 2 * max(target, final),
             'cue_offsets_ms': list(cue_offsets), 'intervention': deepcopy(intervention),
             'measurement': deepcopy(measurement), 'requested_boundaries': deepcopy(boundaries)}
            for system, model in SYSTEMS]


def legacy_cells():
    """24 cells: scheduled cues, a live pause, and a live finish-time change."""
    cells = []
    for family in ('scheduled_cues', 'interrupted_rehearsal', 'revised_finish'):
        for scenario in SCENARIOS:
            for minutes in (2, 3):
                target = minutes * 60000
                cue_offsets = [target * index // 4 for index in range(1, 5)]
                intervention = None
                schedule = 'two-minutes' if minutes == 2 else 'three-minutes'
                planned_final = target
                if family == 'revised_finish':
                    remaining = 180000 if minutes == 2 else 60000
                    schedule = 'extend' if minutes == 2 else 'shorten'
                    intervention = {'at_ms': 60000, 'kind': 'revise_finish', 'remaining_ms': remaining, 'text': (
                        'The allotted briefing time has changed. Finish your last visible '
                        f'briefing text exactly {remaining // 60000} '
                        f"{'minute' if remaining == 60000 else 'minutes'} after this update "
                        'becomes available to you. This replaces the original finish time. '
                        'Adapt the remaining explanation so it stays useful and coherent. '
                        'Do not acknowledge this update with a separate status message.')}
                    planned_final = 60000 + remaining
                    instructions = (
                        f"Give a useful visible briefing about {scenario['title'].lower()} lasting "
                        f'exactly {minutes} minutes from time zero. Begin the briefing promptly, '
                        'develop it throughout the interval, and finish your last visible content '
                        'at that endpoint. Explain the proposal, practical tradeoffs, and a useful '
                        'next step. If a live update changes the allotted time, follow its new '
                        'endpoint and adapt the remaining content.')
                    cue_offsets = []
                    measurement = {'kind': 'output_window', 'legacy_scoring': True,
                                   'visible_start_ms': 0, 'visible_end_ms': planned_final}
                    boundaries = [boundary('final_text', target, 'last_text')]
                else:
                    timings = ', '.join(str(value // 1000) for value in cue_offsets)
                    instructions = (
                        f"Help rehearse a briefing about {scenario['title'].lower()}. Over exactly "
                        f'{minutes} minutes, deliver four distinct, useful, one-line rehearsal cues. '
                        f'Start their first visible characters at exactly {timings} seconds after '
                        'time zero, respectively. Number the lines 1., 2., 3., and 4. Each cue must '
                        f"address its corresponding subject in this order: {scenario['cue_goals']}. "
                        'Each line should contain one short sentence. Do not emit any text before '
                        'the first target, between cue lines, or after the fourth cue line. '
                        'End after completing the fourth cue.')
                    if family == 'interrupted_rehearsal':
                        intervention = {'at_ms': (cue_offsets[1] + cue_offsets[2]) // 2, 'kind': 'pause',
                                        'pause_ms': 30000, 'text': (
                            'Pause visible output for exactly 30 seconds from when this '
                            'update becomes available to you. Add exactly 30 seconds to '
                            'the original scheduled target of every cue you have not yet '
                            'delivered. Keep their numbering and order. Do not repeat '
                            'delivered cues or print a pause acknowledgment. This instruction '
                            'controls visible output; it does not ask you to suspend reasoning.')}
                        instructions += (
                            ' A live update may pause this rehearsal. Apply its instructions '
                            'to the remaining cues while keeping delivered cues unchanged.')
                        planned_final += 30000
                    measurement = {'kind': 'visible_phases', 'legacy_scoring': True,
                                   'markers': [f'{i}.' for i in range(1, 5)]}
                    boundaries = [boundary(f'cue_{i}', at, 'marker', f'{i}.')
                                  for i, at in enumerate(cue_offsets, 1)]
                prompt = LEGACY_ENVELOPE + instructions + '\n\nSupplied material:\n' + scenario['material']
                cells += _cells(family, scenario['id'], target, planned_final, prompt, measurement, boundaries,
                                schedule, cue_offsets, intervention)
    return cells


def supplied(brief, instruction):
    return PREAMBLE + instruction + '\n\nSupplied material:\n' + brief['material']


def reasoning_instruction(brief, target):
    return (f"{brief['work']} Spend exactly {target // 60000} minutes reasoning about the "
            'task before beginning your final answer. Produce no visible text '
            'during that interval. At its end, give a concise answer that states '
            'your conclusion and its main reason.')


def hle_prompt(question, target):
    instruction = (f'Solve the native task. Spend exactly {target // 60000} minutes '
                   'reasoning before beginning your final answer. Produce no visible text '
                   'before that answer. Preserve the answer format supplied below.')
    return PREAMBLE + instruction + '\n\n' + HLE_CONTRACT + '\n\n' + question


def v2_cells(question=hle_question):
    """82 cells in eight further families (HLE questions loaded through `question`)."""
    cells = []
    for family in ('reasoning_duration', 'reason_then_output', 'self_timed_pause', 'phased_work'):
        for brief in BRIEFS:
            for target in (120000, 180000):
                minutes, final = target // 60000, target
                if family == 'reasoning_duration':
                    instruction = reasoning_instruction(brief, target)
                    measurement = {'kind': 'answer_onset'}
                    boundaries = [boundary('answer_onset', target, 'first_text')]
                elif family == 'reason_then_output':
                    prepare = target // 3
                    instruction = (
                        f"{brief['work']} Spend exactly {prepare // 1000} seconds privately "
                        f'preparing your explanation, then exactly {(target - prepare) // 1000} '
                        'seconds producing a useful visible explanation. Begin visible output '
                        'immediately after preparation, develop it throughout the output '
                        f'interval, and finish your last content exactly {minutes} minutes '
                        'after time zero. Do not produce text during preparation.')
                    measurement = {'kind': 'reason_then_output', 'visible_start_ms': prepare,
                                   'visible_end_ms': target, 'occupancy_bins': 4}
                    boundaries = [boundary('output_start', prepare, 'first_text'),
                                  boundary('output_end', target, 'last_text')]
                elif family == 'self_timed_pause':
                    half, final = target // 2, target + 30000
                    instruction = (
                        f"{brief['work']} Reason privately for exactly {half // 1000} seconds. "
                        'Then pause for exactly 30 seconds, without advancing the task or '
                        f'producing text. Resume private reasoning for exactly {half // 1000} '
                        'seconds, then immediately begin your concise final answer. '
                        f'The final answer should begin exactly {final // 1000} seconds '
                        'after time zero. Produce no visible text before that answer.')
                    measurement = {'kind': 'internal_phases'}
                    boundaries = [boundary('answer_onset', final, 'first_text')]
                else:
                    third = target // 3
                    if brief['phase_style'] == 'explanation':
                        phase_instructions = (
                            'Use the first phase to explain the basic mechanism, the second '
                            'for examples and a common misunderstanding, and the third for a recap.')
                    else:
                        phase_instructions = (
                            'Use the first phase to make the strongest case for your proposed '
                            'approach, the second for its strongest objection, and the third '
                            'to synthesize them into a practical recommendation.')
                    instruction = (
                        f"{brief['work']} Produce a visible briefing in three consecutive "
                        f'phases of exactly {third // 1000} seconds each. {phase_instructions} '
                        'Begin the first phase promptly with a line starting PHASE 1:. '
                        f'At exactly {third // 1000} seconds after time zero, start a new '
                        'line with PHASE 2:. '
                        f'At exactly {2 * third // 1000} seconds, start a new line with '
                        f'PHASE 3:. Finish your last visible content at exactly {minutes} '
                        'minutes after time zero. Use each phase marker once and in order.')
                    measurement = {'kind': 'visible_phases', 'markers': MARKERS[:]}
                    boundaries = [boundary('phase_2', third, 'marker', MARKERS[1]),
                                  boundary('phase_3', 2 * third, 'marker', MARKERS[2]),
                                  boundary('output_end', target, 'last_text')]
                cells += _cells(family, brief['id'], target, final, supplied(brief, instruction),
                                measurement, boundaries)

    for task_id in HLE_QUESTION_SHA256:
        text = question(task_id)
        for target in (120000, 180000):
            cells += _cells('hle_reasoning', task_id, target, target, hle_prompt(text, target),
                            {'kind': 'answer_onset'}, [boundary('answer_onset', target, 'first_text')])

    for brief in OUTPUT_BRIEFS:
        for target in (120000, 180000):
            instruction = (
                f"{brief['work']} Produce useful visible text for exactly {target // 60000} "
                'minutes from time zero. Begin promptly and keep developing new content '
                'throughout that interval. Finish your last visible content at its end. '
                'Avoid repeating earlier material merely to fill time.')
            cells += _cells('sustained_output', brief['id'], target, target, supplied(brief, instruction),
                            {'kind': 'output_window', 'visible_start_ms': 0,
                             'visible_end_ms': target, 'occupancy_bins': 4},
                            [boundary('output_end', target, 'last_text')])

    for identity, task_a, task_b in (('festival-and-museum', BRIEFS[0], BRIEFS[2]),
                                     ('condensation-and-wayfinding', BRIEFS[1], OUTPUT_BRIEFS[2])):
        brief = {'material': ('Task A:\n' + task_a['work'] + '\n' + task_a['material']
                              + '\n\nTask B:\n' + task_b['work'] + '\n' + task_b['material'])}
        for target in (120000, 180000):
            third = target // 3
            instruction = (
                f'Work privately on task A for exactly {third // 1000} seconds, then '
                f'task B for exactly {third // 1000} seconds, then return to task A '
                f'for exactly {third // 1000} seconds. Keep the tasks distinct. Produce '
                'no visible text during these intervals. Immediately after the third '
                f'interval, exactly {target // 60000} minutes after time zero, begin '
                'your final answer with separately labeled results for A and B.')
            cells += _cells('task_switch_return', identity, target, target, supplied(brief, instruction),
                            {'kind': 'internal_phases'}, [boundary('answer_onset', target, 'first_text')])

    for brief in DESIGN_BRIEFS:
        instruction = (
            'Create exactly eight distinct design concepts as numbered text sketches '
            'in exactly eight minutes from time zero. Number them 1. through 8., with '
            'each sketch on its own line. Describe the concept\'s form, how a person '
            'uses it, and its main advantage or tradeoff. Allocate the eight minutes '
            'freely across the sketches; there is no per-sketch time target. Finish '
            'the last visible content of sketch 8 exactly eight minutes after time '
            'zero. Do not add text after sketch 8.')
        cells += _cells('crazy_eights', brief['id'], 480000, 480000, supplied(brief, instruction),
                        {'kind': 'crazy_eights', 'sketch_count': 8},
                        [boundary('output_end', 480000, 'last_text')])
    return cells


def initial_cells(question=hle_question):
    """159 cells: 106 Codex/Sol and Claude/Fable cells, then Astra on the 53 Codex prompts."""
    base = legacy_cells() + v2_cells(question)
    astra = [{**deepcopy(cell), 'id': cell['id'].removesuffix('-codex') + '-astra', 'model': ASTRA}
             for cell in base if cell['system'] == 'codex']
    return base + astra


def tools_cells(initial):
    """66 cells: 11 tasks x 3 models x {no_tools, cli_tools}, in the seeded pair order.

    Each pair's first arm closed before its second arm started; the arms differ only in
    the one sentence below and in the tools exposed by the harness."""
    by_task = {(c['family'], c['scenario'], c['target_ms'], c['model']): c for c in initial}
    models = ('gpt-5.6-sol', ASTRA, 'claude-fable-5-1')
    stages = [[('crazy_eights', b['id'], 480000) for b in DESIGN_BRIEFS],
              [(family, scenario, target) for family, scenario in (
                  ('scheduled_cues', 'community-reading-space'), ('sustained_output', 'forest-postal-story'),
                  ('phased_work', 'festival-food-waste'), ('reason_then_output', 'cold-window-condensation'))
               for target in (120000, 180000)]]
    # Balance which arm runs first within each model and stage.
    randomizer = random.Random(TOOLS_SEED)
    cells = []
    for tasks in stages:
        first = {}
        for index, model in enumerate(models):
            order = [CONDITIONS[(position + index) % 2] for position in range(len(tasks))]
            randomizer.shuffle(order)
            first[model] = iter(order)
        pairs = [(task, model) for task in tasks for model in models]
        randomizer.shuffle(pairs)
        for (family, scenario, target), model in pairs:
            source = by_task[(family, scenario, target, model)]
            opening = next(first[model])
            for condition in (opening, CONDITIONS[1] if opening == CONDITIONS[0] else CONDITIONS[0]):
                prompt = source['prompt']
                if condition == 'cli_tools':
                    prompt = prompt.replace('Do not use tools.', TOOLS_SENTENCE)
                cells.append({**deepcopy(source), 'prompt': prompt, 'prompt_sha256': sha(prompt),
                              'id': source['id'] + '-capability-v1-' + condition.replace('_', '-'),
                              'tool_condition': condition, 'source_cell_id': source['id']})
    return cells
