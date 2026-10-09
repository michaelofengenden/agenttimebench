"""Project controller observations for a read-only natural-pilot dashboard.

This module never dispatches, grades, releases a claim, or reads task contents.
An unknown observation keeps its reservation and is not counted as known active.
"""
import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import time


def _utc(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed if parsed.utcoffset() is not None else None
    except ValueError:
        return None


def _number(value):
    return (type(value) in (float, int) and math.isfinite(value) and value >= 0)


def merge_observation(previous, incoming):
    """Retain claim/release facts when a worker publishes incomplete metadata."""
    prior = dict(previous) if isinstance(previous, dict) else {}
    valid = isinstance(incoming, dict) and bool(incoming)
    if valid and prior.get('attempt_id'):
        valid = incoming.get('attempt_id') == prior['attempt_id']
    if valid and prior.get('native_release_verified') is True:
        valid = (incoming.get('native_release_verified') is True
                 and incoming.get('prompt_released_at') == prior.get('prompt_released_at'))
    if valid:
        for key in ('native_terminal_at', 'owned_work_drained_at'):
            if prior.get(key) and incoming.get(key) != prior[key]:
                valid = False
        if prior.get('stop_verified') is True and incoming.get('stop_verified') is not True:
            valid = False
    if not valid:
        return dict(prior, observation_invalid=True, blocked=True)
    return dict(incoming)


def project_status(manifest, observations, now, history=()):
    clock = _utc(now)
    if clock is None:
        raise ValueError('Observation time must include a timezone')
    rows = []
    for task in manifest['tasks']:
        obs = observations.get(task['task_id'], {})
        if not isinstance(obs, dict):
            obs = {}
        released = obs.get('native_release_verified') is True and _utc(obs.get('prompt_released_at')) is not None
        beat = _utc(obs.get('heartbeat_at'))
        fresh = beat is not None and 0 <= (clock - beat).total_seconds() <= 30
        stopped = obs.get('stop_verified') is True
        drained = _utc(obs.get('owned_work_drained_at')) is not None
        terminal = _utc(obs.get('native_terminal_at')) is not None
        state = 'preparing'
        detail = 'Native worker, task interface and session restoration qualification pending.'
        if released:
            if terminal and drained and stopped:
                state, detail = 'finished', 'Native work finished. Grade and archive status are shown separately.'
            elif stopped:
                state, detail = 'blocked', 'Execution stopped without complete native completion evidence.'
            elif not fresh or obs.get('observation_invalid') is True:
                state, detail = 'unknown', 'Worker observation is missing or stale; reservation remains held.'
            elif terminal and drained:
                state, detail = 'collecting', 'Native work finished; preserving the session and confirming worker shutdown.'
            elif obs.get('verified_wait') in ('tool', 'subagent', 'owned_process', 'rate_limit'):
                state, detail = 'waiting', 'Native work is waiting for ' + obs['verified_wait'].replace('_', ' ') + '.'
            else:
                state, detail = 'running', 'Native task prompt released; execution remains observed.'
        elif obs.get('blocked') is True or obs.get('observation_invalid') is True:
            state, detail = 'blocked', 'Preparation cannot proceed until its recorded gate is cleared.'
        elif obs.get('admission_ready') is True:
            state, detail = 'ready', 'Staged at the release barrier; final launch checks remain.'

        timing = {'status': 'not_started', 'elapsed_seconds': None, 'runtime_seconds': None, 'reason': None}
        if released:
            timing['status'] = 'live' if state in ('running', 'waiting') else 'pending'
            if _number(obs.get('elapsed_seconds')):
                timing['elapsed_seconds'] = obs['elapsed_seconds']
            admitted = obs.get('timing', {})
            if isinstance(admitted, dict):
                if state == 'finished' and admitted.get('status') == 'valid' and _number(admitted.get('runtime_seconds')):
                    timing.update(status='valid', runtime_seconds=admitted['runtime_seconds'])
                elif admitted.get('status') == 'invalid':
                    timing.update(status='invalid', reason='Native timing did not pass admission.')

        raw_grade = obs.get('grade', {})
        grade = {'status': 'not_started', 'value': None, 'scale': None}
        if isinstance(raw_grade, dict) and raw_grade.get('status') in ('queued', 'running', 'available', 'failed', 'unavailable'):
            grade['status'] = raw_grade['status']
            if state == 'finished' and grade['status'] == 'available' and _number(raw_grade.get('value')):
                grade['value'] = raw_grade['value']
                grade['scale'] = raw_grade.get('scale') if raw_grade.get('scale') in ('fraction', 'percent', 'points') else None
            elif grade['status'] == 'available':
                grade['status'] = 'unavailable'

        archive = {'status': 'none', 'restore_status': 'unchecked'}
        raw_archive = obs.get('archive', {})
        if isinstance(raw_archive, dict):
            if raw_archive.get('status') in ('local', 'transferring', 'acknowledged', 'failed'):
                archive['status'] = raw_archive['status']
            if raw_archive.get('restore_status') in ('verified', 'failed'):
                archive['restore_status'] = raw_archive['restore_status']

        rows.append({
            'task_id': task['task_id'], 'label': task['label'], 'benchmark': task['benchmark'],
            'attempt_id': obs.get('attempt_id') if isinstance(obs.get('attempt_id'), str) else None,
            'state': state, 'detail': detail,
            'heartbeat_at': obs.get('heartbeat_at') if beat else None,
            'prompt_released_at': obs.get('prompt_released_at') if released else None,
            'native_terminal_at': obs.get('native_terminal_at') if terminal else None,
            'owned_work_drained_at': obs.get('owned_work_drained_at') if drained else None,
            'stop_verified': stopped, 'timing': timing, 'grade': grade, 'archive': archive,
            'issue': None if state not in ('blocked', 'unknown') else {'code': state, 'message': detail},
        })
    counts = {s: sum(r['state'] == s for r in rows) for s in
              ('preparing', 'ready', 'running', 'waiting', 'collecting', 'finished', 'blocked', 'unknown')}
    counts['active_native'] = counts['running'] + counts['waiting']
    counts['capacity_held'] = sum(bool(r['attempt_id']) and r['state'] != 'finished' for r in rows)
    samples = [dict(x) for x in history if isinstance(x, dict) and _utc(x.get('at'))
               and all(type(x.get(k)) is int and x[k] >= 0 for k in ('running', 'waiting', 'unknown'))]
    samples.append({'at': now, 'running': counts['running'], 'waiting': counts['waiting'], 'unknown': counts['unknown']})
    # Preserve extrema and every transition; avoid unbounded duplicate heartbeat samples.
    condensed = []
    for sample in samples:
        values = tuple(sample[k] for k in ('running', 'waiting', 'unknown'))
        if len(condensed) >= 2 and values == tuple(condensed[-1][k] for k in ('running', 'waiting', 'unknown')) == tuple(condensed[-2][k] for k in ('running', 'waiting', 'unknown')):
            condensed[-1] = sample
        else:
            condensed.append(sample)
    counts['peak_active_native'] = max((x['running'] + x['waiting'] for x in condensed), default=0)
    phase = 'preparing'
    if counts['active_native']:
        phase = 'running'
    elif counts['unknown']:
        phase = 'paused'
    elif counts['collecting']:
        phase = 'collecting'
    elif rows and counts['finished'] == len(rows):
        phase = 'complete' if all(r['archive']['status'] == 'acknowledged' and r['grade']['status'] == 'available' for r in rows) else 'collecting'
    blockers = manifest.get('blockers', [])
    pause = '; '.join(x['message'] for x in blockers if isinstance(x, dict) and isinstance(x.get('message'), str)) or None
    return {
        'schema_version': 1, 'campaign_id': manifest['campaign_id'], 'snapshot_at': now,
        'source_observed_at': now, 'refresh_seconds': 5, 'stale_after_seconds': 30,
        'campaign': {'phase': phase, 'model': 'claude-opus-5-5', 'effort': 'max',
                     'route': 'Michael subscription', 'selected_tasks': len(rows),
                     'target_concurrency': manifest['target_concurrency'], 'paused_reason': pause},
        'counts': counts, 'tasks': rows, 'concurrency': condensed,
    }


def write_snapshot(path, value):
    temp = path.with_suffix('.writing')
    with temp.open('w') as stream:
        json.dump(value, stream, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', required=True, type=Path)
    parser.add_argument('--watch', action='store_true')
    args = parser.parse_args()
    manifest_file = args.campaign / 'manifest.json'
    snapshot_file = args.campaign / 'public-status.json'
    history = []
    observations = {}
    if snapshot_file.exists():
        previous = json.loads(snapshot_file.read_text())
        history = previous.get('concurrency', [])
        # The public prior projection can preserve known facts across observer restart.
        # It never authorizes dispatch or scientific timing admission.
        for row in previous.get('tasks', []):
            if row.get('attempt_id'):
                observations[row['task_id']] = dict(row, native_release_verified=bool(row.get('prompt_released_at')))
    while True:
        manifest = json.loads(manifest_file.read_text())
        for task in manifest['tasks']:
            path = args.campaign / 'observations' / (task['task_id'] + '.json')
            if path.exists():
                try:
                    observations[task['task_id']] = merge_observation(
                        observations.get(task['task_id']), json.loads(path.read_text()))
                except (ValueError, OSError):
                    observations.setdefault(task['task_id'], {})['observation_invalid'] = True
                    observations[task['task_id']]['blocked'] = True
            elif task['task_id'] in observations:
                observations[task['task_id']]['observation_invalid'] = True
        now = datetime.now(timezone.utc).isoformat()
        result = project_status(manifest, observations, now, history)
        write_snapshot(snapshot_file, result)
        history = result['concurrency']
        if not args.watch:
            return
        time.sleep(5)


if __name__ == '__main__':
    main()
