import base64
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
import os
import time
import threading
from unittest.mock import patch
from pathlib import Path
import tarfile
import tempfile
import unittest
from uuid import uuid4

SOURCE=Path(__file__).resolve().parents[1]/'qualification/native_session/remote_gate.py'
remote=None
if SOURCE.exists():
    spec=importlib.util.spec_from_file_location('agenttime_remote_gate_test',SOURCE)
    remote=importlib.util.module_from_spec(spec); spec.loader.exec_module(remote)

def encoded(value): return (json.dumps(value,sort_keys=True)+'\n').encode()
def sha(data): return hashlib.sha256(data).hexdigest()
def single_file_tar(name,data):
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as archive:
        item=tarfile.TarInfo(name); item.size=len(data); archive.addfile(item,io.BytesIO(data))
    return stream.getvalue()

class DockerFake:
    def __init__(self):
        self.calls=[]; self.network=None; self.container=None; self.files={}; self.mutate=None; self.empty_created=False; self.empty_running=False; self.chain=b'-N AGENTTIME_SUBJECT\n'; self.host_chain=b'-N AGENTTIME_HOST\n'
    def __call__(self,argv,payload=b'',*,timeout=60,output=None):
        self.calls.append((list(argv),payload))
        if argv[0]=='/usr/sbin/iptables':
            if argv[1]=='-S': return self.chain if argv[2]=='AGENTTIME_SUBJECT' else self.host_chain
            return b''
        self.assert_docker(argv)
        command=argv[3:]
        if command[0]=='info': return encoded({'ID':'daemon-h1'})
        if command[:2]==['network','create']:
            labels={command[i+1].split('=',1)[0]:command[i+1].split('=',1)[1] for i,v in enumerate(command) if v=='--label'}
            self.network={'Id':'d'*64,'Name':command[-1],'Driver':'bridge','EnableIPv6':False,'Internal':False,
                          'Options':{'com.docker.network.bridge.enable_icc':'false'},'Labels':labels}
            return ('d'*64+'\n').encode()
        if command[:2]==['network','inspect']: return encoded([self.network])
        if command[0]=='create':
            def val(key,default=None): return command[command.index(key)+1] if key in command else default
            labels={command[i+1].split('=',1)[0]:command[i+1].split('=',1)[1] for i,v in enumerate(command) if v=='--label'}
            runtime=val('--mount').split('src=')[1].split(',')[0]
            memory=int(val('--memory')); cpus=float(val('--cpus'))
            self.container={'Id':'c'*64,'Image':'sha256:'+'b'*64,'Config':{'User':'10001:10001','Labels':labels,'Entrypoint':['/bin/sleep'],'Cmd':['infinity'],'Env':['PYTHONPATH=/opt/agenttime/src','PYTHONDONTWRITEBYTECODE=1']},
                'HostConfig':{'Privileged':False,'CapAdd':None,'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges'],'PidsLimit':512,'NanoCpus':int(cpus*1e9),'Memory':memory,'MemorySwap':memory,
                    'NetworkMode':val('--network'),'PidMode':'','IpcMode':'private','UTSMode':'','Devices':[],'DeviceRequests':None,'PortBindings':{},'PublishAllPorts':False,'ShmSize':int(val('--shm-size','67108864'))},
                'Mounts':[{'Type':'bind','Source':runtime,'Destination':'/opt/agenttime','RW':False}],
                'NetworkSettings':{'Networks':{val('--network'):{'NetworkID':'d'*64}}},'State':{'Running':False,'Status':'created'}}
            if self.empty_created: self.container['NetworkSettings']['Networks'][val('--network')]['NetworkID']=''
            if self.mutate: self.mutate(self.container)
            return ('c'*64+'\n').encode()
        if command[0]=='start':
            self.container['State']={'Running':True,'Status':'running'}
            for net in self.container['NetworkSettings']['Networks'].values(): net['NetworkID']='' if self.empty_running else 'd'*64
            return b'c\n'
        if command[0]=='inspect': return encoded([self.container])
        if command[0]=='stop': self.container['State']={'Running':False,'Status':'exited'}; return b'c\n'
        if command[0]=='cp':
            if command[-1]=='-':
                path=command[-2].split(':',1)[1]
                if path not in self.files: raise OSError('missing file')
                return single_file_tar(Path(path).name,self.files[path])
            return b''
        if command[0]=='exec':
            if 'prepare' in command: return encoded({'state':'prepared'})
            return b''
        raise AssertionError(command)
    @staticmethod
    def assert_docker(argv):
        assert argv[:3]==['/usr/bin/docker','--host','unix:///var/run/docker.sock'],argv

class RemoteGateTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(remote,'remote helper implementation missing')
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root=Path(self.tmp.name).resolve()
        self.docker=DockerFake(); self.runtime=self.root/'runtime'/('a'*64); self.runtime.mkdir(parents=True)
        helper=self.runtime/'qualification/native_session/remote_gate.py'; helper.parent.mkdir(parents=True); helper.write_bytes(SOURCE.read_bytes())
        (self.runtime/'src').mkdir(); (self.runtime/'src/worker.py').write_text('pinned source\n')
        manifest={'schema_version':'agenttime.runtime-files.v1','files':{'qualification/native_session/remote_gate.py':sha(helper.read_bytes()),'src/worker.py':sha(b'pinned source\n')}}
        (self.runtime/'runtime-manifest.json').write_bytes(encoded(manifest))
        self.policy={'schema_version':'agenttime.subject-network.v1','host_id':'h1','forward_chain_sha256':sha(self.docker.chain),'host_chain_sha256':sha(self.docker.host_chain),
            'peer_isolation_qualified':True,'private_destinations_blocked':True,'metadata_blocked':True,'host_services_blocked':True,'public_internet_allowed':True}
        (self.root/'network-policy.json').write_bytes(encoded(self.policy))
        self.config={'schema_version':'agenttime.ssh-host.v1','host_id':'h1','daemon_id':'daemon-h1','remote_root':str(self.root),'runtime_bundle_sha256':'a'*64,
            'image_sha256':'sha256:'+'b'*64,'helper_sha256':sha(SOURCE.read_bytes()),'runtime_manifest_sha256':sha(encoded(manifest)),'network_policy_sha256':sha(encoded(self.policy))}
        self.config_path=self.root/'host-config.json'; self.config_path.write_bytes(encoded(self.config)); self.config_path.chmod(0o600)
        self.gate=remote.Gate(self.config_path,sha(self.config_path.read_bytes()),command=self.docker)
        self.intent={'campaign_id':str(uuid4()),'manifest_sha256':'e'*64,'attempt_id':str(uuid4()),'task_id':'gpqa-01','session_id':str(uuid4()),'dispatch_id':str(uuid4()),
            'resources':{'allocation_scope':'no_task_compute','cpus':None,'memory_gib':None,'gpu_count':0},'runtime_pins':{'image_sha256':self.config['image_sha256']}}
    def allocate(self): return self.gate.allocate({'intent':self.intent})
    def prepared(self,identity):
        data=b'{"synthetic":"input"}\n'
        spec={'identity':identity,'attempt_id':identity['worker_id'],'session_id':identity['session_id'],'task_id':self.intent['task_id'],
            'runtime_pins':self.intent['runtime_pins'],'resources':self.intent['resources'],'input_sha256':sha(data),'preparation_contract_sha256':'f'*64}
        self.gate.prepare({'spec':spec,'input_base64':base64.b64encode(data).decode()})
        return spec
    def arm_request(self,identity):
        spec=self.prepared(identity)
        ack={'schema_version':'agenttime.natural-baseline-ack.v1','attempt_id':identity['worker_id'],'identity_sha256':remote.document_sha(identity),
            'session_id':identity['session_id'],'contract_sha256':spec['preparation_contract_sha256'],'input_sha256':spec['input_sha256'],
            'archive_verified':True,'independent_archive':True}
        auth={'schema_version':'agenttime.natural-release-authorization.v1','attempt_id':identity['worker_id'],'identity_sha256':remote.document_sha(identity),
            'spec_sha256':sha(encoded(spec)),'input_sha256':spec['input_sha256'],'baseline_ack_sha256':remote.document_sha(ack),'authorization_id':str(uuid4()),
            'campaign_id':self.intent['campaign_id'],'manifest_sha256':self.intent['manifest_sha256'],'ledger_authorized':True}
        return {'identity':identity,'barrier_id':str(uuid4()),'baseline_ack':ack,'authorization':auth,'token':'offline-only-secret-abcdefghijklmnop'}
    def test_allocation_has_inert_labeled_container_dedicated_network_and_permanent_claim(self):
        identity=self.allocate(); self.assertEqual(identity['worker_id'],self.intent['attempt_id']); self.assertTrue(self.docker.container['State']['Running'])
        self.assertEqual(self.docker.container['HostConfig']['NanoCpus'],500_000_000); self.assertEqual(self.docker.container['HostConfig']['Memory'],2*1024**3)
        self.assertEqual(self.docker.network['Options']['com.docker.network.bridge.enable_icc'],'false')
        calls=len(self.docker.calls)
        with self.assertRaisesRegex(remote.GateError,'already_claimed'): self.allocate()
        self.assertFalse(any('create' in argv for argv,_ in self.docker.calls[calls:]))
        self.assertFalse(any('exec' in argv for argv,_ in self.docker.calls))
    def test_actual_resource_or_mount_mismatch_holds_and_does_not_start(self):
        self.docker.mutate=lambda x:x['HostConfig'].__setitem__('Memory',1)
        with self.assertRaisesRegex(remote.GateError,'container_isolation'): self.allocate()
        self.assertFalse(self.docker.container['State']['Running'])
        self.assertFalse(any('stop' in a or 'rm' in a for a,_ in self.docker.calls))
    def test_firewall_drift_prevents_creation(self):
        self.docker.chain+=b'-A AGENTTIME_SUBJECT -j ACCEPT\n'
        with self.assertRaisesRegex(remote.GateError,'network_policy'): self.allocate()
        self.assertIsNone(self.docker.container)
    def test_runtime_tamper_prevents_creation(self):
        (self.runtime/'src/worker.py').write_text('tampered')
        with self.assertRaisesRegex(remote.GateError,'runtime'): self.allocate()
        self.assertIsNone(self.docker.container)
    def test_unexpected_runtime_file_prevents_creation(self):
        (self.runtime/'answers.json').write_text('private')
        with self.assertRaisesRegex(remote.GateError,'runtime'): self.allocate()
    def test_daemon_or_container_ownership_mismatch_refuses_read(self):
        identity=self.allocate(); changed={**identity,'container_id':'f'*64}
        with self.assertRaisesRegex(remote.GateError,'identity'): self.gate.stop_proof({'identity':changed})
        self.assertFalse(any('stop' in a for a,_ in self.docker.calls))
    def test_stop_proof_is_readonly_and_missing_stop_receipt_is_not_proof(self):
        identity=self.allocate(); proof=self.gate.stop_proof({'identity':identity})
        self.assertFalse(proof['stop_verified']); self.assertTrue(proof['container_running'])
        self.docker.container['State']={'Running':False,'Status':'exited'}
        self.assertFalse(self.gate.stop_proof({'identity':identity})['stop_verified'])
        self.assertFalse(any('stop' in a for a,_ in self.docker.calls))
    def test_stop_only_after_owned_drain_and_root_exit_even_for_failed_attempt(self):
        identity=self.allocate(); report={'attempt_id':identity['worker_id'],'session_id':identity['session_id'],'identity_sha256':remote.document_sha(identity),
            'execution':{'session_id':identity['session_id'],'clock_id':'linux-boot:'+str(uuid4()),'root_exit_monotonic_ns':20,'owned_work_drained_monotonic_ns':30,'root_exit_code':1},'state':'held'}
        self.assertFalse(self.gate.stop_after_drain(identity,{**report,'execution':{}}))
        self.assertTrue(self.docker.container['State']['Running'])
        self.docker.files['/tmp/at-native/report.json']=encoded(report)
        clock=report['execution']['clock_id']
        self.docker.files['/tmp/at-native/capture/events.jsonl']=encoded({'sequence':1,'kind':'root_process_exited','monotonic_ns':20,'session_id':identity['session_id'],'clock_id':clock,'exit_code':1})+encoded({'sequence':2,'kind':'owned_work_drained','monotonic_ns':30,'session_id':identity['session_id'],'clock_id':clock})
        self.assertTrue(self.gate.stop_after_drain(identity,report)); self.assertTrue(self.gate.stop_proof({'identity':identity})['stop_verified'])
    def test_arm_is_persistent_one_use_and_does_not_write_token(self):
        identity=self.allocate(); request=self.arm_request(identity); barrier=request['barrier_id']; token=request['token']
        called=[]
        def detach(ident,barrier_id,credential): called.append((ident,barrier_id,credential)); return {'armed':True,'identity':ident,'barrier_id':barrier_id}
        result=self.gate.arm(request,detach=detach); self.assertTrue(result['armed']); self.assertEqual(len(called),1)
        for path in (self.root/'attempts').rglob('*'):
            if path.is_file(): self.assertNotIn(token.encode(),path.read_bytes())
        with self.assertRaisesRegex(remote.GateError,'already_claimed'): self.gate.arm(request,detach=detach)
        self.assertEqual(len(called),1)
    def test_open_requires_all_armed_and_cannot_reopen(self):
        identity=self.allocate(); barrier=str(uuid4()); request={'barrier_id':barrier,'identities':[identity]}
        with self.assertRaises(remote.GateError): self.gate.open(request)
        directory=self.root/'attempts'/identity['worker_id']
        (directory/'armed.json').write_bytes(encoded({'armed':True,'identity':identity,'barrier_id':barrier}))
        self.assertTrue(self.gate.open(request)['opened'])
        with self.assertRaisesRegex(remote.GateError,'already_claimed'): self.gate.open(request)
    def test_observation_reads_only_metadata_and_ignores_partial_last_event(self):
        identity=self.allocate(); worker={'schema_version':'agenttime.natural-worker-observation.v1','attempt_id':identity['worker_id'],'session_id':identity['session_id'],'task_id':'gpqa-01','clock_id':'linux-boot:abc','state':'active'}
        self.docker.files['/tmp/at-native/observation.json']=encoded(worker)
        event={'kind':'prompt_released','sequence':1,'session_id':identity['session_id'],'clock_id':'linux-boot:abc'}
        self.docker.files['/tmp/at-native/capture/events.jsonl']=encoded(event)+b'{"unfinished"'
        value=self.gate.observe({'attempt_id':identity['worker_id'],'identity':identity})
        self.assertEqual(value['events'],[event]); self.assertIsNone(value['report'])
        paths=[argv[-2] for argv,_ in self.docker.calls if 'cp' in argv]
        self.assertFalse(any('stream.jsonl' in p or 'sealed-answer' in p for p in paths))
    def test_report_or_observation_identity_mismatch_is_held(self):
        identity=self.allocate(); self.docker.files['/tmp/at-native/observation.json']=encoded({'attempt_id':'wrong'})
        with self.assertRaisesRegex(remote.GateError,'observation'): self.gate.observe({'attempt_id':identity['worker_id'],'identity':identity})

    def test_nonsecret_docker_inputs_are_readable_under_private_umask(self):
        identity=self.allocate(); old=os.umask(0o077)
        try: self.prepared(identity)
        finally: os.umask(old)
        self.assertEqual((self.root/'attempts'/identity['worker_id']/'spec.json').stat().st_mode&0o777,0o644)
    def test_auth_spec_drift_prevents_supervisor_arm(self):
        identity=self.allocate(); request=self.arm_request(identity); request['authorization']['spec_sha256']='0'*64
        called=[]
        with self.assertRaisesRegex(remote.GateError,'authorization'): self.gate.arm(request,detach=lambda *args:called.append(args))
        self.assertEqual(called,[])
    def test_drain_claim_without_matching_journal_never_stops(self):
        identity=self.allocate(); report={'attempt_id':identity['worker_id'],'session_id':identity['session_id'],'identity_sha256':remote.document_sha(identity),
            'execution':{'session_id':identity['session_id'],'root_exit_monotonic_ns':20,'owned_work_drained_monotonic_ns':30,'root_exit_code':0}}
        self.assertFalse(self.gate.stop_after_drain(identity,report)); self.assertTrue(self.docker.container['State']['Running'])
    def test_observation_cannot_forward_unknown_prompt_or_answer_fields(self):
        identity=self.allocate(); value={'schema_version':'agenttime.natural-worker-observation.v1','attempt_id':identity['worker_id'],
            'session_id':identity['session_id'],'task_id':'gpqa-01','prompt':'must not leave host'}
        self.docker.files['/tmp/at-native/observation.json']=encoded(value)
        with self.assertRaisesRegex(remote.GateError,'metadata'): self.gate.observe({'attempt_id':identity['worker_id'],'identity':identity})
    def test_actual_detach_returns_receipt_without_inheriting_ssh_stdio(self):
        identity=self.allocate(); barrier=str(uuid4()); marker=self.root/'detached-done.json'
        def bounded_supervisor(ident,gate,token):
            marker.write_bytes(encoded({'identity':ident,'barrier':gate,'different_session':os.getsid(0)!=os.getsid(os.getppid()),'auth_in_memory':len(token)>12}))
        self.gate.supervise=bounded_supervisor
        result=self.gate._detach(identity,barrier,'offline-only-secret-abcdefghijklmnop')
        self.assertTrue(result['armed'])
        for _ in range(100):
            if marker.exists(): break
            time.sleep(.01)
        self.assertTrue(json.loads(marker.read_bytes())['auth_in_memory'])
        self.assertNotIn(b'offline-only-secret',marker.read_bytes())
    def test_supervisor_duplicate_launch_does_not_execute_again(self):
        identity=self.allocate(); request=self.arm_request(identity); barrier=request['barrier_id']; directory=self.root/'attempts'/identity['worker_id']
        (self.root/'barriers').mkdir(); (self.root/'barriers'/barrier).write_bytes(encoded({'barrier_id':barrier,'identities':[identity]}))
        (directory/'launch-intent.json').write_bytes(b'{}')
        before=len(self.docker.calls)
        with self.assertRaisesRegex(remote.GateError,'already_claimed'): self.gate.supervise(identity,barrier,request['token'])
        self.assertFalse(any('run' in a for a,_ in self.docker.calls[before:]))

    def test_empty_prestart_network_id_is_allowed_only_before_start(self):
        self.docker.empty_created=True
        identity=self.allocate()
        self.assertTrue(self.gate._owned(identity)[2]['State']['Running'])
    def test_empty_running_network_id_is_rejected(self):
        self.docker.empty_running=True
        with self.assertRaisesRegex(remote.GateError,'network'): self.allocate()
    def test_empty_stopped_network_id_still_requires_original_owned_network(self):
        identity=self.allocate(); self.docker.container['State']={'Running':False,'Status':'exited'}
        for network in self.docker.container['NetworkSettings']['Networks'].values(): network['NetworkID']=''
        self.assertFalse(self.gate.stop_proof({'identity':identity})['stop_verified'])
        self.docker.network['Id']='e'*64
        with self.assertRaisesRegex(remote.GateError,'network'): self.gate.stop_proof({'identity':identity})
    def test_real_worker_serialized_success_and_failure_metadata_schemas(self):
        import test_natural_worker
        for family,failed in [('gpqa',False),('browsecomp',False),('hle',True)]:
            fixture=test_natural_worker.NaturalWorkerTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
            root,spec=fixture.prepared(family)
            def simulate(*args):
                fixture.simulate(*args)
                if family=='browsecomp': fixture.add_child_evidence(root)
                if failed: args[-1].issues.append('synthetic_failure')
            report=fixture.run_fake(root,spec,simulate=simulate)
            self.assertEqual(report['state'],'held' if failed else 'captured')
            saved=json.loads((root/'report.json').read_bytes())
            remote.Gate._metadata(saved,'report')
            remote.Gate._metadata(json.loads((root/'observation.json').read_bytes()),'worker')
            for line in (root/'capture/events.jsonl').read_bytes().splitlines(): remote.Gate._metadata(json.loads(line),'event')
    def test_supervisor_credential_output_is_never_persisted_or_accepted(self):
        identity=self.allocate(); request=self.arm_request(identity); barrier=request['barrier_id']; directory=self.root/'attempts'/identity['worker_id']
        (self.root/'barriers').mkdir(); (self.root/'barriers'/barrier).write_bytes(encoded({'barrier_id':barrier,'identities':[identity]}))
        original=self.gate.command
        def command(argv,payload=b'',**kwargs):
            if 'run' in argv:
                self.assertEqual(kwargs['timeout'],None); self.assertNotIn(request['token'],str(argv)); self.assertEqual(payload,(request['token']+'\n').encode())
                return request['token'].encode()
            return original(argv,payload,**kwargs)
        self.gate.command=command
        with self.assertRaisesRegex(remote.GateError,'credential'): self.gate.supervise(identity,barrier,request['token'])
        for path in directory.iterdir():
            if path.is_file(): self.assertNotIn(request['token'].encode(),path.read_bytes())
        self.assertTrue(self.docker.container['State']['Running'])
    def test_archive_stream_filters_root_metadata_but_preserves_native_state(self):
        identity=self.allocate(); receipt=b'{"inventory":{}}\n'; self.docker.files['/tmp/at-native/baseline.json']=receipt
        raw=io.BytesIO()
        with tarfile.open(fileobj=raw,mode='w') as archive:
            for name in ('home','config','tmp','work','xdg','capture'):
                item=tarfile.TarInfo('./'+name); item.type=tarfile.DIRTYPE; item.mode=0o700; archive.addfile(item)
            for name,data in [('./capture/native.jsonl',b'original native bytes'),('./spec.json',b'outside-six-roots')]:
                item=tarfile.TarInfo(name); item.size=len(data); archive.addfile(item,io.BytesIO(data))
        class Process:
            def __init__(self): self.stdout=io.BytesIO(raw.getvalue()); self.returncode=None
            def wait(self): self.returncode=0; return 0
            def poll(self): return self.returncode
            def kill(self): self.returncode=-9
        output=io.BytesIO()
        with patch.object(remote.subprocess,'Popen',return_value=Process()): self.gate.archive({'identity':identity,'phase':'baseline'},output)
        with tarfile.open(fileobj=io.BytesIO(output.getvalue())) as archive:
            self.assertNotIn('spec.json',archive.getnames())
            self.assertEqual(archive.extractfile('capture/native.jsonl').read(),b'original native bytes')
            self.assertEqual(archive.extractfile('__receipt__.json').read(),receipt)

    def test_verified_stop_receipt_is_stable_across_controller_restarts(self):
        identity=self.allocate(); self.docker.container['State']={'Running':False,'Status':'exited'}
        receipt={'identity':identity,'stop_verified':True,'container_running':False,'external_verification':True,'observed_at':'2026-10-09T00:00:00+00:00'}
        (self.root/'attempts'/identity['worker_id']/'stop-proof.json').write_bytes(encoded(receipt))
        self.assertEqual(self.gate.stop_proof({'identity':identity}),receipt)
        self.assertEqual(self.gate.stop_proof({'identity':identity}),receipt)

    def test_barrier_never_exposes_partial_json_to_waiting_supervisor(self):
        identity=self.allocate(); request=self.arm_request(identity); barrier=request['barrier_id']; directory=self.root/'attempts'/identity['worker_id']
        (directory/'armed.json').write_bytes(encoded({'armed':True,'identity':identity,'barrier_id':barrier}))
        (self.root/'barriers').mkdir(); published=self.root/'barriers'/barrier
        finished=threading.Event(); failures=[]
        def wait_for_release():
            try: self.gate.supervise(identity,barrier,request['token'])
            except remote.GateError as error: failures.append(str(error))
            finally: finished.set()
        supervisor=threading.Thread(target=wait_for_release)
        original_open=os.open
        def delayed_open(path,flags,*args,**kwargs):
            fd=original_open(path,flags,*args,**kwargs)
            if Path(path)==published and flags&os.O_CREAT:
                finished.wait(2)  # Expose the original create-before-write window deterministically.
            return fd
        supervisor.start()
        try:
            with patch.object(remote.os,'open',side_effect=delayed_open): self.gate.open({'barrier_id':barrier,'identities':[identity]})
            self.assertTrue(finished.wait(2))
        finally: supervisor.join(timeout=3)
        self.assertNotIn('invalid_json',failures)
        self.assertTrue((directory/'launch-intent.json').exists())
        self.assertEqual(sum('run' in argv for argv,_ in self.docker.calls),1)
    def test_barrier_file_is_fsynced_before_exclusive_publication(self):
        identity=self.allocate(); barrier=str(uuid4()); directory=self.root/'attempts'/identity['worker_id']
        (directory/'armed.json').write_bytes(encoded({'armed':True,'identity':identity,'barrier_id':barrier}))
        published=self.root/'barriers'/barrier; synced=set(); linked=[]; original_fsync=os.fsync; original_link=os.link
        def fsync(fd): synced.add(os.fstat(fd).st_ino); return original_fsync(fd)
        def link(source,target,*args,**kwargs):
            if Path(target)==published:
                self.assertFalse(published.exists()); self.assertIn(Path(source).stat().st_ino,synced)
                self.assertEqual(json.loads(Path(source).read_bytes())['barrier_id'],barrier); linked.append(True)
            return original_link(source,target,*args,**kwargs)
        with patch.object(remote.os,'fsync',side_effect=fsync),patch.object(remote.os,'link',side_effect=link):
            self.gate.open({'barrier_id':barrier,'identities':[identity]})
        self.assertEqual(linked,[True]); original=published.read_bytes()
        with self.assertRaisesRegex(remote.GateError,'already_claimed'): self.gate.open({'barrier_id':barrier,'identities':[identity]})
        self.assertEqual(published.read_bytes(),original)

    def test_failed_barrier_write_retains_claim_without_visible_release(self):
        identity=self.allocate(); barrier=str(uuid4()); directory=self.root/'attempts'/identity['worker_id']
        (directory/'armed.json').write_bytes(encoded({'armed':True,'identity':identity,'barrier_id':barrier}))
        request={'barrier_id':barrier,'identities':[identity]}; original_new=remote.new
        def interrupted_new(path,data,mode=0o600):
            original_new(path,data,mode)
            if str(path).endswith('.pending'): raise OSError('synthetic interruption before publication')
        with patch.object(remote,'new',side_effect=interrupted_new),self.assertRaises(OSError): self.gate.open(request)
        self.assertFalse((self.root/'barriers'/barrier).exists())
        with self.assertRaisesRegex(remote.GateError,'already_claimed'): self.gate.open(request)
        self.assertFalse((self.root/'barriers'/barrier).exists())

if __name__=='__main__': unittest.main()
