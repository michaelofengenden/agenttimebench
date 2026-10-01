"""Timing scores for one native CLI attempt (pure functions, no I/O).

Event offsets are milliseconds since prompt dispatch. Text times are client-arrival proxies;
nothing here observes private reasoning or grades content. A boundary passes within
TOLERANCE_MS. A strict pass needs at least one boundary, every boundary within tolerance and
no issue. `score_attempt` scores the cue and live-update families and passes every other
family to `score_extended`. Cells are assumed to come from cells.py.
"""
from __future__ import annotations

import math
import re

TOLERANCE_MS = 1000
LEGACY_FAMILIES = {'scheduled_cues', 'interrupted_rehearsal', 'revised_finish'}


def _prepare(events, outcome, legacy):
    """Checks shared by both scorers. Returns issues, eligibility and the visible text."""
    issues, eligible, valid, previous = [], True, [], -1
    for event in events:
        at = event.get('offset_ms') if isinstance(event, dict) else None
        if isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at) or at < 0:
            issues.append('invalid_event_offset')
            eligible = False
            continue
        if at < previous:
            issues.append('nonmonotonic_event_offsets')
            eligible = False
        previous = at
        if event.get('kind') == 'text' and not isinstance(event.get('text'), str):
            issues.append('invalid_text_event')
            eligible = False
            continue
        valid.append(event)
    if outcome.get('kind') not in {'native', 'fixture'}:
        issues.append('invalid_outcome_kind')
        eligible = False
    if outcome.get('state') != 'completed':
        issues.append('outcome_' + str(outcome.get('state', 'missing')))
        eligible = False
    started = outcome.get('model_attempt_started')
    if outcome.get('kind') == 'native' and (started is False if legacy else started is not True):
        issues.append('model_attempt_not_started')
        eligible = False
    text = [e for e in valid if e.get('kind') == 'text']
    content = [e for e in text if e['text'].strip()]
    if not content:
        issues.append('missing_text')
    stream = ''.join(e['text'] for e in text)
    arrivals = [e['offset_ms'] for e in text for _ in e['text']]  # arrival time of each character
    return issues, eligible, valid, content, stream, arrivals


def _numbered(stream, arrivals, pattern, issues, empty_issue, other_issue):
    """Numbered lines (`1.`, `2.`, ...): number, arrival of the digit, and text after it."""
    records, cursor = [], 0
    for line in stream.splitlines(keepends=True):
        match = re.match(pattern, line)
        if match:
            number, nonempty = int(match.group(1)), bool(line[match.end():].strip())
            records.append({'number': number, 'nonempty': nonempty,
                            'offset_ms': arrivals[cursor + match.start(1)]})
            if not nonempty:
                issues.append(empty_issue + str(number))
        elif line.strip() and other_issue:
            issues.append(other_issue)
        cursor += len(line)
    return records


def _boundary(label, requested, observed):
    return {'label': label, 'requested_ms': requested, 'observed_ms': observed,
            'error_ms': None if requested is None or observed is None else observed - requested}


def _off_target(boundary, issues):
    error = boundary['error_ms']
    if error is not None and abs(error) > TOLERANCE_MS:
        issues.append(boundary['label'] + ('_early' if error < 0 else '_late'))


def _strict_pass(boundaries, issues):
    return bool(boundaries) and not issues and all(
        b['error_ms'] is not None and abs(b['error_ms']) <= TOLERANCE_MS for b in boundaries)


def score_attempt(cell: dict, events: list[dict], outcome: dict) -> dict:
    """Score cue and live-update families. Only an intervention_ack with
    evidence=model_receipt establishes receipt; harness acceptance does not."""
    if cell['family'] not in LEGACY_FAMILIES:
        return score_extended(cell, events, outcome)
    issues, eligible, valid, content, stream, arrivals = _prepare(events, outcome, legacy=True)
    first_text = content[0]['offset_ms'] if content else None
    final_text = content[-1]['offset_ms'] if content else None

    intervention, receipt = cell.get('intervention'), None
    if intervention:
        acks = [e for e in valid if e.get('kind') == 'intervention_ack']
        model_receipts = [e for e in acks if e.get('evidence') == 'model_receipt']
        harness_receipts = [e for e in acks if e.get('evidence') == 'harness_accepted']
        if model_receipts:
            receipt = model_receipts[0]['offset_ms']
            if len(model_receipts) != 1:
                issues.append('ambiguous_model_receipt')
                eligible = False
        elif harness_receipts:
            receipt = harness_receipts[0]['offset_ms']
            issues.append('model_receipt_unverified')
            eligible = False
        else:
            issues.append('intervention_unacknowledged')
            eligible = False
        if receipt is not None and receipt < intervention['at_ms']:
            issues.append('receipt_before_scheduled_intervention')
            eligible = False

    other = None if cell['family'] == 'revised_finish' else 'unexpected_text_outside_cues'
    cues = _numbered(stream, arrivals, r'^[ \t]*([1-4])\.', issues, 'empty_cue_', other)
    boundaries = []
    if cell['family'] == 'revised_finish':
        target = receipt + intervention['remaining_ms'] if receipt is not None else None
        boundaries.append(_boundary('final_text', target, final_text))
    else:
        if [cue['number'] for cue in cues] != [1, 2, 3, 4]:
            issues.append('cue_order')
        for index, target in enumerate(cell['cue_offsets_ms'], 1):
            matching = [cue for cue in cues if cue['number'] == index]
            observed = matching[0]['offset_ms'] if matching else None
            if not matching:
                issues.append(f'missing_cue_{index}')
            elif len(matching) > 1:
                issues.append(f'duplicate_cue_{index}')
            if intervention:
                if receipt is None:
                    # Only the targets before the scheduled pause remain observable.
                    if target >= intervention['at_ms']:
                        target = None
                elif observed is None or observed >= receipt:
                    target += intervention['pause_ms']
            boundaries.append(_boundary(f'cue_{index}', target, observed))
        if intervention and receipt is not None:
            end_pause = receipt + intervention['pause_ms']
            if any(receipt <= e['offset_ms'] < end_pause for e in content):
                issues.append('output_during_pause')
    for boundary in boundaries:
        _off_target(boundary, issues)
    issues = list(dict.fromkeys(issues))
    return {'boundaries': boundaries, 'timing_eligible': eligible,
            'timing_pass': _strict_pass(boundaries, issues) if eligible else None,
            'issues': issues, 'intervention_receipt_ms': receipt,
            'first_text_ms': first_text, 'final_text_ms': final_text,
            'observed_cue_offsets_ms': [cue['offset_ms'] for cue in cues]}


