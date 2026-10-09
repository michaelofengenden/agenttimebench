import hashlib
import io
import json
from pathlib import Path
import shlex
import tarfile
import tempfile
import threading
import unittest
from uuid import uuid4

try:
    from agenttime import ssh_transport as transport
except ImportError:
    transport = None

TOKEN = 'offline-only-secret-abcdefghijklmnop'
ROOTS = ('home', 'config', 'tmp', 'work', 'xdg', 'capture')


def encoded(value):
    return json.dumps(value, sort_keys=True).encode()


def identity(host='h1', attempt=None):
    return dict(worker_id=attempt or str(uuid4()), session_id=str(uuid4()), host_id=host,
                daemon_id='daemon-' + host, docker_endpoint='unix:///var/run/docker.sock', container_id='c' * 64)


def tar_bytes(extra=(), receipt=b'{"verified":true}\n'):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w') as archive:
        item = tarfile.TarInfo('__receipt__.json'); item.size = len(receipt); archive.addfile(item, io.BytesIO(receipt))
        for name in ROOTS:
            item = tarfile.TarInfo(name); item.type = tarfile.DIRTYPE; item.mode = 0o700; archive.addfile(item)
        for name, data, kind in extra:
            item = tarfile.TarInfo(name); item.mode = 0o600
            if kind == 'link': item.type = tarfile.SYMTYPE; item.linkname = data; archive.addfile(item)
            elif kind == 'hardlink': item.type = tarfile.LNKTYPE; item.linkname = data; archive.addfile(item)
            else: item.size = len(data); archive.addfile(item, io.BytesIO(data))
    return out.getvalue()


class FakeSSH:
    def __init__(self): self.calls=[]; self.lock=threading.Lock(); self.fail_arm=None; self.tar=tar_bytes(); self.bad_identity=False
    def __call__(self, argv, payload, *, timeout, output=None):
        command = shlex.split(argv[-1]); operation=command[-1]; request=json.loads(payload)
        with self.lock: self.calls.append((argv, operation, request))
        if operation == 'arm' and request['identity']['host_id'] == self.fail_arm: raise TimeoutError('do not leak ' + TOKEN)
        if operation == 'archive':
            for start in range(0,len(self.tar),13): output.write(self.tar[start:start+13])
            return b''
        if operation == 'allocate':
            value=identity('h1',request['intent']['attempt_id']); value['session_id']=request['intent']['session_id']
            if self.bad_identity: value['host_id']='other'
            return encoded(value)
        if operation == 'arm': return encoded({'armed':True,'barrier_id':request['barrier_id'],'identity':request['identity']})
        if operation == 'open': return encoded({'opened':True,'barrier_id':request['barrier_id']})
        if operation == 'observe': return encoded(None)
        if operation == 'stop-proof': return encoded({'identity':request['identity'],'stop_verified':False,'container_running':True,'external_verification':True,'observed_at':'2026-10-09T00:00:00+00:00'})
        return encoded({'state':'prepared'})


class SSHTransportTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(transport, 'transport implementation missing')
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root=Path(self.tmp.name).resolve()
        self.runner=FakeSSH(); self.reads=0
        def token(): self.reads += 1; return TOKEN
        self.hosts=[transport.HostPlan(host_id='h'+str(i),address='192.0.2.'+str(i),daemon_id='daemon-h'+str(i),
            key_path=self.root/'key',known_hosts_path=self.root/'known_hosts',remote_root='/srv/agenttime/pilot50',
            runtime_bundle_sha256='a'*64,image_sha256='sha256:'+'b'*64,host_config_sha256='d'*64) for i in (1,2,3)]
        self.client=transport.SSHTransport(self.hosts,token_supplier=token,runner=self.runner)
    def intent(self):
        return dict(campaign_id=str(uuid4()),manifest_sha256='e'*64,attempt_id=str(uuid4()),task_id='gpqa-01',session_id=str(uuid4()),
                    dispatch_id=str(uuid4()),resources={'allocation_scope':'no_task_compute','cpus':None,'memory_gib':None,'gpu_count':0},
                    runtime_pins={'image_sha256':'sha256:'+'b'*64},controller_intent_path='/private/controller/intent')
    def members(self):
        return [{'identity':identity('h'+str(i)),'baseline_ack':{'x':'nonsecret'},'authorization':{'attempt_id':str(uuid4())}} for i in (1,2,3)]
    def test_allocation_uses_strict_ssh_and_no_auth_read(self):
        result=self.client.allocate(self.intent())
        self.assertEqual(result['host_id'],'h1'); self.assertEqual(self.reads,0)
        argv,operation,request=self.runner.calls[0]
        self.assertEqual(argv[0],'/usr/bin/ssh'); self.assertIn('StrictHostKeyChecking=yes',argv)
        self.assertIn('IdentitiesOnly=yes',argv); self.assertIn('BatchMode=yes',argv)
        self.assertNotIn('controller_intent_path',request['intent']); self.assertNotIn(TOKEN,str(argv))
    def test_wrong_host_identity_is_rejected(self):
        self.runner.bad_identity=True
        with self.assertRaisesRegex(transport.TransportError,'identity'): self.client.allocate(self.intent())
    def test_all_arms_precede_every_open_and_secret_only_in_arm_stdin(self):
        members=self.members(); self.client.release_barrier(str(uuid4()),members)
        operations=[v[1] for v in self.runner.calls]
        self.assertEqual(operations[:3],['arm']*3); self.assertEqual(operations[3:],['open']*3)
        self.assertEqual(self.reads,1)
        for argv,op,request in self.runner.calls:
            self.assertNotIn(TOKEN,str(argv)); self.assertEqual(TOKEN in str(request),op=='arm')
    def test_one_unknown_arm_keeps_all_host_barriers_closed(self):
        self.runner.fail_arm='h2'
        with self.assertRaisesRegex(transport.TransportError,'arm_outcome_unknown') as caught:
            self.client.release_barrier(str(uuid4()),self.members())
        self.assertNotIn(TOKEN,str(caught.exception)); self.assertNotIn('open',[v[1] for v in self.runner.calls])
    def test_readonly_reconciliation_never_reads_token(self):
        ident=identity(); self.assertIsNone(self.client.observe(self.intent(),ident)); self.client.stop_proof(ident)
        self.assertEqual(self.reads,0); self.assertEqual([c[1] for c in self.runner.calls],['observe','stop-proof'])
    def test_archive_preserves_modes_relative_links_and_original_receipt(self):
        self.runner.tar=tar_bytes([('capture/result',b'ok','file'),('config/latest','../capture/result','link')])
        destination=self.root/'archive'; result=self.client.copy_archive(identity(),'final',destination)
        self.assertTrue(result.credential_scan_verified); self.assertEqual(result.receipt_bytes,b'{"verified":true}\n')
        self.assertEqual((destination/'capture/result').stat().st_mode&0o777,0o600)
        self.assertEqual((destination/'config/latest').read_bytes(),b'ok'); self.assertEqual(self.reads,1)
        self.assertEqual(set(p.name for p in destination.iterdir()),set(ROOTS))
    def test_archive_rejects_traversal_hardlink_extra_root_and_auth_store(self):
        for index,entry in enumerate([('../escape',b'bad','file'),('/absolute',b'bad','file'),('capture/link','../../escape','link'),
            ('capture/link','capture/other','hardlink'),('unexpected',b'bad','file'),('config/.credentials.json',b'bad','file')]):
            with self.subTest(entry=entry):
                self.runner.tar=tar_bytes([entry])
                with self.assertRaises(transport.TransportError): self.client.copy_archive(identity(),'final',self.root/f'a{index}')
        self.assertFalse((self.root/'escape').exists())
    def test_archive_detects_secret_across_stream_chunks_before_publishing(self):
        self.runner.tar=tar_bytes([('capture/output',b'x'*7+TOKEN.encode()+b'y','file')])
        destination=self.root/'archive'
        with self.assertRaisesRegex(transport.TransportError,'credential'): self.client.copy_archive(identity(),'final',destination)
        self.assertFalse(destination.exists())
    def test_archive_requires_fresh_destination_and_never_overwrites(self):
        destination=self.root/'old'; destination.mkdir(); (destination/'keep').write_text('original')
        with self.assertRaises(transport.TransportError): self.client.copy_archive(identity(),'baseline',destination)
        self.assertEqual((destination/'keep').read_text(),'original'); self.assertEqual(self.runner.calls,[])
    def test_host_and_remote_paths_cannot_inject_shell_arguments(self):
        with self.assertRaises(transport.TransportError):
            transport.HostPlan(host_id='h1',address='host;bad',daemon_id='daemon',key_path=self.root/'key',known_hosts_path=self.root/'hosts',
                remote_root='/srv/agenttime/../outside',runtime_bundle_sha256='a'*64,image_sha256='sha256:'+'b'*64,host_config_sha256='d'*64)

    def test_normalized_duplicate_archive_directories_are_rejected(self):
        output=io.BytesIO()
        with tarfile.open(fileobj=output,mode='w') as archive:
            for name in ('__receipt__.json',):
                item=tarfile.TarInfo(name); item.size=2; archive.addfile(item,io.BytesIO(b'{}'))
            for name in (*ROOTS,'./home'):
                item=tarfile.TarInfo(name); item.type=tarfile.DIRTYPE; item.mode=0o700; archive.addfile(item)
        self.runner.tar=output.getvalue()
        with self.assertRaises(transport.TransportError): self.client.copy_archive(identity(),'final',self.root/'archive')

if __name__=='__main__': unittest.main()
