#!/usr/bin/env python3
"""Root-only host helper for a pinned, isolated natural pilot.

Every mutating operation has a permanent intent. Unknown outcomes retain their
claims and never trigger a retry. Authentication enters arm over stdin, remains
in supervisor memory, and reaches the native worker over stdin. Read operations
never release, resume, fork, or stop a task. This helper does not grant scientific
admission; that remains the independent controller's responsibility.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import select
import stat
import subprocess
import sys
import tarfile
import time
import uuid

DOCKER=['/usr/bin/docker','--host','unix:///var/run/docker.sock']
ROOTS=frozenset(('home','config','tmp','work','xdg','capture'))
AUTH_NAMES=frozenset(('.credentials.json','credentials.json','auth.json','tokens.json'))
WORKER_ROOT='/tmp/at-native'
PYTHON='/usr/local/bin/python3.12'


class GateError(ValueError): pass
def fail(code): raise GateError(code)
def sha(data): return hashlib.sha256(data).hexdigest()
def encoded(value): return (json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False)+'\n').encode()
def document_sha(value): return sha(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode())
def now(): return datetime.now(timezone.utc).isoformat()
def digest(value):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{64}',value): fail('invalid_digest')
def execution_id(value):
    try:
        if str(uuid.UUID(value))!=value: raise ValueError
    except (ValueError,TypeError,AttributeError): fail('invalid_execution_id')
def decode(data):
    def pairs(items):
        out={}
        for key,value in items:
            if key in out: fail('duplicate_json_key')
            out[key]=value
        return out
    try:
        value=json.loads(data,object_pairs_hook=pairs,parse_constant=lambda _:fail('invalid_json'))
        encoded(value); return value
    except (ValueError,TypeError,UnicodeError,RecursionError): fail('invalid_json')
def read(path):
    path=Path(path)
    if path.resolve()!=path.absolute() or path.is_symlink(): fail('unsafe_host_path')
    try: descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except OSError: fail('host_file_unavailable')
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode): fail('unsafe_host_file')
        with os.fdopen(descriptor,'rb',closefd=False) as stream: return stream.read()
    finally: os.close(descriptor)
def sync(path):
    descriptor=os.open(path,os.O_RDONLY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)
def new(path,data,mode=0o600):
    try: descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
    except FileExistsError: fail('operation_already_claimed')
    os.fchmod(descriptor,mode)
    with os.fdopen(descriptor,'wb') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    sync(Path(path).parent)
def publish_barrier(path,data):
    """Expose one complete durable payload atomically; never overwrite a release."""
    path=Path(path)
    # The private claim remains after every outcome, including a failed write.
    new(path.with_name('.'+path.name+'.publication-claim'),encoded({'target':path.name,'sha256':sha(data)}))
    temporary=path.with_name('.'+path.name+'.'+str(uuid.uuid4())+'.pending')
    try:
        new(temporary,data)
        try: os.link(temporary,path,follow_symlinks=False)
        except FileExistsError: fail('operation_already_claimed')
        sync(path.parent)
    finally:
        if temporary.exists(): temporary.unlink(); sync(path.parent)

def run_command(argv,payload=b'',*,timeout=60,output=None):
    try:
        result=subprocess.run(argv,input=payload,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            timeout=timeout,env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LANG':'C','LC_ALL':'C'},close_fds=True)
        if result.returncode: fail('host_command_failed')
        if output is not None: output.write(result.stdout); return b''
        return result.stdout
    except (OSError,subprocess.SubprocessError): fail('host_command_failed')
def file_sha(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''): result.update(chunk)
    return result.hexdigest()
def relative(name):
    path=PurePosixPath(name)
    if (type(name) is not str or not name or path.is_absolute() or '..' in path.parts
            or '\\' in name or '\0' in name): fail('unsafe_archive_path')
    return path


class Gate:
    def __init__(self,config_path,pin,*,command=run_command):
        self.config_path=Path(config_path).absolute(); digest(pin); raw=read(self.config_path)
        if sha(raw)!=pin: fail('host_config_pin_mismatch')
        config=decode(raw)
        fields={'schema_version','host_id','daemon_id','remote_root','runtime_bundle_sha256','image_sha256',
                'helper_sha256','runtime_manifest_sha256','network_policy_sha256'}
        if type(config) is not dict or set(config)!=fields or config['schema_version']!='agenttime.ssh-host.v1': fail('host_config_shape')
        self.root=Path(config['remote_root'])
        if self.root.resolve()!=self.root or self.config_path!=self.root/'host-config.json': fail('unsafe_host_root')
        for key in ('runtime_bundle_sha256','helper_sha256','runtime_manifest_sha256','network_policy_sha256'): digest(config[key])
        if not re.fullmatch('sha256:[0-9a-f]{64}',config['image_sha256']): fail('invalid_image_pin')
        if sha(read(Path(__file__).resolve()))!=config['helper_sha256']: fail('host_helper_pin_mismatch')
        self.config=config; self.command=command; self.runtime=self.root/'runtime'/config['runtime_bundle_sha256']

    def _docker(self,*args,payload=b'',timeout=60): return self.command(DOCKER+list(args),payload,timeout=timeout)
    def _daemon(self):
        if decode(self._docker('info','--format','{{json .}}')).get('ID')!=self.config['daemon_id']: fail('daemon_identity_mismatch')
    def _runtime(self):
        raw=read(self.runtime/'runtime-manifest.json')
        if sha(raw)!=self.config['runtime_manifest_sha256']: fail('runtime_manifest_pin_mismatch')
        manifest=decode(raw)
        if type(manifest) is not dict or set(manifest)!={'schema_version','files'} or manifest['schema_version']!='agenttime.runtime-files.v1' or not isinstance(manifest['files'],dict): fail('runtime_manifest_shape')
        actual=set()
        for path in self.runtime.rglob('*'):
            if path.is_symlink(): fail('runtime_symlink_forbidden')
            if path.is_file() and path!=self.runtime/'runtime-manifest.json': actual.add(path.relative_to(self.runtime).as_posix())
            elif not path.is_dir() and not path.is_file(): fail('runtime_special_file')
        if actual!=set(manifest['files']): fail('runtime_files_changed')
        for name,pin in manifest['files'].items():
            relative(name); digest(pin)
            if file_sha(self.runtime/name)!=pin: fail('runtime_file_pin_mismatch')
    def _network_policy(self):
        raw=read(self.root/'network-policy.json')
        if sha(raw)!=self.config['network_policy_sha256']: fail('network_policy_pin_mismatch')
        policy=decode(raw)
        fields={'schema_version','host_id','forward_chain_sha256','host_chain_sha256','peer_isolation_qualified',
                'private_destinations_blocked','metadata_blocked','host_services_blocked','public_internet_allowed'}
        if (type(policy) is not dict or set(policy)!=fields or policy['schema_version']!='agenttime.subject-network.v1'
                or policy['host_id']!=self.config['host_id']): fail('network_policy_shape')
        for key in fields-{'schema_version','host_id','forward_chain_sha256','host_chain_sha256'}:
            if policy[key] is not True: fail('network_policy_unqualified')
        for chain,key in (('AGENTTIME_SUBJECT','forward_chain_sha256'),('AGENTTIME_HOST','host_chain_sha256')):
            if sha(self.command(['/usr/sbin/iptables','-S',chain]))!=policy[key]: fail('network_policy_rules_changed')
        for base,target in (('DOCKER-USER','AGENTTIME_SUBJECT'),('INPUT','AGENTTIME_HOST')):
            for interface in ('br+','docker0'):
                self.command(['/usr/sbin/iptables','-C',base,'-i',interface,'-j',target])
    def _environment(self): self._daemon(); self._runtime(); self._network_policy()
    def _directory(self,attempt_id): execution_id(attempt_id); return self.root/'attempts'/attempt_id
    def _intent(self,attempt_id): return decode(read(self._directory(attempt_id)/'allocation-intent.json'))
    def _labels(self,intent):
        return {'agenttime.attempt':intent['attempt_id'],'agenttime.session':intent['session_id'],
            'agenttime.dispatch':intent['dispatch_id'],'agenttime.campaign':intent['campaign_id'],
            'agenttime.manifest':intent['manifest_sha256'],'agenttime.host':self.config['host_id'],
            'agenttime.runtime':self.config['runtime_bundle_sha256'],'agenttime.network-policy':self.config['network_policy_sha256']}
    def _resources(self,intent):
        task=intent.get('task_id',''); family=task.split('-')[0]; count={'gpqa':12,'hle':20,'browsecomp':12,'assistant':6}.get(family,0)
        if task not in {f'{family}-{i:02}' for i in range(1,count+1)}: fail('task_outside_frozen_pilot')
        closed=family in ('gpqa','hle')
        expected={'allocation_scope':'no_task_compute' if closed else 'natural_subject','cpus':None if closed else 4,'memory_gib':None if closed else 16,'gpu_count':0}
        if intent.get('resources')!=expected: fail('resource_policy_mismatch')
        return (.5 if closed else 4,(2 if closed else 16)*1024**3,(2*1024**3 if family=='assistant' else 64*1024**2))
    def _check_container(self,identity,intent,*,before_start=False):
        self._daemon()
        if (type(identity) is not dict or set(identity)!={'worker_id','session_id','host_id','daemon_id','docker_endpoint','container_id'}
                or identity['worker_id']!=intent['attempt_id'] or identity['session_id']!=intent['session_id']
                or identity['host_id']!=self.config['host_id'] or identity['daemon_id']!=self.config['daemon_id']
                or identity['docker_endpoint']!='unix:///var/run/docker.sock'): fail('container_identity_mismatch')
        digest(identity['container_id']); actual=decode(self._docker('inspect',identity['container_id']))
        if type(actual) is not list or len(actual)!=1 or actual[0].get('Id')!=identity['container_id']: fail('container_identity_mismatch')
        item=actual[0]; conf=item.get('Config',{}); host=item.get('HostConfig',{}); network='at-'+intent['attempt_id']
        cpu,memory,shm=self._resources(intent)
        expected={'Privileged':False,'NanoCpus':int(cpu*1e9),'Memory':memory,'MemorySwap':memory,'PidsLimit':512,
            'NetworkMode':network,'ShmSize':shm,'PublishAllPorts':False,'PidMode':'','IpcMode':'private','UTSMode':''}
        if (item.get('Image')!=self.config['image_sha256'] or conf.get('Labels')!=self._labels(intent)
                or conf.get('User')!='10001:10001' or conf.get('Entrypoint')!=['/bin/sleep'] or conf.get('Cmd')!=['infinity']
                or any(host.get(key)!=value for key,value in expected.items())
                or host.get('CapAdd') not in (None,[]) or host.get('CapDrop')!=['ALL']
                or host.get('SecurityOpt') not in (['no-new-privileges'],['no-new-privileges=true'])
                or any(host.get(key) not in (None,[],{}) for key in ('Devices','DeviceRequests','PortBindings'))): fail('container_isolation_mismatch')
        mounts=item.get('Mounts',[])
        if (len(mounts)!=1 or any(mounts[0].get(k)!=v for k,v in {'Type':'bind','Source':str(self.runtime),'Destination':'/opt/agenttime','RW':False}.items())):
            fail('container_isolation_mismatch')
        env=conf.get('Env',[])
        if not isinstance(env,list) or any(re.match('(ANTHROPIC|CLAUDE_CODE_OAUTH|OPENAI|AWS|GOOGLE)_',str(entry)) for entry in env): fail('container_auth_environment')
        networks=item.get('NetworkSettings',{}).get('Networks',{})
        if set(networks)!={network}: fail('container_network_mismatch')
        net=decode(self._docker('network','inspect',network))
        recorded=decode(read(self._directory(intent['attempt_id'])/'network.json'))
        attachment=networks[network].get('NetworkID'); state=item.get('State',{})
        missing_allowed=(attachment=='' and state.get('Running') is False
                         and (state.get('Status')=='exited' or (before_start and state.get('Status')=='created')))
        if (len(net)!=1 or net[0].get('Id')!=recorded.get('network_id') or recorded.get('name')!=network
                or (attachment!=net[0].get('Id') and not missing_allowed) or net[0].get('Name')!=network
                or net[0].get('Driver')!='bridge' or net[0].get('EnableIPv6') is not False or net[0].get('Internal') is not False
                or net[0].get('Labels')!=self._labels(intent) or net[0].get('Options',{}).get('com.docker.network.bridge.enable_icc')!='false'):
            fail('container_network_mismatch')
        return item
    def _owned(self,identity):
        if not isinstance(identity,dict): fail('container_identity_mismatch')
        directory=self._directory(identity.get('worker_id')); stored=decode(read(directory/'identity.json'))
        if stored!=identity: fail('container_identity_mismatch')
        intent=self._intent(identity['worker_id']); return directory,intent,self._check_container(identity,intent)

    def allocate(self,request):
        intent=request['intent']
        fields={'campaign_id','manifest_sha256','attempt_id','task_id','session_id','dispatch_id','resources','runtime_pins'}
        if type(intent) is not dict or set(intent)!=fields: fail('allocation_intent_shape')
        for key in ('attempt_id','session_id','dispatch_id'): execution_id(intent[key])
        digest(intent['manifest_sha256']); self._resources(intent)
        if intent.get('runtime_pins',{}).get('image_sha256')!=self.config['image_sha256']: fail('image_pin_mismatch')
        directory=self._directory(intent['attempt_id']); directory.parent.mkdir(mode=0o700,exist_ok=True)
        try: directory.mkdir(mode=0o700)
        except FileExistsError: fail('operation_already_claimed')
        sync(directory.parent)
        new(directory/'allocation-intent.json',encoded(intent))
        self._environment()
        labels=self._labels(intent); label_args=[part for key,value in labels.items() for part in ('--label',key+'='+value)]
        network='at-'+intent['attempt_id']
        network_id=self._docker('network','create','--driver','bridge','--opt','com.docker.network.bridge.enable_icc=false',*label_args,network).decode('ascii').strip()
        digest(network_id); new(directory/'network.json',encoded({'name':network,'network_id':network_id}))
        cpu,memory,shm=self._resources(intent)
        raw=self._docker('create','--name',network,'--user','10001:10001','--cap-drop','ALL','--security-opt','no-new-privileges',
            '--pids-limit','512','--cpus',str(cpu),'--memory',str(memory),'--memory-swap',str(memory),'--shm-size',str(shm),'--ipc','private',
            '--network',network,'--mount','type=bind,src='+str(self.runtime)+',dst=/opt/agenttime,readonly',
            '--env','PYTHONPATH=/opt/agenttime/src','--env','PYTHONDONTWRITEBYTECODE=1',*label_args,'--entrypoint','/bin/sleep',self.config['image_sha256'],'infinity')
        container=raw.decode('ascii').strip(); digest(container)
        identity={'worker_id':intent['attempt_id'],'session_id':intent['session_id'],'host_id':self.config['host_id'],
            'daemon_id':self.config['daemon_id'],'docker_endpoint':'unix:///var/run/docker.sock','container_id':container}
        new(directory/'created-identity.json',encoded(identity))
        self._check_container(identity,intent,before_start=True)
        self._docker('start',container)
        if self._check_container(identity,intent).get('State',{}).get('Running') is not True: fail('inert_container_start_unverified')
        new(directory/'identity.json',encoded(identity)); return identity

    def _copy_file_in(self,identity,source,destination):
        self._docker('cp',str(source),identity['container_id']+':'+destination)
    def _file(self,identity,path,*,optional=False):
        try: raw=self._docker('cp',identity['container_id']+':'+path,'-')
        except GateError:
            if optional: return None
            raise
        except OSError:
            if optional: return None
            fail('container_file_unavailable')
        try:
            with tarfile.open(fileobj=io.BytesIO(raw),mode='r:*') as archive:
                entries=archive.getmembers()
                if len(entries)!=1 or not entries[0].isfile() or PurePosixPath(entries[0].name).name!=PurePosixPath(path).name or entries[0].size>64*1024*1024:
                    fail('unsafe_container_metadata')
                return archive.extractfile(entries[0]).read()
        except (tarfile.TarError,ValueError): fail('unsafe_container_metadata')
    def _worker_command(self,identity,operation,*args):
        return DOCKER+['exec','-i','--user','10001:10001',identity['container_id'],PYTHON,'-m','agenttime.natural_worker',operation,'--root',WORKER_ROOT,*args]
    def prepare(self,request):
        spec=request['spec']; identity=spec.get('identity'); directory,intent,item=self._owned(identity)
        self._environment()
        if item.get('State',{}).get('Running') is not True: fail('container_not_running')
        if (spec.get('attempt_id')!=intent['attempt_id'] or spec.get('session_id')!=intent['session_id']
                or spec.get('task_id')!=intent['task_id'] or spec.get('runtime_pins')!=intent['runtime_pins'] or spec.get('resources')!=intent['resources']): fail('prepared_spec_drift')
        try: payload=base64.b64decode(request['input_base64'],validate=True)
        except (ValueError,TypeError): fail('prepared_input_invalid')
        if sha(payload)!=spec.get('input_sha256'): fail('prepared_input_drift')
        new(directory/'prepare-intent.json',encoded({'spec_sha256':sha(encoded(spec)),'input_sha256':sha(payload)}))
        new(directory/'spec.json',encoded(spec),0o644); new(directory/'input.json',payload,0o644)
        self._copy_file_in(identity,directory/'spec.json','/tmp/at-spec.json'); self._copy_file_in(identity,directory/'input.json','/tmp/at-input.json')
        raw=self.command(self._worker_command(identity,'prepare','--spec','/tmp/at-spec.json','--input','/tmp/at-input.json'),timeout=600)
        result=decode(raw)
        if result.get('state')!='prepared': fail('worker_preparation_held')
        new(directory/'prepared.json',encoded(result)); return result

    def arm(self,request,*,detach=None):
        identity=request['identity']; directory,intent,item=self._owned(identity); self._environment()
        barrier=request['barrier_id']; execution_id(barrier); token=request['token']
        if not isinstance(token,str) or not 12<=len(token)<=32768 or any(c.isspace() for c in token): fail('invalid_subscription_authentication')
        ack=request['baseline_ack']; grant=request['authorization']; bound=document_sha(identity)
        prepared=decode(read(directory/'prepared.json')); spec_bytes=read(directory/'spec.json'); spec=decode(spec_bytes)
        if prepared.get('state')!='prepared': fail('worker_not_prepared')
        if (not isinstance(ack,dict) or ack.get('schema_version')!='agenttime.natural-baseline-ack.v1'
                or ack.get('attempt_id')!=identity['worker_id'] or ack.get('session_id')!=identity['session_id'] or ack.get('identity_sha256')!=bound
                or ack.get('archive_verified') is not True or ack.get('independent_archive') is not True
                or ack.get('contract_sha256')!=spec.get('preparation_contract_sha256') or ack.get('input_sha256')!=spec.get('input_sha256')): fail('initial_archive_unverified')
        if (not isinstance(grant,dict) or grant.get('schema_version')!='agenttime.natural-release-authorization.v1'
                or grant.get('attempt_id')!=identity['worker_id'] or grant.get('identity_sha256')!=bound
                or grant.get('baseline_ack_sha256')!=document_sha(ack) or grant.get('campaign_id')!=intent['campaign_id']
                or grant.get('manifest_sha256')!=intent['manifest_sha256'] or grant.get('ledger_authorized') is not True
                or grant.get('spec_sha256')!=sha(spec_bytes) or grant.get('input_sha256')!=spec.get('input_sha256')): fail('release_authorization_unverified')
        execution_id(grant.get('authorization_id'))
        if item.get('State',{}).get('Running') is not True: fail('container_not_running')
        new(directory/'arm-intent.json',encoded({'identity':identity,'barrier_id':barrier,'authorization_sha256':document_sha(grant),'automatic_retry':False}))
        new(directory/'baseline-ack.json',encoded(ack),0o644); new(directory/'release-authorization.json',encoded(grant),0o644)
        self._copy_file_in(identity,directory/'baseline-ack.json','/tmp/at-baseline-ack.json')
        self._copy_file_in(identity,directory/'release-authorization.json','/tmp/at-release-authorization.json')
        return (detach or self._detach)(identity,barrier,token)

    def _detach(self,identity,barrier,token):
        read_fd,write_fd=os.pipe(); first=os.fork()
        if first==0:
            try:
                os.close(read_fd); os.setsid()
                if os.fork()>0: os._exit(0)
                null=os.open('/dev/null',os.O_RDWR)
                for fd in (0,1,2): os.dup2(null,fd)
                if null>2: os.close(null)
                os.closerange(3,write_fd); os.closerange(write_fd+1,65536)
                receipt={'armed':True,'identity':identity,'barrier_id':barrier,'supervisor_pid':os.getpid(),'armed_at':now()}
                new(self._directory(identity['worker_id'])/'armed.json',encoded(receipt))
                os.write(write_fd,encoded(receipt)); os.close(write_fd)
                self.supervise(identity,barrier,token)
            except BaseException:
                try: new(self._directory(identity['worker_id'])/'supervisor-held.json',encoded({'state':'unknown','reason':'supervisor_unresolved','observed_at':now()}))
                except BaseException: pass
            finally: os._exit(0)
        os.close(write_fd)
        try:
            if not select.select([read_fd],[],[],30)[0]: fail('arm_outcome_unknown')
            raw=os.read(read_fd,65536)
            if not raw: fail('arm_outcome_unknown')
            return decode(raw)
        finally:
            os.close(read_fd); os.waitpid(first,0)

    def open(self,request):
        barrier=request['barrier_id']; execution_id(barrier); identities=request['identities']
        if not isinstance(identities,list) or not identities or len({i.get('worker_id') for i in identities})!=len(identities): fail('barrier_members_invalid')
        for identity in identities:
            directory,_,_=self._owned(identity); armed=decode(read(directory/'armed.json'))
            if armed.get('armed') is not True or armed.get('identity')!=identity or armed.get('barrier_id')!=barrier: fail('supervisor_not_armed')
        directory=self.root/'barriers'; directory.mkdir(mode=0o700,exist_ok=True)
        value={'barrier_id':barrier,'identities':identities,'opened_at':now()}; publish_barrier(directory/barrier,encoded(value))
        return {'opened':True,'barrier_id':barrier}

    def supervise(self,identity,barrier,token):
        """Detached owner waits indefinitely for explicit release, then executes once."""
        directory=self._directory(identity['worker_id']); gate=self.root/'barriers'/barrier
        while not gate.exists(): time.sleep(.05)
        value=decode(read(gate))
        if value.get('barrier_id')!=barrier or identity not in value.get('identities',[]): fail('barrier_identity_mismatch')
        self._owned(identity); self._network_policy()
        new(directory/'launch-intent.json',encoded({'identity':identity,'barrier_id':barrier,'launch_requested_at':now(),'automatic_retry':False}))
        argv=self._worker_command(identity,'run','--baseline-ack','/tmp/at-baseline-ack.json',
            '--release-authorization','/tmp/at-release-authorization.json','--binary','/opt/agenttime/bin/claude')
        try:
            output=self.command(argv,(token+'\n').encode(),timeout=None)
        except GateError:
            # A nonzero worker exit can still have a sealed failed report. Never rerun.
            output=b''
        if token.encode() in output: fail('credential_in_worker_output')
        raw=self._file(identity,WORKER_ROOT+'/report.json',optional=True)
        if raw is None: fail('worker_stop_unresolved')
        if token.encode() in raw: fail('credential_in_worker_output')
        report=decode(raw)
        new(directory/'worker-returned.json',encoded({'identity':identity,'report_sha256':sha(raw),'observed_at':now()}))
        self.stop_after_drain(identity,report)

    def stop_after_drain(self,identity,report):
        directory,_,_=self._owned(identity)
        execution=report.get('execution',{}) if isinstance(report,dict) else {}
        start=execution.get('root_exit_monotonic_ns'); end=execution.get('owned_work_drained_monotonic_ns')
        if (report.get('attempt_id')!=identity['worker_id'] or report.get('session_id')!=identity['session_id']
                or report.get('identity_sha256')!=document_sha(identity) or execution.get('session_id')!=identity['session_id']
                or type(start) is not int or type(end) is not int or not 0<=start<=end
                or type(execution.get('root_exit_code')) is not int): return False
        raw_report=self._file(identity,WORKER_ROOT+'/report.json',optional=True)
        raw_events=self._file(identity,WORKER_ROOT+'/capture/events.jsonl',optional=True)
        if raw_report is None or raw_events is None or decode(raw_report)!=report: return False
        try:
            events=[decode(line) for line in raw_events.splitlines()]
            prior=-1; found={}; clock=execution.get('clock_id')
            if not isinstance(clock,str) or not clock.startswith('linux-boot:'): return False
            for sequence,event in enumerate(events,1):
                timestamp=event.get('monotonic_ns')
                if (event.get('sequence')!=sequence or event.get('session_id')!=identity['session_id'] or event.get('clock_id')!=clock
                        or type(timestamp) is not int or timestamp<prior): return False
                prior=timestamp
                if event.get('kind') in ('root_process_exited','owned_work_drained'):
                    if event['kind'] in found: return False
                    found[event['kind']]=event
            if (found.get('root_process_exited',{}).get('monotonic_ns')!=start or found.get('root_process_exited',{}).get('exit_code')!=execution['root_exit_code']
                    or found.get('owned_work_drained',{}).get('monotonic_ns')!=end): return False
        except (ValueError,TypeError,AttributeError): return False
        new(directory/'stop-intent.json',encoded({'identity':identity,'report_sha256':document_sha(report),'owned_work_drained':True,'observed_at':now()}))
        self._docker('stop','--time','10',identity['container_id'])
        item=self._owned(identity)[2]
        if item.get('State',{}).get('Running') is not False or item.get('State',{}).get('Status')!='exited': fail('container_stop_unverified')
        proof={'identity':identity,'stop_verified':True,'container_running':False,'external_verification':True,'observed_at':now()}
        new(directory/'stop-proof.json',encoded(proof)); return True

    def stop_proof(self,request):
        identity=request['identity']; directory,_,item=self._owned(identity); stopped=item.get('State',{}).get('Running') is False and item.get('State',{}).get('Status')=='exited'
        receipt=decode(read(directory/'stop-proof.json')) if (directory/'stop-proof.json').exists() else None
        valid=bool(stopped and receipt and receipt.get('identity')==identity and receipt.get('stop_verified') is True
                   and receipt.get('container_running') is False and receipt.get('external_verification') is True)
        if valid:
            if set(receipt)!={'identity','stop_verified','container_running','external_verification','observed_at'}: fail('stop_receipt_changed')
            try:
                if datetime.fromisoformat(receipt['observed_at']).utcoffset() is None: fail('invalid_stop_clock')
            except (ValueError,TypeError): fail('invalid_stop_clock')
            return receipt  # Stable original proof after a fresh independent Docker check.
        return {'identity':identity,'stop_verified':valid,'container_running':item.get('State',{}).get('Running'),
                'external_verification':True,'observed_at':now()}
    def observe(self,request):
        identity=request.get('identity'); directory=self._directory(request['attempt_id'])
        if identity is None:
            if not (directory/'identity.json').exists(): return None
            identity=decode(read(directory/'identity.json'))
        self._owned(identity)
        if identity['worker_id']!=request['attempt_id']: fail('observation_identity_mismatch')
        raw=self._file(identity,WORKER_ROOT+'/observation.json',optional=True)
        if raw is None: return None
        worker=decode(raw)
        self._metadata(worker,'worker')
        if worker.get('attempt_id')!=identity['worker_id'] or worker.get('session_id')!=identity['session_id']: fail('observation_identity_mismatch')
        raw_events=self._file(identity,WORKER_ROOT+'/capture/events.jsonl',optional=True) or b''
        lines=raw_events.splitlines(keepends=True); events=[decode(line) for line in lines if line.endswith(b'\n')]
        for event in events: self._metadata(event,'event')
        raw_report=self._file(identity,WORKER_ROOT+'/report.json',optional=True); report=decode(raw_report) if raw_report else None
        if report is not None: self._metadata(report,'report')
        if report and (report.get('attempt_id')!=identity['worker_id'] or report.get('session_id')!=identity['session_id']): fail('report_identity_mismatch')
        return {'identity':identity,'clock_id':worker.get('clock_id'),'worker':worker,'events':events,'report':report}

    @staticmethod
    def _metadata(value,kind):
        clocks={'prompt_released_monotonic_ns','result_monotonic_ns','submission_monotonic_ns','root_exit_monotonic_ns',
                'owned_work_drained_monotonic_ns','native_terminal_monotonic_ns','runtime_seconds','timing_valid','clock_id'}
        allowed={
            'worker':{'schema_version','attempt_id','task_id','session_id','state','source_observed_at','worker_monotonic_ns',
                'last_native_event_monotonic_ns','timing_admissible','holds_capacity_until_controller_verifies_stop','issues'}|clocks,
            'event':{'sequence','kind','monotonic_ns','audit_utc','session_id','clock_id','pid','owner_pid','bytes','input_sha256','attempt_id',
                'sha256','native_submission_monotonic_ns','sealed_answer_sha256','exit_code','metadata','reason'},
            'report':{'schema_version','attempt_id','session_id','identity_sha256','spec_sha256','input_sha256','authorization_sha256','automatic_retry',
                'state','issues','requested_route','configured_authentication','archive_verified','independent_initial_archive_acknowledged',
                'independent_final_archive_acknowledged','included_subscription_allowance_verified','timing_admissible','study_admitted',
                'experiment_time_cap_seconds','requires_controller_stop_verification','capture_finished_monotonic_ns',
                'runtime_image_and_resources_verified_by_worker','runtime_verification','execution','archive','transport','native_sessions_verified','native_session_evidence'},
            'execution':{'session_id','root_exit_code','issues','sealed_answer_sha256','usage_evidence','cancelled','observed_native_init','study_admitted'}|clocks,
            'event_detail':{'type','subtype','session_id','model','tool_names'}}
        if not isinstance(value,dict) or set(value)-allowed[kind]: fail('unsafe_observation_metadata')
        if kind=='event' and 'metadata' in value: Gate._metadata(value['metadata'],'event_detail')
        if kind=='report' and 'execution' in value: Gate._metadata(value['execution'],'execution')

    def archive(self,request,output):
        identity=request['identity']; self._owned(identity); phase=request['phase']
        if phase not in ('baseline','final'): fail('invalid_archive_phase')
        base=WORKER_ROOT+('/archive' if phase=='final' else '')
        receipt=self._file(identity,WORKER_ROOT+('/archive-receipt.json' if phase=='final' else '/baseline.json'))
        # Docker streams a tar snapshot; forward only six explicit state roots.
        process=subprocess.Popen(DOCKER+['cp',identity['container_id']+':'+base+'/.','-'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            env={'PATH':'/usr/bin:/bin','LANG':'C','LC_ALL':'C'},close_fds=True)
        try:
            with tarfile.open(fileobj=process.stdout,mode='r|') as source, tarfile.open(fileobj=output,mode='w|') as destination:
                item=tarfile.TarInfo('__receipt__.json'); item.mode=0o600; item.size=len(receipt); destination.addfile(item,io.BytesIO(receipt))
                seen=set()
                for member in source:
                    path=relative(member.name); parts=list(path.parts)
                    if parts and parts[0] in ('at-native','archive'): parts=parts[1:]
                    if not parts or parts[0] not in ROOTS: continue
                    name='/'.join(parts)
                    if name in seen or any(part.lower() in AUTH_NAMES for part in parts): fail('unsafe_archive_member')
                    seen.add(name); member.name=name
                    if not (member.isfile() or member.isdir() or member.issym()): fail('unsupported_archive_object')
                    destination.addfile(member,source.extractfile(member) if member.isfile() else None)
            if process.wait()!=0: fail('archive_transfer_failed')
        finally:
            if process.poll() is None: process.kill(); process.wait()
            process.stdout.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--config',required=True); parser.add_argument('--pin',required=True)
    parser.add_argument('operation',choices=('allocate','prepare','arm','open','observe','stop-proof','archive'))
    args=parser.parse_args(argv); os.umask(0o077)
    try:
        if os.geteuid()!=0: fail('host_helper_requires_root')
        gate=Gate(args.config,args.pin); request=decode(sys.stdin.buffer.read(64*1024*1024+1))
        if args.operation=='archive': gate.archive(request,sys.stdout.buffer); return 0
        result=getattr(gate,args.operation.replace('-','_'))(request)
        sys.stdout.buffer.write(encoded(result)); sys.stdout.buffer.flush(); return 0
    except Exception as exc:
        code=str(exc) if isinstance(exc,GateError) and re.fullmatch('[a-z0-9_]{1,96}',str(exc)) else 'host_operation_unresolved'
        if args.operation!='archive': sys.stdout.buffer.write(encoded({'error_code':code})); sys.stdout.buffer.flush()
        return 1

if __name__=='__main__': raise SystemExit(main())