def _coverage(measurement, content):
    """Each quarter of the requested visible interval must contain visible text."""
    start, end = measurement['visible_start_ms'], measurement['visible_end_ms']
    bins, width = [], (end - start) / 4
    for index in range(4):
        lower, upper = start + index * width, start + (index + 1) * width
        count = sum(lower <= e['offset_ms'] < upper or (index == 3 and e['offset_ms'] == end) for e in content)
        bins.append({'index': index, 'start_ms': lower, 'end_ms': upper,
                     'content_event_count': count, 'occupied': bool(count)})
    occupied = sum(item['occupied'] for item in bins)
    return {'start_ms': start, 'end_ms': end, 'bins': bins, 'occupied_bins': occupied, 'pass': occupied == 4}


def score_extended(cell: dict, events: list[dict], outcome: dict) -> dict:
    """Score the requested visible boundaries of the other eight families."""
    issues, eligible, _, content, stream, arrivals = _prepare(events, outcome, legacy=False)
    measurement = cell['measurement']
    kind = measurement['kind']
    first_text = content[0]['offset_ms'] if content else None
    final_text = content[-1]['offset_ms'] if content else None

    markers = []
    if kind == 'visible_phases':
        cursor = 0
        for line in stream.splitlines(keepends=True):
            trimmed = line.lstrip(' \t')
            marker = next((value for value in measurement['markers'] if trimmed.startswith(value)), None)
            if marker:
                markers.append({'marker': marker, 'offset_ms': arrivals[cursor + len(line) - len(trimmed)]})
            elif re.match(r'PHASE\s+\d+:', trimmed):
                issues.append('unexpected_phase_marker')
            cursor += len(line)
        if [record['marker'] for record in markers] != measurement['markers']:
            issues.append('phase_marker_order')
        for marker in measurement['markers']:
            count = sum(record['marker'] == marker for record in markers)
            if not count:
                issues.append('missing_phase_marker:' + marker)
            elif count > 1:
                issues.append('duplicate_phase_marker:' + marker)

    boundaries = []
    for requested in cell['requested_boundaries']:
        via = requested['observed_via']
        if via == 'marker':
            matching = [r for r in markers if r['marker'] == requested['marker']]
            observed = matching[0]['offset_ms'] if matching else None
        else:
            observed = first_text if via == 'first_text' else final_text
        boundaries.append(_boundary(requested['label'], requested['requested_ms'], observed))
        if observed is None:
            issues.append('missing_boundary:' + requested['label'])
        else:
            _off_target(boundaries[-1], issues)

    coverage = None
    if kind in {'output_window', 'reason_then_output'}:
        coverage = _coverage(measurement, content)
        if not coverage['pass']:
            issues.append('insufficient_visible_coverage')

    sketches = []
    if kind == 'crazy_eights':
        sketches = _numbered(stream, arrivals, r'^[ \t]*(\d+)\.', issues, 'empty_sketch:',
                             'unexpected_text_outside_sketches')
        if [record['number'] for record in sketches] != list(range(1, 9)):
            issues.append('sketch_numbering')
        if len(sketches) != 8:
            issues.append('sketch_count')

    observable_pass = _strict_pass(boundaries, issues) if eligible else None
    # Private pauses and task switches are unobservable, so they are never timing-eligible.
    timing_eligible = eligible and kind != 'internal_phases'
    if kind == 'internal_phases':
        issues.append('internal_phase_control_unobservable')
    return {'boundaries': boundaries, 'timing_eligible': timing_eligible,
            'timing_pass': observable_pass if timing_eligible else None,
            'observable_timing_pass': observable_pass, 'issues': list(dict.fromkeys(issues)),
            'first_text_ms': first_text, 'final_text_ms': final_text, 'observed_cue_offsets_ms': [],
            'visible_coverage': coverage, 'phase_markers': markers, 'sketches': sketches}
