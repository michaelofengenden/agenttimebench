"""Pinned SSH adapter for the first natural pilot, without implicit replay.

The privileged host helper creates inert containers and owns detached release
supervisors. The controller supplies durable admission. Network/provider access
occurs only when callers invoke this adapter; construction reads no credentials.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid

from .jsonio import loads_strict
from .natural_controller import ArchiveCopy
from .natural_inputs import SELECTED_SLOTS
from .natural_ledger import validate_identity

ROOTS = frozenset(('home', 'config', 'tmp', 'work', 'xdg', 'capture'))
AUTH_NAMES = frozenset(('.credentials.json', 'credentials.json', 'auth.json', 'tokens.json'))


class TransportError(ValueError):
    """Static metadata code, never remote output or supplied authentication."""


def _fail(code): raise TransportError(code)
def _sha(data): return hashlib.sha256(data).hexdigest()
def _digest(value):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{64}', value): _fail('invalid_pin')
def _uuid(value):
    try:
        if str(uuid.UUID(value)) != value: raise ValueError
    except (ValueError, TypeError, AttributeError): _fail('invalid_execution_id')
def _encode(value): return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


@dataclass(frozen=True)
class HostPlan:
    host_id: str
    address: str
    daemon_id: str
    key_path: Path
    known_hosts_path: Path
    remote_root: str
    runtime_bundle_sha256: str
    image_sha256: str
    host_config_sha256: str
    user: str = 'root'

    def __post_init__(self):
        for value in (self.host_id, self.daemon_id):
            if type(value) is not str or not re.fullmatch('[A-Za-z0-9_.:-]{1,160}', value): _fail('invalid_host_identity')
        if type(self.address) is not str or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9.:-]{0,252}', self.address): _fail('invalid_host_address')
        root = PurePosixPath(self.remote_root)
        if (not root.is_absolute() or str(root) != self.remote_root or '..' in root.parts
                or not re.fullmatch('/[A-Za-z0-9_./-]+', self.remote_root)): _fail('unsafe_remote_root')
        for key in ('runtime_bundle_sha256', 'host_config_sha256'): _digest(getattr(self, key))
        if not re.fullmatch('sha256:[0-9a-f]{64}', self.image_sha256): _fail('invalid_image_pin')
        if self.user != 'root': _fail('host_helper_requires_root')
        for key in ('key_path', 'known_hosts_path'):
            path = Path(getattr(self, key)).expanduser().absolute()
            if '\0' in str(path): _fail('unsafe_ssh_path')
            object.__setattr__(self, key, path)


def run_command(argv, payload, *, timeout, output=None):
    """No shell, inherited auth, stderr capture, retries, or remote task deadline."""
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, env=env, close_fds=True)
    try:
        # Payloads can contain images. communicate drains stdout while feeding stdin.
        if output is None:
            data, _ = process.communicate(payload, timeout=timeout)
            if process.returncode or len(data) > 64 * 1024 * 1024: _fail('ssh_result_unavailable')
            return data
        # Archive requests are small and their output is streamed to private storage.
        process.stdin.write(payload); process.stdin.close()
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0: _fail('ssh_result_unavailable')
                if not selector.select(remaining): _fail('ssh_result_unavailable')
                chunk = os.read(process.stdout.fileno(), 1024 * 1024)
                if not chunk: break
                output.write(chunk)
        process.wait(timeout=max(.01, deadline-time.monotonic()))
        if process.returncode: _fail('ssh_result_unavailable')
        return b''
    except (OSError, subprocess.SubprocessError):
        _fail('ssh_result_unavailable')
    finally:
        if process.poll() is None:
            process.kill(); process.wait()
        if process.stdout: process.stdout.close()
        if process.stdin and not process.stdin.closed: process.stdin.close()


class _SecretScanningSink:
    def __init__(self, stream, secret): self.stream=stream; self.secret=secret; self.tail=b''
    def write(self, chunk):
        data=self.tail+chunk
        if self.secret in data: _fail('credential_in_archive')
        keep=len(self.secret)-1
        if len(data)>keep:
            self.stream.write(data[:-keep]); self.tail=data[-keep:]
        else: self.tail=data
    def finish(self): self.stream.write(self.tail); self.tail=b''; self.stream.flush(); self.stream.seek(0)


def _extract_archive(stream, destination):
    seen=set(); receipt=None; directory_modes={}; links=[]
    with tarfile.open(fileobj=stream, mode='r|') as archive:
        for member in archive:
            parts=PurePosixPath(member.name).parts
            if (not parts or member.name.startswith('/') or '..' in parts or '\\' in member.name
                    or '\0' in member.name or '/'.join(parts) in seen): _fail('unsafe_archive_member')
            seen.add('/'.join(parts))
            if member.name=='__receipt__.json':
                if receipt is not None or not member.isfile() or member.size>16*1024*1024: _fail('invalid_archive_receipt')
                receipt=archive.extractfile(member).read(); continue
            if parts[0] not in ROOTS or any(part.lower() in AUTH_NAMES for part in parts): _fail('unsafe_archive_member')
            target=destination.joinpath(*parts)
            if any(parent.is_symlink() for parent in (target,*target.parents) if parent != destination.parent): _fail('unsafe_archive_link')
            if member.isdir():
                if target.exists() and not target.is_dir(): _fail('duplicate_archive_member')
                target.mkdir(mode=0o700,parents=True,exist_ok=True); directory_modes[target]=member.mode & 0o777
            elif member.isfile():
                target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                with target.open('xb') as output: shutil.copyfileobj(archive.extractfile(member),output); output.flush(); os.fsync(output.fileno())
                target.chmod(member.mode & 0o777)
            elif member.issym():
                link=PurePosixPath(member.linkname)
                if link.is_absolute() or '\0' in member.linkname or '\\' in member.linkname: _fail('unsafe_archive_link')
                target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                resolved=(target.parent/member.linkname).resolve()
                if not resolved.is_relative_to(destination): _fail('unsafe_archive_link')
                target.symlink_to(member.linkname); links.append(target)
            else: _fail('unsupported_archive_object')
    if receipt is None or set(p.name for p in destination.iterdir()) != ROOTS: _fail('incomplete_archive')
    if any((destination/name).is_symlink() or not (destination/name).is_dir() for name in ROOTS): _fail('unsafe_archive_root')
    for link in links:
        if not link.resolve().is_relative_to(destination) or not link.exists(): _fail('unsafe_archive_link')
    for path, mode in sorted(directory_modes.items(),key=lambda pair:len(pair[0].parts),reverse=True): path.chmod(mode)
    return receipt


class SSHTransport:
    def __init__(self, hosts, *, token_supplier, runner=run_command):
        self.hosts=tuple(hosts)
        if not self.hosts or len({h.host_id for h in self.hosts})!=len(self.hosts): _fail('invalid_host_plan')
        self.by_id={h.host_id:h for h in self.hosts}; self.token_supplier=token_supplier; self.runner=runner
        self.limits={h.host_id:threading.BoundedSemaphore(6) for h in self.hosts}

    def _token(self):
        try: value=self.token_supplier()
        except Exception: _fail('subscription_credential_unavailable')
        if not isinstance(value,str) or not 12<=len(value)<=32768 or any(c.isspace() for c in value): _fail('subscription_credential_unavailable')
        return value

    def _assigned(self, intent):
        if intent.get('task_id') not in SELECTED_SLOTS: _fail('task_outside_frozen_pilot')
        return self.hosts[SELECTED_SLOTS.index(intent['task_id'])%len(self.hosts)]

    def _identity_host(self, identity):
        try: validate_identity(identity)
        except Exception: _fail('invalid_worker_identity')
        host=self.by_id.get(identity['host_id'])
        if host is None or identity['daemon_id']!=host.daemon_id or identity['docker_endpoint']!='unix:///var/run/docker.sock':
            _fail('worker_identity_host_mismatch')
        _uuid(identity['worker_id'])
        return host

    def _rpc(self, host, operation, request, *, secret=None, output=None):
        helper=host.remote_root+'/runtime/'+host.runtime_bundle_sha256+'/qualification/native_session/remote_gate.py'
        remote=shlex.join(['/usr/bin/python3',helper,'--config',host.remote_root+'/host-config.json','--pin',host.host_config_sha256,operation])
        argv=['/usr/bin/ssh','-i',str(host.key_path),'-o','UserKnownHostsFile='+str(host.known_hosts_path),
              '-o','StrictHostKeyChecking=yes','-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','ConnectTimeout=10',
              '-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3','--',host.user+'@'+host.address,remote]
        try:
            with self.limits[host.host_id]:
                raw=self.runner(argv,_encode(request),timeout=600 if operation in ('allocate','prepare','archive') else 60,output=output)
            if output is not None: return
            if secret and secret.encode() in raw: _fail('credential_in_remote_result')
            value=loads_strict(raw)
            if isinstance(value,dict) and value.get('error_code'): _fail('remote_operation_held')
            return value
        except TransportError: raise
        except Exception: _fail('ssh_outcome_unknown')

    def allocate(self, intent):
        host=self._assigned(intent)
        if intent.get('runtime_pins',{}).get('image_sha256')!=host.image_sha256: _fail('image_pin_mismatch')
        for key in ('attempt_id','session_id','dispatch_id'): _uuid(intent.get(key))
        request={k:v for k,v in intent.items() if k!='controller_intent_path'}
        identity=self._rpc(host,'allocate',{'intent':request})
        actual=self._identity_host(identity)
        if actual!=host or identity['worker_id']!=intent['attempt_id'] or identity['session_id']!=intent['session_id']:
            _fail('allocation_identity_mismatch')
        return identity

    def prepare(self, spec, input_bytes):
        host=self._identity_host(spec['identity'])
        if not isinstance(input_bytes,bytes) or _sha(input_bytes)!=spec.get('input_sha256'): _fail('prepared_input_mismatch')
        return self._rpc(host,'prepare',{'spec':spec,'input_base64':base64.b64encode(input_bytes).decode()})

    def release_barrier(self, barrier_id, members):
        _uuid(barrier_id)
        if not members or len({m['identity']['worker_id'] for m in members})!=len(members): _fail('invalid_barrier_members')
        groups={}
        for member in members:
            host=self._identity_host(member['identity']); groups.setdefault(host.host_id,[]).append(member)
        token=self._token()
        def arm(member):
            host=self._identity_host(member['identity'])
            value=self._rpc(host,'arm',{'barrier_id':barrier_id,**member,'token':token},secret=token)
            if not isinstance(value,dict) or value.get('armed') is not True or value.get('barrier_id')!=barrier_id or value.get('identity')!=member['identity']:
                _fail('arm_outcome_unknown')
        try:
            with ThreadPoolExecutor(max_workers=min(50,len(members))) as pool:
                futures=[pool.submit(arm,member) for member in members]
                failures=[future.exception() for future in futures]
            if any(failures): _fail('arm_outcome_unknown')
        finally:
            del token
        # A failure before this line cannot open any gate. No RPC is retried.
        def open_gate(host_id):
            value=self._rpc(self.by_id[host_id],'open',{'barrier_id':barrier_id,'identities':[m['identity'] for m in groups[host_id]]})
            if not isinstance(value,dict) or value.get('opened') is not True or value.get('barrier_id')!=barrier_id: _fail('barrier_outcome_unknown')
        with ThreadPoolExecutor(max_workers=len(groups)) as pool:
            futures=[pool.submit(open_gate,host_id) for host_id in groups]
            failures=[future.exception() for future in futures]
        if any(failures): _fail('barrier_outcome_unknown')

    def observe(self, intent, identity):
        host=self._identity_host(identity) if identity else self._assigned(intent)
        return self._rpc(host,'observe',{'attempt_id':intent['attempt_id'],'identity':identity})

    def stop_proof(self, identity):
        return self._rpc(self._identity_host(identity),'stop-proof',{'identity':identity})

    def copy_archive(self, identity, phase, destination):
        host=self._identity_host(identity); destination=Path(destination).absolute()
        if phase not in ('baseline','final'): _fail('invalid_archive_phase')
        if destination.exists() or destination.is_symlink() or destination.parent.resolve()!=destination.parent: _fail('archive_destination_not_fresh')
        token=self._token()  # Also authorized for independent scans after controller restart.
        with tempfile.TemporaryFile(dir=destination.parent) as raw:
            sink=_SecretScanningSink(raw,token.encode())
            self._rpc(host,'archive',{'identity':identity,'phase':phase},secret=token,output=sink)
            sink.finish()
            with tempfile.TemporaryDirectory(prefix='.archive-copy-',dir=destination.parent) as temporary:
                staging=Path(temporary)
                try: receipt=_extract_archive(raw,staging)
                except (OSError,ValueError,RuntimeError,tarfile.TarError): _fail('unsafe_archive_copy')
                destination.mkdir(mode=0o700)  # Exclusive publication; an interrupted copy remains held.
                for name in ROOTS: os.rename(staging/name,destination/name)
                descriptor=os.open(destination,os.O_RDONLY)
                try: os.fsync(descriptor)
                finally: os.close(descriptor)
        return ArchiveCopy(receipt_bytes=receipt,credential_scan_verified=True)
