"""Observe the SVG files in a run's container by hashing them about once per second.

The endpoint of a run is an interval: for each file, the last snapshot k whose hash differs
from snapshot k-1 bounds its last content change to [start of k-1, end of k]; the run
endpoint is [max lower, max upper] over files. It says nothing about private reasoning or
drawing quality. `validate_svg` checks structural rendering safety only.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time
import xml.etree.ElementTree as ET

SVG_NS = 'http://www.w3.org/2000/svg'

# Runs inside the task container; prints metadata only, never file contents.
SNAPSHOT_SCRIPT = r'''
import hashlib,json,os,stat
out={}
for i in range(1,9):
    name='sketch-%02d.svg'%i
    path=os.path.join('/workspace',name)
    fd=None
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            out[name]={'status':'not_regular'}
            continue
        if before.st_size>1048576:
            out[name]={'status':'too_large'}
            continue
        data=bytearray()
        while len(data)<1048576:
            chunk=os.read(fd,min(65536,1048576-len(data)))
            if not chunk: break
            data.extend(chunk)
        after=os.fstat(fd)
        current=os.stat(path,follow_symlinks=False)
        fields=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        if fields(before)!=fields(after) or fields(after)!=fields(current) or len(data)!=after.st_size:
            out[name]={'status':'unstable'}
        else:
            out[name]={'status':'ok','sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
    except FileNotFoundError:
        out[name]={'status':'missing'}
    except Exception as exc:
        out[name]={'status':'error','error_type':type(exc).__name__}
    finally:
        if fd is not None: os.close(fd)
print(json.dumps(out))
'''


def names(count):
    return [f'sketch-{i:02d}.svg' for i in range(1, count + 1)]


def validate_svg(data):
    """UTF-8 SVG root, finite positive viewBox, allowed elements, no scripts or external
    resources, and at least one drawable shape."""
    errors, drawable = [], 0
    try:
        decoded = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        return {'valid': False, 'errors': ['SVG must be UTF-8']}
    if re.search(r'<!\s*(DOCTYPE|ENTITY)|<\?(?!xml\s)', decoded, re.I):
        return {'valid': False, 'errors': ['DTD or entity declaration']}
    try:
        root = ET.fromstring(decoded)
    except (ET.ParseError, ValueError):
        return {'valid': False, 'errors': ['Invalid XML']}
    if root.tag != '{' + SVG_NS + '}svg':
        errors.append('Root must be SVG in the SVG namespace')
    try:
        box = [float(v) for v in re.split(r'[\s,]+', root.attrib['viewBox'].strip())]
        if len(box) != 4 or not all(math.isfinite(v) for v in box) or box[2] <= 0 or box[3] <= 0:
            raise ValueError()
    except (KeyError, ValueError):
        errors.append('A finite positive viewBox is required')
    shapes = {'path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon'}
    allowed = shapes | {'svg', 'g', 'defs', 'title', 'desc', 'text', 'tspan', 'style', 'linearGradient',
                        'radialGradient', 'stop', 'clipPath', 'mask', 'pattern', 'symbol', 'use', 'marker'}

    def unsafe_css(value):
        if '@' in value or '\\' in value or '/*' in value or re.search(r'expression\s*\(', value, re.I):
            return True
        urls = re.findall(r'url\s*\((.*?)\)', value, re.I | re.S)
        if re.search(r'url\s*\(', value, re.I) and not urls:
            return True
        return any(not re.fullmatch(r'#[A-Za-z_][\w:.-]*', url.strip().strip('\'"')) for url in urls)

    for node in root.iter():
        local = node.tag.removeprefix('{' + SVG_NS + '}')
        if not node.tag.startswith('{' + SVG_NS + '}') or local not in allowed:
            errors.append('Disallowed element: ' + local)
        drawable += local in shapes
        if local == 'style' and unsafe_css(''.join(node.itertext())):
            errors.append('Unsafe stylesheet')
        for key, value in node.attrib.items():
            attr = key.rsplit('}', 1)[-1]
            if attr.lower().startswith('on') or attr.lower() in {'src', 'base'}:
                errors.append('Disallowed attribute: ' + attr)
            if attr == 'href' and not re.fullmatch(r'#[A-Za-z_][\w:.-]*', value):
                errors.append('External or embedded resource')
            if unsafe_css(value):
                errors.append('Unsafe attribute value')
    if not drawable:
        errors.append('No vector drawing elements')
    return {'valid': not errors, 'errors': sorted(set(errors))}


def summarize(rows, origin_ns, count, exported, export_complete=True):
    """Endpoint interval and qualification from snapshot rows.

    rows: [{'kind', 'before_monotonic_ns', 'after_monotonic_ns', 'files', 'error'}];
    exported: {name: final bytes copied out of the container}."""
    reasons = []
    for prev, row in zip(rows, rows[1:]):
        if row['before_monotonic_ns'] < prev['after_monotonic_ns']:
            reasons.append('invalid_history')
    if not rows or rows[0]['kind'] != 'baseline' or rows[-1]['kind'] != 'final' or \
            any(r['kind'] != 'poll' for r in rows[1:-1]):
        reasons.append('missing_baseline_or_final')
    if any(r['error'] or set(r['files']) != set(names(count)) for r in rows):
        reasons.append('snapshot_error')
    if rows and any(v.get('status') != 'missing' for v in rows[0]['files'].values()):
        reasons.append('baseline_not_empty')
    if not export_complete:
        reasons.append('invalid_or_incomplete_export_receipt')
    intervals, valid = [], []
    for name in names(count):
        interval, last, seen = None, None, False
        for index, row in enumerate(rows):
            item = row['files'].get(name, {})
            identity = item.get('sha256') if item.get('status') == 'ok' else None
            if item.get('status') not in {'ok', 'missing'}:
                reasons.append('unstable_or_unreadable_observation')
            if item.get('status') == 'missing' and seen:
                reasons.append('file_disappeared')
            if index and identity is not None and identity != last:
                interval = [rows[index - 1]['before_monotonic_ns'], row['after_monotonic_ns']]
            seen |= identity is not None
            last = identity
        terminal = rows[-1]['files'].get(name, {}) if rows else {}
        if terminal.get('status') != 'ok':
            reasons.append(name + ': missing terminal file')
        if interval is None:
            reasons.append(name + ': no observed creation or change after baseline')
        else:
            intervals.append([(v - origin_ns) / 1e9 for v in interval])
        data = exported.get(name)
        if data is None or hashlib.sha256(data).hexdigest() != terminal.get('sha256'):
            reasons.append(name + ': export hash differs from terminal observation')
        valid.append(data is not None and validate_svg(data)['valid'])
    qualified = not reasons
    return {'timing_qualified': qualified, 'reasons': sorted(set(reasons)),
            'endpoint_interval_seconds': [max(v[0] for v in intervals), max(v[1] for v in intervals)]
            if qualified else None, 'svg_count': count, 'valid_svgs': sum(valid)}


class ArtifactObserver:
    """Synchronous empty baseline before dispatch, then a polling thread, then a final snapshot."""

    def __init__(self, container, root, sketch_count=8, poll_interval_s=1.0):
        self.container, self.root, self.count = container, Path(root), sketch_count
        self.script = SNAPSHOT_SCRIPT.replace('range(1,9)', f'range(1,{sketch_count + 1})')
        self.poll_interval_s, self.rows = poll_interval_s, []
        self._stop, self._thread = threading.Event(), None

    def _snapshot(self, kind):
        before, files, error = time.monotonic_ns(), {}, None
        try:
            files = self.container.exec_python(self.script, timeout=5, isolated=True)
        except Exception as exc:
            error = type(exc).__name__
        row = {'sequence': len(self.rows), 'kind': kind, 'before_monotonic_ns': before,
               'after_monotonic_ns': time.monotonic_ns(), 'files': files, 'error': error}
        self.rows.append(row)
        with (self.root / 'svg-observations.jsonl').open('a') as stream:
            stream.write(json.dumps(row, sort_keys=True) + '\n')
        return row

    def start(self):
        baseline = self._snapshot('baseline')
        if baseline['error'] or any(v.get('status') != 'missing' for v in baseline['files'].values()):
            raise RuntimeError('SVG observer requires an empty baseline before dispatch')
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.wait(self.poll_interval_s):
            self._snapshot('poll')

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=6)
        self._snapshot('final')

    def summary(self, origin_ns, outcome):
        export = json.loads((self.root / 'workspace-export.json').read_text()) \
            if (self.root / 'workspace-export.json').exists() else {'truncated': True}
        exported = {n: (self.root / 'workspace' / n).read_bytes() for n in names(self.count)
                    if (self.root / 'workspace' / n).exists()}
        result = summarize(self.rows, origin_ns, self.count, exported,
                           export.get('truncated') is False and export.get('skipped') == [])
        # Only a completed run whose container had no leftover processes is timing-qualified.
        if outcome.get('state') != 'completed' or \
                outcome.get('container_cleanup', {}).get('naturally_quiescent') is not True:
            result.update(timing_qualified=False, endpoint_interval_seconds=None,
                          reasons=result['reasons'] + ['incomplete_or_nonquiescent_native_tail'])
        return result
