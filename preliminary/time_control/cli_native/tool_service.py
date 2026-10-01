"""The one tool in the tools arms: a stdio MCP `shell` that runs commands in a networkless,
read-only Docker container with a private tmpfs /workspace.

The native CLI launches this file as an MCP server (`--config tool-service.json`). Each call
is journaled to tool-events.jsonl on the host monotonic clock; calls stop at the run's
backstop, read from tool-control.json. When a call fails (cutoff or output cap), the command
is killed; the run code also closed the container at that point.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time
import uuid

TOOL = 'shell'
DESCRIPTION = ('Run a shell command in your isolated task workspace. '
               'Clock commands and waiting are available. No network access.')
OUTPUT_CAP = 65536            # bytes per stream returned to the model
TOTAL_OUTPUT_CAP = 64 * 1024 * 1024
COMMAND_CAP = 16384

CENSUS_SCRIPT = """import json,os,pathlib
rows=[]
for path in pathlib.Path('/proc').iterdir():
 if not path.name.isdigit() or int(path.name) in (1,os.getpid()): continue
 try:
  raw=(path/'cmdline').read_bytes()
  state=(path/'stat').read_text().rsplit(')',1)[1].split()[0]
  rows.append({'pid':int(path.name),'command':raw.replace(b'\\0',b' ').decode(errors='replace').strip(),'state':state})
 except (FileNotFoundError,ProcessLookupError): pass
