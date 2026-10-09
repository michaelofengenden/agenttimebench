"""A disposable native subject. Its witness is independent of the controller ledger."""
import base64
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import sys
import time
import uuid

mode, attempt_id, execution_id, prompt_b64, work_seconds_text = sys.argv[1:]
work_seconds = float(work_seconds_text)
if mode not in {'complete', 'crash', 'grade_failure', 'wrong_answer'} or not math.isfinite(work_seconds) or not 0 <= work_seconds <= 5:
    raise ValueError('Unsupported disposable fixture mode or delay')
prompt = base64.b64decode(prompt_b64, validate=True)
logs = Path('/logs/agent')
logs.mkdir(parents=True, exist_ok=True)
clock_id = platform.node() + ':' + Path('/proc/sys/kernel/random/boot_id').read_text().strip() + ':monotonic_ns'
sequence = 0


def emit(kind, payload):
    global sequence
    sequence += 1
    event = {'schema_version': 1, 'attempt_id': attempt_id, 'execution_id': execution_id,
             'event_id': str(uuid.uuid4()), 'sequence': sequence, 'clock_id': clock_id,
             'monotonic_ns': time.monotonic_ns(), 'recorded_at': datetime.now(timezone.utc).isoformat(),
             'kind': kind, 'payload': payload}
    with (logs / 'native-events.jsonl').open('ab') as output:
        output.write(json.dumps(event, sort_keys=True, separators=(',', ':')).encode() + b'\n')
        output.flush()
        os.fsync(output.fileno())


with (logs / 'release-witness.txt').open('ab') as witness:
    witness.write(attempt_id.encode() + b'\n')
    witness.flush()
    os.fsync(witness.fileno())
emit('prompt_released', {'prompt_sha256': hashlib.sha256(prompt).hexdigest(), 'fixture_mode': mode})
if mode == 'crash':
    os._exit(70)
time.sleep(work_seconds)
working_answer = logs / 'working-answer.txt'
working_answer.write_bytes(b'41' if mode == 'wrong_answer' else b'42')
answer = working_answer.read_bytes()
with (logs / 'sealed-submission.bin').open('xb') as sealed:
    sealed.write(answer)
    sealed.flush()
    os.fsync(sealed.fileno())
emit('native_terminal', {'submission_b64': base64.b64encode(answer).decode(),
                        'submission_sha256': hashlib.sha256(answer).hexdigest()})
# Deliberate work after the native terminal marker must not enlarge primary time or change the grade.
time.sleep(.2)
working_answer.write_bytes(b'late overwrite')
emit('owned_work_drained', {})
emit('journal_closed', {'final_sequence': sequence + 1})
