"""Disposable, model-free isolation/context check in a cached ProgramBench image.

Only the two containers created here may be removed. No credentials, host mounts,
real model endpoints, task execution or task grading are used.
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

BASE = Path(__file__).resolve().parent
IMAGE = 'sha256:245ec38fb91de703a186403531046260c171c82ca6511ae1fc2eeb18af43eb16'
CLI_SHA = '1e08503dbdf3c2cb0d706d32f3408277388d1c76ef108673e8fe42c1b322925b'
LABEL = 'agenttime.forecast-clone-qualification'


def docker(*args, check=True, timeout=45):
    return subprocess.run(['docker', *args], text=True, capture_output=True,
                          check=check, timeout=timeout)


def inspect(cid):
    return json.loads(docker('inspect', cid).stdout)[0]


def python(cid, code):
    return docker('exec', cid, 'python3', '-c', code)


def main():
    os.umask(0o077)
    nonce = uuid.uuid4().hex
    out = BASE / ('clone-isolation-' + nonce[:10])
    out.mkdir()
    owned = {}
    report = {'created_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'nonce': nonce, 'purpose': 'model_free_local_diagnostic',
              'study_requests': 0, 'real_credentials_used': False,
              'pinned_image': IMAGE, 'native_worker_qualified': False,
              'resource_override': {'cpus': 1, 'memory_bytes': 2147483648,
                                    'network': 'none', 'user': '10001:10001'},
              'resource_override_is_fixture_only': True, 'containers': {}, 'checks': {}}
    try:
        binary = BASE / 'linux-cli/claude'
        assert hashlib.sha256(binary.read_bytes()).hexdigest() == CLI_SHA
        bundle = out / 'probe'
        shutil.copytree(BASE / 'clone-probe', bundle)
        shutil.copy2(binary, bundle / 'claude')
        # Docker copies preserve file modes. The fixture user needs read/execute only.
        for path in [bundle, *bundle.rglob('*')]:
            path.chmod(0o755 if path.is_dir() or path.name == 'claude' else 0o644)
        for role in ('forecast', 'natural'):
            cid = docker('create', '--pull=never', '--platform=linux/amd64',
                '--name', 'at-forecast-check-' + nonce[:10] + '-' + role,
                '--label', LABEL + '=' + nonce, '--network=none',
                '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pids-limit=128',
                '--cpus=1', '--memory=2g', '--memory-swap=2g', '--user=10001:10001',
                '--workdir=/tmp', '--entrypoint=/bin/sleep', IMAGE, '600').stdout.strip()
            owned[role] = cid
            (out / 'owned-containers.json').write_text(json.dumps(owned, indent=2) + '\n')
            docker('cp', str(bundle), cid + ':/probe', timeout=90)
            docker('start', cid)
            state = inspect(cid)
            assert state['Config']['Labels'][LABEL] == nonce
            assert state['Image'] == IMAGE
            assert state['HostConfig']['NetworkMode'] == 'none'
            assert state['Mounts'] == []
            assert state['HostConfig']['Privileged'] is False
            report['containers'][role] = {
                'id': cid, 'image': state['Image'], 'mounts': state['Mounts'],
                'network_mode': state['HostConfig']['NetworkMode'],
                'user': state['Config']['User'],
                'nano_cpus': state['HostConfig']['NanoCpus'],
                'memory_bytes': state['HostConfig']['Memory'],
                'platform': python(cid, 'import platform,json; print(json.dumps({"system":platform.system(),"machine":platform.machine()}))').stdout.strip()}
        roots = ['/tmp/qualification-home/.claude', '/tmp/qualification-config',
                 '/tmp/qualification-workspace', '/tmp/qualification-memory',
                 '/tmp/qualification-archive']
        marker = 'FORECAST_ONLY_' + nonce
        write = ('from pathlib import Path\n'
                 'for root in ' + repr(roots) + ':\n'
                 ' p=Path(root); p.mkdir(parents=True,exist_ok=True); '
                 '(p/"marker.txt").write_text(' + repr(marker) + ')\n')
        detect = ('from pathlib import Path; import json; '
                  'print(json.dumps({p:(Path(p)/"marker.txt").exists() for p in ' + repr(roots) + '}))')
        python(owned['forecast'], write)
        f_markers = json.loads(python(owned['forecast'], detect).stdout)
        n_markers = json.loads(python(owned['natural'], detect).stdout)
        assert all(f_markers.values()) and not any(n_markers.values())
        report['checks']['forecast_markers_present'] = f_markers
        report['checks']['natural_markers_absent'] = {p: not present for p, present in n_markers.items()}
        # Negative control: a contaminated clone must be detected by the same check.
        python(owned['natural'], write)
        contaminated = json.loads(python(owned['natural'], detect).stdout)
        assert all(contaminated.values())
        report['checks']['deliberate_contamination_detected'] = all(contaminated.values())
        python(owned['natural'], 'from pathlib import Path\nfor p in ' + repr(roots) + ': (Path(p)/"marker.txt").unlink()')
        assert not any(json.loads(python(owned['natural'], detect).stdout).values())
        result = docker('exec', owned['forecast'], 'python3', '/probe/probe_in_container.py',
                        check=False, timeout=120)
        (out / 'probe.stdout').write_text(result.stdout)
        (out / 'probe.stderr').write_text(result.stderr)
        assert result.returncode == 0, result.stderr[-1000:]
        capture = out / 'capture'
        capture.mkdir()
        docker('cp', owned['forecast'] + ':/tmp/forecast-capture/.', str(capture))
        detail = json.loads((capture / 'capabilities-report.json').read_text())
        variant = detail['variants'][0]
        assert variant['exit_code'] == 0 and variant['result_is_error'] is False
        assert variant['message_requests'] == 1
        assert variant['tools'] == []
        assert variant['model'] == 'claude-opus-5-5'
        assert variant['effort'] == {'effort': 'max'}
        assert variant['images_received'] == 1 and variant['image_bytes_identical']
        assert variant['native_transcripts_saved'] == 1 and variant['raw_body_files']
        messages = [json.loads(p.read_text()) for p in capture.glob('*/requests/*.json')]
        body = next(m['body'] for m in messages if m['path'].startswith('/v1/messages'))
        context = json.dumps(body)
        assert '# Environment' in context and 'linux' in context.lower()
        assert 'darwin' not in context.lower() and 'macos' not in context.lower()
        assert '/Users/michaelofengenden' not in context
        assert marker not in context
        report['checks']['linux_environment_note_delivered'] = True
        report['checks']['mac_host_note_absent'] = True
        report['checks']['synthetic_image_bytes_received'] = True
        report['checks']['one_fake_model_request'] = True
        report['checks']['native_transcript_and_request_saved'] = True
        report['cli'] = {'version': variant['version'], 'sha256': CLI_SHA,
                         'observed_model_in_fake_request': variant['model'], 'effort': variant['effort']}
        report['probe_passed'] = True
    except BaseException as exc:
        report['probe_passed'] = False
        report['error'] = type(exc).__name__ + ': ' + str(exc)
        raise
    finally:
        cleanup = {}
        for role, cid in owned.items():
            try:
                assert inspect(cid)['Config']['Labels'][LABEL] == nonce
                docker('rm', '--force', cid)
                # A successful list verifies absence without treating daemon failure as absence.
                remaining = docker('ps', '-a', '--no-trunc', '--filter', 'id=' + cid, '--format', '{{.ID}}').stdout.strip()
                cleanup[role] = remaining == ''
            except Exception as exc:
                cleanup[role] = {'error': type(exc).__name__ + ': ' + str(exc)}
        report['cleanup'] = cleanup
        report['all_owned_containers_removed'] = len(cleanup) == 2 and all(v is True for v in cleanup.values())
        (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        (BASE / 'latest-clone-probe.txt').write_text(str(out) + '\n')
        print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