print(json.dumps(rows))
"""
EXPORT_SCRIPT = r'''import base64,json,os,stat
files=[];skipped=[];used=0;limit=8*1024*1024
for folder,dirs,names in os.walk('/workspace',followlinks=False):
 dirs[:]=sorted(d for d in dirs if not os.path.islink(os.path.join(folder,d)))
 for name in sorted(names):
  path=os.path.join(folder,name);relative=os.path.relpath(path,'/workspace')
  try:
   fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
   try:
    info=os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_size>limit-used: skipped.append(relative);continue
    data=os.read(fd,info.st_size+1)
   finally: os.close(fd)
   if len(data)>limit-used: skipped.append(relative);continue
   used+=len(data);files.append({'path':relative,'base64':base64.b64encode(data).decode()})
  except OSError: skipped.append(relative)
print(json.dumps({'files':files,'skipped':skipped,'truncated':bool(skipped)}))
'''


class Container:
    """A task container owned by one run; `image` must contain python3."""

    def __init__(self, docker, image, root, container_id=None):
        self.docker, self.image, self.root = docker, image, Path(root)
        self.container_id = container_id

    def _docker(self, args, timeout=30):
        return subprocess.run([self.docker, *args], text=True, capture_output=True, timeout=timeout)

    def create_command(self):
        return [self.docker, 'create', '--pull', 'never', '--network', 'none',
                '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '--log-driver', 'none', '--pids-limit', '64', '--memory', '256m', '--cpus', '1.0',
                '--user', f'{os.getuid()}:{os.getgid()}',
                '--tmpfs', '/tmp:rw,nosuid,nodev,size=64m,mode=1777',
                '--tmpfs', '/workspace:rw,nosuid,nodev,size=64m,mode=1777',
                '--workdir', '/workspace', '--env', 'HOME=/tmp', '--env', 'TMPDIR=/tmp',
                '--entrypoint', 'python3', self.image, '-c', 'import time; time.sleep(86400)']

    def prepare(self):
        created = subprocess.run(self.create_command(), text=True, capture_output=True, timeout=30)
        if created.returncode:
            raise RuntimeError('container creation failed')
        self.container_id = created.stdout.strip()
        info = json.loads(self._docker(['inspect', self.container_id]).stdout or '[{}]')[0]
        if self._docker(['start', self.container_id]).returncode or \
                info.get('HostConfig', {}).get('NetworkMode') != 'none':
            self.close()
            raise RuntimeError('container isolation or startup failed')
        return self.container_id

    def exec_python(self, script, timeout, isolated=False):
        result = self._docker(['exec', self.container_id, 'python3', *(['-I'] if isolated else []), '-c', script],
                              timeout=timeout)
        if result.returncode:
            raise RuntimeError('container python failed')
        return json.loads(result.stdout)

    def export_workspace(self):
        """Copy the final workspace files (up to 8 MiB) to <root>/workspace with hashes."""
        payload = self.exec_python(EXPORT_SCRIPT, 10)
        records = []
        for row in payload['files']:
            data = base64.b64decode(row['base64'], validate=True)
            destination = self.root / 'workspace' / row['path']
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            records.append({'path': row['path'], 'bytes': len(data),
                            'sha256': hashlib.sha256(data).hexdigest()})
        receipt = {'files': records, 'truncated': payload['truncated'], 'skipped': payload['skipped']}
        (self.root / 'workspace-export.json').write_text(json.dumps(receipt, sort_keys=True) + '\n')
        return receipt

    def close(self):
        """Census leftover task processes, export the workspace, then remove the container."""
        residual = None
        if self.container_id:
            try:
                residual = self.exec_python(CENSUS_SCRIPT, 5)
            except Exception:
                pass
            try:
                self.export_workspace()
            except Exception:
                pass
            self._docker(['rm', '--force', self.container_id])
        return {'container_removed': True, 'residual_task_processes': residual,
                'naturally_quiescent': (not residual) if residual is not None else None}


def log(root, kind, **fields):
    event = {'kind': kind, 'monotonic_ns': time.monotonic_ns(), **fields}
    with open(Path(root) / 'tool-events.jsonl', 'a') as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + '\n')


def execute(config, command):
    """Run one shell command in the container; output arrives as JSON text for the model."""
    root = Path(config['root'])
    deadline = json.loads((root / 'tool-control.json').read_text())['deadline_monotonic_ns']
    if time.monotonic_ns() >= deadline:
        raise RuntimeError('tool cutoff')
    call = uuid.uuid4().hex
    log(root, 'tool_start', call_id=call, command=command)
    process = subprocess.Popen([config['docker'], 'exec', '--workdir', '/workspace', config['container_id'],
                                '/bin/sh', '-lc', command], stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    log(root, 'command_process_started', call_id=call, pid=process.pid)
    output, total, status = {'stdout': bytearray(), 'stderr': bytearray()}, 0, 'error'
    selector = selectors.DefaultSelector()
    try:
        for name in output:
            selector.register(getattr(process, name), selectors.EVENT_READ, name)
        while selector.get_map():
            if time.monotonic_ns() >= deadline:
                status = 'cutoff'
                raise RuntimeError('tool cutoff')
            for key, _ in selector.select(min(.05, max(0, (deadline - time.monotonic_ns()) / 1e9))):
                chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                total += len(chunk)
                log(root, 'tool_output', call_id=call, stream=key.data, base64=base64.b64encode(chunk).decode())
                output[key.data].extend(chunk[:max(0, OUTPUT_CAP - len(output[key.data]))])
                if total > TOTAL_OUTPUT_CAP:
                    raise RuntimeError('tool output limit exceeded')
        process.wait(timeout=max(0.001, (deadline - time.monotonic_ns()) / 1e9))
        status = 'completed'
        return {**{k: bytes(v).decode('utf-8', errors='replace') for k, v in output.items()},
                'exit_code': process.returncode, 'output_truncated': total > sum(map(len, output.values()))}
    except BaseException:
        process.kill()
        process.wait(timeout=5)
        raise
    finally:
        selector.close()
        log(root, 'tool_end', call_id=call, status=status, exit_code=process.returncode)


def handle(config, request):
    identity, method = request.get('id'), request.get('method')
    if identity is None:
        return None
    if method == 'initialize':
        result = {'protocolVersion': request.get('params', {}).get('protocolVersion', '2024-11-05'),
                  'capabilities': {'tools': {'listChanged': False}},
                  'serverInfo': {'name': 'agenttime-temporal', 'version': '1'}}
    elif method == 'ping':
        result = {}
    elif method == 'tools/list':
        result = {'tools': [{'name': TOOL, 'description': DESCRIPTION, 'inputSchema': {
            'type': 'object', 'properties': {'command': {'type': 'string'}},
            'required': ['command'], 'additionalProperties': False}}]}
    elif method == 'tools/call':
        params = request.get('params', {})
        args = params.get('arguments', {})
        if (params.get('name') != TOOL or not isinstance(args, dict) or set(args) != {'command'}
                or not isinstance(args['command'], str) or not args['command'].strip()
                or len(args['command'].encode()) > COMMAND_CAP):
            return {'jsonrpc': '2.0', 'id': identity, 'error': {'code': -32602, 'message': 'Invalid shell call'}}
        try:
            result = {'content': [{'type': 'text', 'text': json.dumps(execute(config, args['command']))}],
                      'isError': False}
        except Exception:
            result = {'content': [{'type': 'text', 'text': 'Controlled command failed; see private capture.'}],
                      'isError': True}
    else:
        return {'jsonrpc': '2.0', 'id': identity, 'error': {'code': -32601, 'message': 'Method unavailable'}}
    return {'jsonrpc': '2.0', 'id': identity, 'result': result}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    config = json.loads(parser.parse_args().config.read_text())
    log(config['root'], 'service_started', pid=os.getpid())
    for line in sys.stdin:
        try:
            reply = handle(config, json.loads(line))
        except Exception:
            reply = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Malformed request'}}
        if reply is not None:
            print(json.dumps(reply, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
