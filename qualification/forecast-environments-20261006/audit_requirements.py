"""Read source declarations and cached image metadata. Never launch a container."""
from collections import Counter
import datetime
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import subprocess
import tomllib
import yaml

REPO = Path('/Users/michaelofengenden/Developer/TimeResearch/AgentTime-v1.1')
OUT = Path(__file__).resolve().parent
KEYS = ('cpus', 'memory_mb', 'storage_mb', 'gpus', 'gpu_types', 'os', 'allow_internet')


@lru_cache(maxsize=None)
def pin(path):
    path = path.resolve()
    if not path.is_relative_to(REPO):
        raise ValueError('Source escaped the repository')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'path': str(path.relative_to(REPO)), 'sha256': digest}


def image_info(reference):
    fmt = '{"id":{{json .Id}},"repo_digests":{{json .RepoDigests}},"os":{{json .Os}},"architecture":{{json .Architecture}}}'
    result = subprocess.run(['docker', 'image', 'inspect', '--format', fmt, reference],
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        return {'reference': reference, 'cached': False, 'inspection_error': result.stderr.strip()}
    return {'reference': reference, 'cached': True, **json.loads(result.stdout)}


def main():
    inventory = REPO / 'benchmarks/inventory/tasks.json'
    rows = json.loads(inventory.read_text())['rows']
    infos = {}
    records = []
    for row in rows:
        family = row['family_id']
        paths = [REPO / e['path'] for e in row['components']['environment']['evidence'] if e.get('path')]
        tomls = [p for p in paths if p.name == 'task.toml' and p.is_file()]
        inference = None
        if not tomls and family == 'posttrain':
            dockerfiles = [p for p in paths if p.name == 'Dockerfile' and p.parent.name == 'environment']
            if len(dockerfiles) == 1:
                sibling = dockerfiles[0].parent.parent / 'task.toml'
                if sibling.is_file():
                    tomls = [sibling]
                    inference = 'Shared native adapter template beside this task\'s Dockerfile; not a rendered task or launch configuration.'
        declarations = []
        image_refs = []
        for path in tomls:
            config = tomllib.loads(path.read_text())
            environment = config.get('environment', {})
            declarations.append({'source': pin(path), 'source_kind': 'harbor_task_or_template',
                                 'subject_resources': {k: environment[k] for k in KEYS if k in environment},
                                 'agent_timeout_seconds': config.get('agent', {}).get('timeout_sec'),
                                 'timer_healthcheck_declared': 'timer' in str(environment.get('healthcheck', {})),
                                 'inference': inference})
            if environment.get('docker_image'):
                image_refs.append(environment['docker_image'])
        if family == 'metr':
            variant = row['candidate_id'].split('/', 1)[1]
            for path in [p for p in paths if p.name == 'manifest.yaml' and p.is_file()]:
                task = yaml.safe_load(path.read_text())['tasks'][variant]
                declarations.append({'source': pin(path), 'source_kind': 'metr_selected_variant',
                                     'selected_variant': variant,
                                     'subject_resources': task.get('resources', {}),
                                     'inference': None})
        bindings = [p for p in paths if p.name == 'image-bindings.json' and p.is_file()]
        if family == 'program' and not bindings:
            path = REPO / 'benchmarks/cache/code-assets-20261002/programbench' / row['candidate_id'] / 'task/image-bindings.json'
            if path.is_file():
                bindings = [path]
        image_bindings = []
        for path in bindings:
            data = json.loads(path.read_text())
            reference = data.get('image_content_id')
            if reference:
                image_refs.append(reference)
            image_bindings.append({'source': pin(path), 'image_content_id': reference,
                                   'image_repo_digest': data.get('image_repo_digest')})
        for ref in image_refs:
            if ref not in infos:
                infos[ref] = image_info(ref)
        records.append({'slot_id': row['slot_id'], 'family_id': family,
                        'candidate_id': row['candidate_id'], 'native_declarations': declarations,
                        'image_bindings': image_bindings,
                        'cached_image_observations': [infos[r] for r in image_refs],
                        'definition_paths_present': [pin(p) for p in paths if p.is_file()],
                        'remaining_environment_gap': row['components']['environment'].get('remaining_gap'),
                        'selected_runtime_profile': None, 'qualified': False, 'dispatch_allowed': False})
    docker = subprocess.run(['docker', 'info', '--format',
        '{"os":{{json .OperatingSystem}},"architecture":{{json .Architecture}},"cpus":{{.NCPU}},"memory_bytes":{{.MemTotal}}}'],
        capture_output=True, text=True, check=True, timeout=30)
    report = {'schema_version': 1, 'created_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'scope': 'read_only_source_and_local_cache_audit_not_runtime_qualification',
              'host_strategy': 'matching_benchmark_environment_independent_fresh_copy',
              'inventory': pin(inventory), 'local_docker': json.loads(docker.stdout),
              'slot_count': len(records), 'families': dict(Counter(r['family_id'] for r in records)),
              'slots_with_harbor_resource_declarations': sum(any(
                  d['source_kind'] == 'harbor_task_or_template' and bool(d['subject_resources'])
                  for d in r['native_declarations']) for r in records),
              'slots_with_other_native_resource_declarations': sum(any(
                  d['source_kind'] != 'harbor_task_or_template' and bool(d['subject_resources'])
                  for d in r['native_declarations']) for r in records),
              'slots_with_content_pinned_image_bindings': sum(bool(r['image_bindings']) for r in records),
              'slots_with_an_image_reference_inspected': sum(bool(r['cached_image_observations']) for r in records),
              'slots_with_a_matching_image_in_local_cache': sum(any(i['cached'] for i in r['cached_image_observations']) for r in records),
              'qualified_slots': 0, 'rows': records}
    assert len(records) == len({r['slot_id'] for r in records}) == 200
    (OUT / 'environment-requirements.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'rows'}, indent=2))


if __name__ == '__main__':
    main()
