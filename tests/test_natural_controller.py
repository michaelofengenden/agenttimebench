"""Offline controller orchestration, using no model/provider/authentication calls."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import copy
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from uuid import uuid4

from agenttime.natural_ledger import NaturalLedger, document_sha256, validate_identity, validate_manifest
from agenttime.natural_inputs import SELECTED_SLOTS


def sha(data):
    return hashlib.sha256(data).hexdigest()


class MemoryLedger:
    """Thread-safe fake with the real ledger's receipt validators and API."""
    def __init__(self):
        self.lock = threading.RLock(); self.rows = {}; self.campaigns = {}; self.reason = None
    def create_campaign(self, campaign_id, manifest):
        validate_manifest(manifest)
        with self.lock:
            if campaign_id in self.campaigns and self.campaigns[campaign_id] != manifest: raise ValueError('immutable')
            self.campaigns[campaign_id] = copy.deepcopy(manifest)
    def campaign(self, campaign_id):
        return {'id': campaign_id, 'manifest': copy.deepcopy(self.campaigns[campaign_id]),
                'manifest_sha256': document_sha256(self.campaigns[campaign_id])}
    def attempts(self, campaign_id):
        with self.lock: return copy.deepcopy([r for r in self.rows.values() if r['campaign_id'] == campaign_id])
    def attempt(self, attempt_id):
        with self.lock: return copy.deepcopy(self.rows[attempt_id])
    def gate(self): return {'paused_reason': self.reason}
    def pause(self, reason): self.reason = self.reason or reason
    def reserve(self, campaign_id):
        with self.lock:
            if self.reason: return None
            manifest = self.campaigns[campaign_id]
            if sum(r['holds_capacity'] for r in self.rows.values()) >= manifest['capacity']: return None
            used = {r['task_id'] for r in self.rows.values()}
            task = next((t for t in manifest['tasks'] if t['id'] not in used), None)
            if task is None: return None
            row = {'id': str(uuid4()), 'task_id': task['id'], 'campaign_id': campaign_id,
                'contract_sha256': task['contract_sha256'], 'input_sha256': task['input_sha256'],
                'runtime_pins': task['runtime_pins'], 'identity': None, 'identity_sha256': None,
                'baseline_ack': None, 'release_authorized': False, 'prompt_release': None, 'report': None,
                'state': 'reserved', 'holds_capacity': True}
            self.rows[row['id']] = row; return copy.deepcopy(row)
    def claim(self, attempt_id, identity):
        validate_identity(identity)
        with self.lock:
            row = self.rows[attempt_id]
            if row['state'] != 'reserved' or self.reason: return False
            if any(r['identity'] and (r['identity']['session_id'] == identity['session_id'] or
                r['identity']['container_id'] == identity['container_id']) for r in self.rows.values()): raise ValueError('reused identity')
            row.update(identity=copy.deepcopy(identity), identity_sha256=document_sha256(identity), state='claimed'); return True
    def acknowledge_baseline(self, attempt_id, identity, acknowledgement):
        with self.lock:
            row = self.rows[attempt_id]; NaturalLedger._owner(row, identity); NaturalLedger._validate_ack(row, acknowledgement)
            if row['baseline_ack'] is not None and row['baseline_ack'] != acknowledgement: raise ValueError('immutable ACK')
            row['baseline_ack'] = copy.deepcopy(acknowledgement)
    def authorize_release(self, attempt_id, identity):
        with self.lock:
            row = self.rows[attempt_id]; NaturalLedger._owner(row, identity)
            if self.reason or row['state'] != 'claimed' or not row['baseline_ack'] or row['release_authorized']: return False
            row['release_authorized'] = True; return True
    def mark_prompt_released(self, attempt_id, identity, event):
        with self.lock:
            row = self.rows[attempt_id]; NaturalLedger._owner(row, identity); NaturalLedger._validate_release(row, event)
            if not row['release_authorized']: raise ValueError('not authorized')
            if row['prompt_release'] is not None:
                if row['prompt_release'] != event: raise ValueError('changed release')
                return False
            row.update(prompt_release=copy.deepcopy(event), state='released'); return True
    def quarantine(self, attempt_id, reason):
        with self.lock:
            row = self.rows[attempt_id]
            if row['state'] == 'finished': return False
            row.update(state='quarantined', reason=reason); return True
    def finish(self, attempt_id, identity, report):
        with self.lock:
            row = self.rows[attempt_id]; NaturalLedger._owner(row, identity); NaturalLedger._validate_report(row, report)
            if row['state'] == 'finished' and row['report'] != report: raise ValueError('immutable final')
            row.update(state='finished', holds_capacity=False, report=copy.deepcopy(report))


class FakeTransport:
    def __init__(self, module, base, ledger, now):
        self.module=module; self.base=base; self.ledger=ledger; self.now=now
        self.lock=threading.Lock(); self.calls=[]; self.state={}; self.fail_prepare=set(); self.bad_baseline=set()
        self.bad_final=set(); self.lost=set(); self.stop=False; self.fail_barrier=False
    def allocate(self, intent):
        row=self.ledger.attempt(intent['attempt_id'])
        assert row['state']=='reserved' and (Path(intent['controller_intent_path'])).is_file()
        identity={'worker_id':'worker-'+row['id'],'session_id':intent['session_id'],'host_id':'fake-host',
            'daemon_id':'fake-daemon','docker_endpoint':'unix:///fake.sock','container_id':sha(row['id'].encode())}
        with self.lock:
            self.calls.append(('allocate', row['id'])); self.state[row['id']]={'identity':identity,'intent':copy.deepcopy(intent)}
        return identity
    def prepare(self, spec, input_bytes):
        attempt=spec['attempt_id']; item=self.state[attempt]; item.update(spec=copy.deepcopy(spec), input=input_bytes)
        assert self.ledger.attempt(attempt)['state']=='claimed'
        with self.lock: self.calls.append(('prepare',attempt))
        if spec['task_id'] in self.fail_prepare: raise OSError('do not persist this secret error body')
        root=self.base/attempt; root.mkdir()
        for name in self.module.STATE_ROOTS: (root/name).mkdir()
        (root/'capture/prepared-spec.json').write_bytes(self.module.native_json(spec))
        (root/'capture/prepared-input.json').write_bytes(input_bytes)
        inventory=self.module.scan_archive(root)
        baseline={'schema_version':'agenttime.natural-worker-baseline.v1','attempt_id':attempt,
            'session_id':spec['session_id'],'identity_sha256':document_sha256(spec['identity']),
            'spec_sha256':sha(self.module.native_json(spec)), 'contract_sha256':spec['preparation_contract_sha256'],
            'input_sha256':sha(input_bytes),'inventory':inventory,'inventory_sha256':sha(self.module.native_json(inventory)),
            'native_state':'empty_store_with_predeclared_session_id','study_admitted':False}
        item.update(root=root, baseline=baseline, events=[], report=None)
        return {'state':'prepared','attempt_id':attempt,'session_id':spec['session_id'],
            'spec_sha256':baseline['spec_sha256'],'baseline_sha256':sha(self.module.native_json(baseline)),
            'inventory_sha256':baseline['inventory_sha256'],'study_admitted':False}
    def copy_archive(self, identity, phase, destination):
        item=next(v for v in self.state.values() if v['identity']==identity)
        with self.lock: self.calls.append(('archive_'+phase,item['spec']['attempt_id']))
        shutil.copytree(item['root'],destination)
        receipt=item['baseline'] if phase=='baseline' else item['archive_receipt']
        if (phase=='baseline' and item['spec']['task_id'] in self.bad_baseline) or (phase=='final' and item['spec']['task_id'] in self.bad_final):
            (destination/'work/injected').write_text('not in inventory')
        return self.module.ArchiveCopy(self.module.native_json(receipt), credential_scan_verified=True)
    def release_barrier(self, barrier_id, members):
        assert all(self.ledger.attempt(m['authorization']['attempt_id'])['release_authorized'] for m in members)
        assert len(self.state)==50
        for member in members:
            row=self.ledger.attempt(member['authorization']['attempt_id'])
            assert member['baseline_ack']==row['baseline_ack']
            self.state[row['id']]['authorization']=copy.deepcopy(member['authorization'])
        with self.lock: self.calls.append(('barrier',len(members)))
        if self.fail_barrier: raise OSError('uncertain remote release')
    def observe(self, intent, identity):
        if intent['task_id'] in self.lost: return None
        item=self.state[intent['attempt_id']]
        return copy.deepcopy(item.get('observation'))
    def stop_proof(self, identity):
        return {'identity':copy.deepcopy(identity),'stop_verified':self.stop,'container_running':not self.stop,
                'external_verification':True,'observed_at':self.now.isoformat()}
    def native_sessions(self, attempt, children=()):
        item=self.state[attempt]; sid=item['spec']['session_id']; cwd='/tmp/at-natural/work'
        parent=f'config/projects/-tmp-at-natural-work/{sid}.jsonl'
        stores=[(None,parent)]+[(child,f'config/projects/-tmp-at-natural-work/{sid}/subagents/agent-{child}.jsonl') for child in children]
        evidence={'original_native_stores_verified':True,'restoration_proven':False,'session_id':sid,'children':{}}
        for child,path in stores:
            rows=[{'type':'user','sessionId':sid,'isSidechain':child is not None,'agentId':child,
                   'message':json.loads(item['input'])['message']},
                  {'type':'assistant','sessionId':sid,'isSidechain':child is not None,'agentId':child,
                   'message':{'role':'assistant','content':[{'type':'text','text':'private synthetic answer'}]}}]
            target=item['root']/path; target.parent.mkdir(parents=True,exist_ok=True)
            data=b''.join(self.module.native_json(row) for row in rows); target.write_bytes(data)
            entry={'path':path,'sha256':sha(data),'message_count':len(rows)}
            if child is None:evidence['parent']=entry
            else:evidence['children'][child]=entry
        stream=[{'type':'system','subtype':'init','session_id':sid,'cwd':cwd},
                {'type':'result','subtype':'success','session_id':sid,'result':'private synthetic answer',
                 'subagent_stats':{'spawned':len(children)}}]
        data=b''.join(self.module.native_json(row) for row in stream)
        (item['root']/'capture/stream.jsonl').write_bytes(data); evidence['stream_sha256']=sha(data)
        return evidence
    def refresh_archive(self, attempt):
        item=self.state[attempt]; inventory=self.module.scan_archive(item['root'])
        receipt={'schema_version':'agenttime.native-local-archive.v1','inventory':inventory,
                 'inventory_sha256':sha(self.module.native_json(inventory)),'verified':True}
        item['archive_receipt']=receipt; item['observation']['report']['archive']=receipt
    def observe_run(self, attempt, finished=False, stale=False, override=None):
        item=self.state[attempt]; spec=item['spec']; sid=spec['session_id']; clock='fake-boot:worker'
        def event(sequence,kind,n,**extra):
            return {'sequence':sequence,'kind':kind,'monotonic_ns':n,'audit_utc':self.now.isoformat(),
                    'session_id':sid,'clock_id':clock,**extra}
        events=[event(1,'prompt_released',100,input_sha256=spec['input_sha256'],attempt_id=attempt)]
        report=None
        if finished:
            answer=b'private synthetic answer'; answer_sha=sha(answer)
            events += [event(2,'answer_sealed',200,sha256=answer_sha),event(3,'root_process_exited',250,exit_code=0),
                       event(4,'owned_work_drained',300),event(5,'native_terminal',300,sealed_answer_sha256=answer_sha)]
            (item['root']/'capture/sealed-answer.txt').write_bytes(answer)
            (item['root']/'capture/delivered-input.json').write_bytes(item['input'])
            native_evidence=self.native_sessions(attempt)
            inventory=self.module.scan_archive(item['root'])
            receipt={'schema_version':'agenttime.native-local-archive.v1','inventory':inventory,
                     'inventory_sha256':sha(self.module.native_json(inventory)),'verified':True}
            execution={'session_id':sid,'prompt_released_monotonic_ns':100,'result_monotonic_ns':200,
                'root_exit_monotonic_ns':250,'owned_work_drained_monotonic_ns':300,'native_terminal_monotonic_ns':300,
                'sealed_answer_sha256':answer_sha,'timing_valid':True,'root_exit_code':0,'cancelled':False,
                'runtime_seconds':200/1e9,'issues':[]}
            report={'schema_version':'agenttime.natural-worker-report.v1','attempt_id':attempt,'session_id':sid,
                'identity_sha256':document_sha256(spec['identity']),'spec_sha256':sha(self.module.native_json(spec)),
                'input_sha256':spec['input_sha256'],'authorization_sha256':document_sha256(item['authorization']),
                'state':'captured','execution':execution,'archive':receipt,'archive_verified':True,
                'native_sessions_verified':True,'native_session_evidence':native_evidence,
                'included_subscription_allowance_verified':True,'timing_admissible':True,'issues':[]}
            item['archive_receipt']=receipt
        observed=self.now-timedelta(seconds=60) if stale else self.now
        worker={'schema_version':'agenttime.natural-worker-observation.v1','attempt_id':attempt,'task_id':spec['task_id'],
            'session_id':sid,'state':'captured' if finished else 'active','source_observed_at':observed.isoformat(),
            'worker_monotonic_ns':400,'last_native_event_monotonic_ns':events[-1]['monotonic_ns'],'issues':[]}
        packet={'identity':item['identity'],'clock_id':clock,'worker':worker,'events':events,'report':report}
        if override: override(packet)
        item['observation']=packet

    def observe_failure(self, attempt):
        self.observe_run(attempt, finished=True)
        item=self.state[attempt]; packet=item['observation']; report=packet['report']
        packet['events']=[event for event in packet['events'] if event['kind'] not in ('answer_sealed','native_terminal')]
        for sequence,event in enumerate(packet['events'],1):
            event['sequence']=sequence
            if event['kind']=='root_process_exited':event['exit_code']=1
        report.update(state='held',timing_admissible=False,native_sessions_verified=False,issues=['invalid_native_result'])
        report.pop('native_session_evidence')
        report['execution'].update(result_monotonic_ns=None,native_terminal_monotonic_ns=None,
            sealed_answer_sha256=None,timing_valid=False,root_exit_code=1,runtime_seconds=None,issues=['invalid_native_result'])
        (item['root']/'capture/sealed-answer.txt').unlink()
        inventory=self.module.scan_archive(item['root'])
        receipt={'schema_version':'agenttime.native-local-archive.v1','inventory':inventory,
            'inventory_sha256':sha(self.module.native_json(inventory)),'verified':True}
        item['archive_receipt']=receipt;report['archive']=receipt
        packet['worker']['state']='held'


class ControllerTests(unittest.TestCase):
    def setUp(self):
        try:self.nc=importlib.import_module('agenttime.natural_controller')
        except ModuleNotFoundError:self.fail('Natural controller is not implemented')
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.base=Path(self.tmp.name).resolve()
        self.root=self.base/'controller'; self.remote=self.base/'fake-worker'; self.remote.mkdir()
        self.runtime=self.base/'runtime'; self.runtime.mkdir()
        for name in ('worker','cli','native','python','bridge'): (self.runtime/name).write_text('synthetic '+name)
        self.pins={'controller_sha256':sha(Path(self.nc.__file__).read_bytes()),
            **{k+'_sha256':sha((self.runtime/v).read_bytes()) for k,v in [('worker','worker'),('cli','cli'),
              ('native_session','native'),('python','python'),('bridge','bridge')]}}
        sources={'controller_sha256':str(Path(self.nc.__file__).resolve()),
            **{k+'_sha256':str(self.runtime/v) for k,v in [('worker','worker'),('cli','cli'),
              ('native_session','native'),('python','python'),('bridge','bridge')]}}
        self.proof=b'{"synthetic_qualification":true}\n'; self.tasks={}; manifest_tasks=[]
        for task in SELECTED_SLOTS:
            family=task.split('-')[0]; closed=family in ('gpqa','hle')
            data=self.nc.native_json({'type':'user','message':{'role':'user','content':[{'type':'text','text':'Private synthetic task '+task}]}})
            contract=self.nc.native_json({'slot_id':task,'arm':'natural','duration_request':None})
            template={'schema_version':'agenttime.natural-worker.v1','executor':'claude-natural-v1','arm':'natural',
                'task_id':task,'family_id':family,'preparation_contract_sha256':sha(contract),'input_sha256':sha(data),
                'model':'claude-opus-5-5[1m]','effort':'max','route':'subscription',
                'tool_policy':{'native_tools':['Agent','WebSearch','WebFetch'] if family=='browsecomp' else [],
                    'mcp_tools':list(self.nc.ASSISTANT_TOOLS) if family=='assistant' else [],'native_subagents':family=='browsecomp'},
                'resources':{'allocation_scope':'no_task_compute' if closed else 'natural_subject',
                    'cpus':None if closed else 4,'memory_gib':None if closed else 16,'gpu_count':0},
                'runtime_pins':{'cli_version':'2.1.280','cli_sha256':self.pins['cli_sha256'],
                    'image_sha256':'sha256:'+'a'*64,'worker_sha256':self.pins['worker_sha256'],
                    'native_session_sha256':self.pins['native_session_sha256'],'python_sha256':self.pins['python_sha256'],
                    'bridge_sha256':self.pins['bridge_sha256'] if family=='assistant' else None}}
            self.tasks[task]={'contract_bytes':contract,'input_bytes':data,'template':template}
            manifest_tasks.append({'id':task,'contract_sha256':sha(contract),'input_sha256':sha(data),
                'runtime_pins':{'image_sha256':'a'*64,'spec_template_sha256':document_sha256(template)}})
        self.manifest={'schema_version':1,'executor':'claude-natural-v1','cohort_id':'synthetic-cohort','arm':'natural',
            'agent':{'id':'synthetic-agent','model':'claude-opus-5-5[1m]','effort':'max','route':'subscription'},
            'capacity':50,'automatic_replacement':False,'runtime_pins':self.pins,
            'qualification_proof_sha256':sha(self.proof),'tasks':manifest_tasks}
        self.nc.NaturalController.freeze(self.root,'synthetic-pilot',self.manifest,self.tasks,sources,self.proof)
        self.ledger=MemoryLedger(); self.now=datetime(2026,10,9,12,tzinfo=timezone.utc)
        self.transport=FakeTransport(self.nc,self.remote,self.ledger,self.now)
        self.controller=self.open()
    def open(self, qualify=True):
        return self.nc.NaturalController(self.root,self.ledger,self.transport,
            qualification_verifier=(lambda manifest,proof:qualify),now=lambda:self.now)
    def rows(self):return self.ledger.attempts('synthetic-pilot')
    def stage_release(self):
        self.controller.stage_all(); self.controller.release_all(); return self.rows()

    def test_fifty_staged_before_common_barrier_and_no_claimed_task_is_native_active(self):
        result=self.controller.stage_all()
        self.assertEqual(result['ready'],50)
        self.assertFalse(any(c[0]=='barrier' for c in self.transport.calls))
        self.assertEqual(len({r['identity']['session_id'] for r in self.rows()}),50)
        self.assertTrue(all(not o.get('native_release_verified') for o in self.controller.observations().values()))
        result=self.controller.release_all()
        self.assertEqual(result['released_permissions'],50)
        self.assertEqual([c for c in self.transport.calls if c[0]=='barrier'],[('barrier',50)])
        self.assertTrue(all(r['prompt_release'] is None for r in self.rows()))

    def test_duplicate_stage_and_release_races_never_replay(self):
        def call(method):
            try: return method()
            except self.nc.ControllerError:return None
        with ThreadPoolExecutor(max_workers=4) as pool: stages=list(pool.map(lambda _:call(self.controller.stage_all),range(4)))
        self.assertEqual(sum(x is not None for x in stages),1)
        with ThreadPoolExecutor(max_workers=4) as pool: releases=list(pool.map(lambda _:call(self.controller.release_all),range(4)))
        self.assertEqual(sum(x is not None for x in releases),1)
        self.assertEqual(sum(c[0]=='allocate' for c in self.transport.calls),50)
        self.assertEqual(sum(c[0]=='barrier' for c in self.transport.calls),1)
        with self.assertRaises(self.nc.ControllerError):self.open().stage_all()
        with self.assertRaises(self.nc.ControllerError):self.open().release_all()

    def test_baseline_tamper_isolated_and_unaffected_tasks_can_release(self):
        self.transport.bad_baseline.add('gpqa-01')
        result=self.controller.stage_all(); self.assertEqual(result['ready'],49)
        self.assertTrue(all(r['holds_capacity'] for r in self.rows()))
        self.assertEqual(self.controller.release_all()['released_permissions'],49)
        row=next(r for r in self.rows() if r['task_id']=='gpqa-01')
        self.assertFalse(row['release_authorized'])

    def test_uncertain_prepare_is_not_repeated_on_restart(self):
        self.transport.fail_prepare.add('gpqa-01'); self.controller.stage_all()
        before=list(self.transport.calls)
        self.open().reconcile()
        self.assertEqual([c for c in self.transport.calls if c[0] in ('allocate','prepare')],
                         [c for c in before if c[0] in ('allocate','prepare')])
        self.assertTrue(all(r['holds_capacity'] for r in self.rows()))
        self.assertNotIn('secret error body',json.dumps(self.controller.observations()))

    def test_source_and_compiled_input_drift_pause_before_launch(self):
        path=self.root/'inputs'/ (self.manifest['tasks'][0]['input_sha256']+'.json')
        path.write_text('changed')
        with self.assertRaises(self.nc.ControllerError):self.controller.stage_all()
        self.assertEqual(self.transport.calls,[]); self.assertIsNotNone(self.ledger.reason)

    def test_unqualified_proof_blocks_release_without_consuming_native_permission(self):
        self.controller.stage_all()
        with self.assertRaises(self.nc.ControllerError):self.open(False).release_all()
        self.assertFalse(any(r['release_authorized'] for r in self.rows()))
        self.assertFalse(any(c[0]=='barrier' for c in self.transport.calls))

    def test_uncertain_barrier_never_retried_and_does_not_claim_native_onset(self):
        self.controller.stage_all(); self.transport.fail_barrier=True
        self.controller.release_all()
        with self.assertRaises(self.nc.ControllerError):self.open().release_all()
        self.assertTrue(all(r['release_authorized'] and r['holds_capacity'] for r in self.rows()))
        self.assertFalse(any(o.get('native_release_verified') for o in self.controller.observations().values()))

    def test_matching_native_release_and_stale_or_lost_observation_keep_claim(self):
        row=self.stage_release()[0]; self.transport.observe_run(row['id'])
        self.controller.reconcile()
        obs=self.controller.observations()[row['task_id']]
        self.assertTrue(obs['native_release_verified']); self.assertIsNotNone(self.ledger.attempt(row['id'])['prompt_release'])
        self.transport.lost.add(row['task_id']); self.open().reconcile()
        obs=self.controller.observations()[row['task_id']]
        self.assertTrue(obs['native_release_verified']); self.assertTrue(obs['observation_invalid'])
        self.assertTrue(self.ledger.attempt(row['id'])['holds_capacity'])
        self.transport.lost.clear(); self.transport.observe_run(row['id'],stale=True); self.controller.reconcile()
        self.assertTrue(self.controller.observations()[row['task_id']]['observation_invalid'])

    def test_changed_native_event_or_identity_fails_closed(self):
        row=self.stage_release()[0]; self.transport.observe_run(row['id']); self.controller.reconcile()
        self.transport.observe_run(row['id'],override=lambda p:p['events'][0].update(input_sha256='0'*64))
        self.controller.reconcile()
        self.assertTrue(self.ledger.attempt(row['id'])['holds_capacity'])
        self.assertTrue(self.controller.observations()[row['task_id']]['observation_invalid'])

    def test_drain_archive_and_external_stop_are_distinct(self):
        row=self.stage_release()[0]; self.transport.observe_run(row['id'],finished=True)
        self.controller.reconcile()
        self.assertTrue(self.ledger.attempt(row['id'])['holds_capacity'])
        self.assertFalse(self.controller.observations()[row['task_id']]['stop_verified'])
        self.transport.stop=True; self.open().reconcile()
        finished=self.ledger.attempt(row['id']); self.assertEqual(finished['state'],'finished')
        self.assertFalse(finished['holds_capacity'])
        obs=self.controller.observations()[row['task_id']]
        self.assertTrue(obs['stop_verified']); self.assertEqual(obs['archive']['status'],'acknowledged')
        queue=list((self.root/'grade-queue').glob('*.json')); self.assertEqual(len(queue),1)
        text=queue[0].read_text(); self.assertNotIn('private synthetic answer',text); self.assertNotIn('Private synthetic task',text)
        self.assertEqual(json.loads(text)['status'],'queued')

    def test_bad_final_archive_cannot_finish_even_with_stop(self):
        row=self.stage_release()[0]; self.transport.observe_run(row['id'],finished=True)
        self.transport.bad_final.add(row['task_id']); self.transport.stop=True
        self.controller.reconcile()
        self.assertTrue(self.ledger.attempt(row['id'])['holds_capacity'])
        self.assertEqual(list((self.root/'grade-queue').glob('*.json')),[])

    def test_completed_requires_positive_original_native_session_evidence(self):
        rows=self.stage_release()[:4]; self.transport.stop=True
        changes=(lambda report:report.pop('native_sessions_verified'),
                 lambda report:report.update(native_sessions_verified=False),
                 lambda report:report.pop('native_session_evidence'),
                 lambda report:report['native_session_evidence'].update(original_native_stores_verified=False))
        for row,change in zip(rows,changes):
            self.transport.observe_run(row['id'],finished=True)
            change(self.transport.state[row['id']]['observation']['report'])
        self.controller.reconcile()
        for row in rows:
            with self.subTest(attempt=row['id']):
                self.assertTrue(self.ledger.attempt(row['id'])['holds_capacity'])
                self.assertFalse(self.controller.observations()[row['task_id']]['stop_verified'])
        self.assertEqual(list((self.root/'grade-queue').glob('*.json')),[])

    def test_completed_requires_session_stream_parent_and_child_archive_bindings(self):
        rows=self.stage_release()[:8]; self.transport.stop=True
        for row in rows:
            self.transport.observe_run(row['id'],finished=True)
            item=self.transport.state[row['id']]
            item['observation']['report']['native_session_evidence']=self.transport.native_sessions(row['id'],('child-a',))
            self.transport.refresh_archive(row['id'])
        reports=[self.transport.state[row['id']]['observation']['report'] for row in rows]
        reports[0]['native_session_evidence']['session_id']=str(uuid4())
        reports[1]['native_session_evidence']['stream_sha256']='0'*64
        reports[2]['native_session_evidence']['parent']['sha256']='0'*64
        reports[3]['native_session_evidence']['parent']['path']='capture/stream.jsonl'
        reports[4]['native_session_evidence']['children']['child-a']['sha256']='0'*64
        reports[5]['native_session_evidence']['children']['child-a']['path']=reports[5]['native_session_evidence']['parent']['path']
        reports[6]['native_session_evidence']['parent']['message_count']=True
        missing=self.transport.state[rows[7]['id']]
        (missing['root']/reports[7]['native_session_evidence']['children']['child-a']['path']).unlink()
        self.transport.refresh_archive(rows[7]['id'])
        self.controller.reconcile()
        for row in rows:
            with self.subTest(attempt=row['id']):self.assertTrue(self.ledger.attempt(row['id'])['holds_capacity'])
        self.assertEqual(list((self.root/'grade-queue').glob('*.json')),[])

    def test_completed_rejects_missing_parent_and_unreported_native_stores(self):
        rows=self.stage_release()[:2]; self.transport.stop=True
        for row in rows:self.transport.observe_run(row['id'],finished=True)
        first=self.transport.state[rows[0]['id']]; report=first['observation']['report']
        (first['root']/report['native_session_evidence']['parent']['path']).unlink()
        self.transport.refresh_archive(rows[0]['id'])
        second=self.transport.state[rows[1]['id']]
        self.transport.native_sessions(rows[1]['id'],('unreported-child',))
        # The stream hash is current, but the original child store is omitted from evidence.
        second['observation']['report']['native_session_evidence']['stream_sha256']=sha((second['root']/'capture/stream.jsonl').read_bytes())
        self.transport.refresh_archive(rows[1]['id'])
        self.controller.reconcile()
        self.assertTrue(all(self.ledger.attempt(row['id'])['holds_capacity'] for row in rows))
        self.assertEqual(list((self.root/'grade-queue').glob('*.json')),[])

    def test_completed_child_archive_has_durable_native_evidence_binding(self):
        row=next(row for row in self.stage_release() if row['task_id'].startswith('browsecomp'))
        self.transport.observe_run(row['id'],finished=True); self.transport.stop=True
        item=self.transport.state[row['id']]
        evidence=self.transport.native_sessions(row['id'],('child-a','child-b'))
        item['observation']['report']['native_session_evidence']=evidence
        self.transport.refresh_archive(row['id']); self.controller.reconcile()
        finished=self.ledger.attempt(row['id']); self.assertEqual(finished['state'],'finished')
        self.assertTrue(finished['report']['native_sessions_verified'])
        self.assertEqual(finished['report']['native_session_evidence_sha256'],document_sha256(evidence))
        ack=json.loads((self.root/'attempts'/row['id']/'final-archive-ack.json').read_bytes())
        self.assertEqual(ack['native_session_evidence_sha256'],document_sha256(evidence))
        self.assertTrue(ack['native_sessions_verified'])
        calls=list(self.transport.calls); self.open().reconcile()
        self.assertEqual(self.transport.calls,calls)
        self.assertEqual(len(list((self.root/'grade-queue').glob('*.json'))),1)

    def test_failed_drained_attempt_without_native_stores_still_archives(self):
        row=self.stage_release()[0]; self.transport.observe_failure(row['id']); self.transport.stop=True
        item=self.transport.state[row['id']]
        shutil.rmtree(item['root']/'config/projects'); (item['root']/'capture/stream.jsonl').unlink()
        self.transport.refresh_archive(row['id']); self.controller.reconcile()
        finished=self.ledger.attempt(row['id'])
        self.assertEqual(finished['state'],'finished'); self.assertFalse(finished['holds_capacity'])
        self.assertEqual(finished['report']['outcome'],'unknown_failure')
        self.assertEqual(finished['report']['timing_status'],'invalid')
        self.assertFalse(finished['report']['native_sessions_verified'])
        self.assertIsNone(finished['report']['native_session_evidence_sha256'])
        self.assertEqual(list((self.root/'grade-queue').glob('*.json')),[])

    def test_restart_requires_native_evidence_ack_before_queuing_grade(self):
        row=self.stage_release()[0]; self.transport.observe_run(row['id'],finished=True); self.transport.stop=True
        def fail_queue(*args):raise OSError('synthetic crash before grade publication')
        self.controller._queue_grade=fail_queue; self.controller.reconcile()
        self.assertEqual(self.ledger.attempt(row['id'])['state'],'finished')
        path=self.root/'attempts'/row['id']/'final-archive-ack.json'
        ack=json.loads(path.read_bytes()); ack.pop('native_session_evidence_sha256',None); ack.pop('native_sessions_verified',None)
        path.write_bytes(self.nc.canonical_json(ack)); self.open().reconcile()
        self.assertEqual(list((self.root/'grade-queue').glob('*.json')),[])
        self.assertTrue(self.controller.observations()[row['task_id']]['observation_invalid'])

    def test_retained_native_canary_evidence_binds_to_original_independent_archive(self):
        from agenttime.natural_worker import validate_native_stores
        base=Path(__file__).resolve().parents[1]/'qualification/native-session-20261009'
        cases=(('synthetic-native-closed-book-ba4db711e948','gpqa'),
               ('synthetic-native-image-ccfd7964f077','hle'),
               ('synthetic-native-web-tools-9159fde3da47','browsecomp'),
               ('synthetic-native-web-subagent-78cd93e4a144','browsecomp'),
               ('synthetic-native-browser-4615dee9f7fa','assistant'))
        checked=0
        for name,family in cases:
            root=base/name/'final-state'
            if not (root/'archive').exists():continue
            with self.subTest(case=name):
                stream=[json.loads(line) for line in (root/'capture/stream.jsonl').read_bytes().splitlines()]
                init=next(row for row in stream if row.get('type')=='system' and row.get('subtype')=='init')
                spec={'session_id':init['session_id'],'family_id':family,'tool_policy':{'native_subagents':family=='browsecomp'}}
                inventory=self.nc.scan_archive(root/'archive')
                evidence=validate_native_stores(root,spec,(root/'capture/delivered-input.json').read_bytes(),runtime_cwd=init['cwd'])
                digest=self.nc._native_archive_evidence({'native_sessions_verified':True,'native_session_evidence':evidence},
                    init['session_id'],inventory)
                self.assertEqual(digest,document_sha256(evidence))
                self.assertEqual(inventory,self.nc.scan_archive(root/'archive'))
                checked+=1
        if not checked:self.skipTest('retained native qualification archives are unavailable')

    def test_untrusted_observation_fields_never_reach_public_snapshot(self):
        row=self.stage_release()[0]
        self.transport.observe_run(row['id'],override=lambda p:p['worker'].update(prompt='SECRET_PROMPT',token='SECRET_TOKEN',answer='SECRET_ANSWER'))
        self.controller.reconcile(); text=json.dumps(self.controller.observations())
        for marker in ('SECRET_PROMPT','SECRET_TOKEN','SECRET_ANSWER'):self.assertNotIn(marker,text)

    def test_actual_native_journal_kinds_are_accepted(self):
        source=Path(__file__).resolve().parents[1]/'qualification/native-session-20261009/synthetic-native-closed-book-ba4db711e948/final-state/archive/capture/events.jsonl'
        if not source.exists(): self.skipTest('retained actual synthetic journal is unavailable')
        row=self.stage_release()[0]; self.transport.observe_run(row['id'])
        packet=self.transport.state[row['id']]['observation']
        events=[json.loads(line) for line in source.read_text().splitlines()]
        for event in events:
            event['session_id']=row['identity']['session_id']; event['clock_id']=packet['clock_id']
            event['audit_utc']=self.now.isoformat()
            if event['kind']=='prompt_released':
                event['attempt_id']=row['id']; event['input_sha256']=row['input_sha256']
            if event.get('metadata',{}).get('session_id'):
                event['metadata']['session_id']=row['identity']['session_id']
        packet['events']=events
        packet['worker']['worker_monotonic_ns']=events[-1]['monotonic_ns']+1
        packet['worker']['last_native_event_monotonic_ns']=events[-1]['monotonic_ns']
        self.controller.reconcile()
        observation=self.controller.observations()[row['task_id']]
        self.assertTrue(observation['native_release_verified'])
        self.assertFalse(observation['observation_invalid'])
        self.assertTrue(self.ledger.attempt(row['id'])['holds_capacity'])

    def test_source_registry_cannot_be_deleted_to_skip_runtime_checks(self):
        (self.root/'runtime-sources.json').write_text('{}')
        with self.assertRaises(self.nc.ControllerError): self.controller.stage_all()
        self.assertEqual(self.transport.calls,[])

    def test_post_staging_spec_drift_cannot_consume_release_permission(self):
        self.controller.stage_all(); row=self.rows()[0]
        specpath=self.root/'attempts'/row['id']/'spec.json'
        spec=json.loads(specpath.read_bytes()); spec['runtime_pins']['image_sha256']='sha256:'+'0'*64
        specpath.write_bytes(self.nc.native_json(spec))
        with self.assertRaises(self.nc.ControllerError): self.controller.release_all()
        self.assertFalse(any(r['release_authorized'] for r in self.rows()))

    def test_wrong_external_container_proof_does_not_finish(self):
        row=self.stage_release()[0]; self.transport.observe_run(row['id'],finished=True); self.transport.stop=True
        original=self.transport.stop_proof
        def wrong(identity):
            proof=original(identity); proof['identity']['container_id']='0'*64; return proof
        self.transport.stop_proof=wrong
        self.controller.reconcile()
        self.assertTrue(self.ledger.attempt(row['id'])['holds_capacity'])

    def test_drained_failure_is_archived_and_releases_only_after_external_stop(self):
        row=self.stage_release()[0]; self.transport.observe_failure(row['id'])
        self.controller.reconcile()
        self.assertTrue(('archive_final',row['id']) in self.transport.calls)
        self.assertTrue(self.ledger.attempt(row['id'])['holds_capacity'])
        self.assertEqual(self.controller.observations()[row['task_id']]['archive']['status'],'acknowledged')
        self.transport.stop=True; self.open().reconcile()
        finished=self.ledger.attempt(row['id'])
        self.assertEqual(finished['state'],'finished');self.assertFalse(finished['holds_capacity'])
        self.assertEqual(finished['report']['outcome'],'unknown_failure')
        self.assertEqual(finished['report']['timing_status'],'invalid')
        self.assertIsNone(finished['report']['runtime_seconds'])
        self.assertEqual(list((self.root/'grade-queue').glob('*.json')),[])
        self.assertEqual(self.controller.observations()[row['task_id']]['grade']['status'],'unavailable')

    def test_successful_native_terminal_with_unverified_route_is_archived_as_failure(self):
        row=self.stage_release()[0];self.transport.observe_run(row['id'],finished=True);self.transport.stop=True
        packet=self.transport.state[row['id']]['observation']
        packet['report'].update(state='held',issues=['subscription_allowance_unverified'],
            included_subscription_allowance_verified=False,timing_admissible=False)
        self.controller.reconcile()
        final=self.ledger.attempt(row['id'])['report']
        self.assertIsNotNone(final);self.assertEqual(final['outcome'],'unknown_failure')
        self.assertEqual(final['timing_status'],'invalid');self.assertIsNone(final['runtime_seconds'])
        self.assertEqual(list((self.root/'grade-queue').glob('*.json')),[])

    def test_failure_without_owned_drain_or_matching_archive_stays_held(self):
        rows=self.stage_release();self.transport.stop=True
        a,b=rows[:2];self.transport.observe_failure(a['id']);self.transport.observe_failure(b['id'])
        packet=self.transport.state[a['id']]['observation']
        packet['events']=[e for e in packet['events'] if e['kind']!='owned_work_drained']
        packet['report']['execution']['owned_work_drained_monotonic_ns']=None
        self.transport.bad_final.add(b['task_id'])
        self.controller.reconcile()
        self.assertTrue(all(self.ledger.attempt(row['id'])['holds_capacity'] for row in (a,b)))
        self.assertNotIn(('archive_final',a['id']),self.transport.calls)
        self.assertFalse(any(self.controller.observations()[r['task_id']]['stop_verified'] for r in (a,b)))

    def test_restart_repairs_publication_lost_after_durable_finish(self):
        rows=self.stage_release();self.transport.stop=True
        for row in rows[:2]:self.transport.observe_run(row['id'],finished=True)
        self.transport.observe_failure(rows[1]['id'])
        publish=self.controller._publish
        def crash_after_finish(row,**values):
            if values.get('stop_verified') is True:raise OSError('synthetic publication failure')
            return publish(row,**values)
        self.controller._publish=crash_after_finish
        self.controller.reconcile()
        self.assertTrue(all(self.ledger.attempt(row['id'])['state']=='finished' for row in rows[:2]))
        calls=list(self.transport.calls)
        self.open().reconcile()
        self.assertEqual(self.transport.calls,calls)
        for row in rows[:2]:
            observation=self.controller.observations()[row['task_id']]
            self.assertTrue(observation['stop_verified']);self.assertFalse(observation['observation_invalid'])
            self.assertEqual(observation['archive']['status'],'acknowledged')
        self.assertEqual(self.controller.observations()[rows[0]['task_id']]['timing']['status'],'valid')
        self.assertEqual(self.controller.observations()[rows[1]['task_id']]['timing']['status'],'invalid')

    def test_restart_finishes_original_receipt_after_ledger_commit_was_interrupted(self):
        row=self.stage_release()[0];self.transport.observe_run(row['id'],finished=True);self.transport.stop=True
        finish=self.ledger.finish
        def fail_commit(*args):raise OSError('synthetic failed ledger commit')
        self.ledger.finish=fail_commit;self.controller.reconcile()
        self.assertTrue(self.ledger.attempt(row['id'])['holds_capacity'])
        self.assertTrue((self.root/'attempts'/row['id']/'final-report.json').exists())
        self.ledger.finish=finish;self.now+=timedelta(seconds=10);self.transport.now=self.now
        self.open().reconcile()
        self.assertEqual(self.ledger.attempt(row['id'])['state'],'finished')

    def test_finished_projection_preserves_subsequent_grade_and_restore_status(self):
        row=self.stage_release()[0];self.transport.observe_run(row['id'],finished=True);self.transport.stop=True
        self.controller.reconcile()
        grade={'status':'available','value':1.0,'scale':'fraction'}
        self.controller._publish(row,grade=grade,archive={'status':'acknowledged','restore_status':'verified'})
        self.open().reconcile();observation=self.controller.observations()[row['task_id']]
        self.assertEqual(observation['grade'],grade)
        self.assertEqual(observation['archive']['restore_status'],'verified')

    def test_failure_receipt_changes_and_unproved_stop_cannot_release(self):
        row=self.stage_release()[0];self.transport.observe_failure(row['id'])
        self.controller.reconcile();self.transport.stop=True
        report=self.transport.state[row['id']]['observation']['report']
        report['archive']['native_restore_qualified']=True
        self.controller.reconcile()
        self.assertTrue(self.ledger.attempt(row['id'])['holds_capacity'])
        self.assertFalse(self.controller.observations()[row['task_id']]['stop_verified'])

    def test_fifty_observations_use_bounded_parallel_reads_before_archival(self):
        rows=self.stage_release();self.transport.stop=True
        for row in rows:self.transport.observe_run(row['id'])
        self.transport.observe_run(rows[0]['id'],finished=True)
        original=self.transport.observe;copy_archive=self.transport.copy_archive
        lock=threading.Lock();inflight=set();peak=0;seen=set();duplicate=[]
        def delayed(intent,identity):
            nonlocal peak
            attempt=intent['attempt_id']
            with lock:
                if attempt in inflight:duplicate.append(attempt)
                inflight.add(attempt);peak=max(peak,len(inflight))
            time.sleep(.04)
            packet=original(intent,identity)
            with lock:inflight.remove(attempt);seen.add(attempt)
            return packet
        def after_all_reads(identity,phase,destination):
            if phase=='final':
                self.assertEqual(len(seen),50)
                self.assertTrue(all(o.get('native_release_verified') for o in self.controller.observations().values()))
            return copy_archive(identity,phase,destination)
        self.transport.observe=delayed;self.transport.copy_archive=after_all_reads
        started=time.monotonic();self.controller.reconcile(max_workers=8);duration=time.monotonic()-started
        self.assertGreater(peak,1);self.assertLessEqual(peak,8);self.assertEqual(duplicate,[])
        self.assertLess(duration,2.0)
        self.assertEqual(self.ledger.attempt(rows[0]['id'])['state'],'finished')
        self.assertTrue(all(o.get('native_release_verified') for o in self.controller.observations().values()))

    def test_overlapping_reconciliations_never_overlap_the_same_attempt(self):
        rows=self.stage_release()
        for row in rows:self.transport.observe_run(row['id'])
        observe=self.transport.observe;lock=threading.Lock();active=set();duplicates=[]
        def delayed(intent,identity):
            attempt=intent['attempt_id']
            with lock:
                if attempt in active:duplicates.append(attempt)
                active.add(attempt)
            time.sleep(.002)
            try:return observe(intent,identity)
            finally:
                with lock:active.remove(attempt)
        self.transport.observe=delayed
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _:self.controller.reconcile(max_workers=8),range(2)))
        self.assertEqual(duplicates,[])
        self.assertTrue(all(row['prompt_release'] is not None for row in self.rows()))

    def test_archive_scan_rejects_traversal_extra_files_and_external_links(self):
        folder=self.base/'scan'; folder.mkdir()
        for name in self.nc.STATE_ROOTS:(folder/name).mkdir()
        (folder/'work/escape').symlink_to(self.base)
        with self.assertRaises(self.nc.ControllerError):self.nc.scan_archive(folder)


from db_support import DatabaseTestCase
import psycopg


class ControllerPostgresTests(DatabaseTestCase):
    def test_real_ledger_fifty_stage_release_and_verified_completion(self):
        fixture=ControllerTests('test_fifty_staged_before_common_barrier_and_no_claimed_task_is_native_active')
        fixture.setUp(); self.addCleanup(fixture.doCleanups)
        with psycopg.connect(self.dsn) as connection:
            connection.execute('DROP SCHEMA IF EXISTS agenttime_natural CASCADE')
        ledger=NaturalLedger(self.dsn); ledger.initialize(50)
        fixture.ledger=ledger; fixture.transport.ledger=ledger; fixture.controller=fixture.open()
        self.assertEqual(fixture.controller.stage_all(max_workers=8)['ready'],50)
        self.assertEqual(fixture.controller.release_all()['released_permissions'],50)
        rows=fixture.rows(); self.assertEqual(len(rows),50)
        self.assertTrue(all(row['holds_capacity'] and row['release_authorized'] for row in rows))
        row=rows[0]; fixture.transport.observe_run(row['id'],finished=True); fixture.transport.stop=True
        fixture.controller.reconcile()
        self.assertEqual(ledger.attempt(row['id'])['state'],'finished')
        self.assertEqual(sum(item['holds_capacity'] for item in fixture.rows()),49)
        failed=rows[1];fixture.transport.observe_failure(failed['id']);fixture.controller.reconcile()
        failure_report=ledger.attempt(failed['id'])['report']
        self.assertEqual(failure_report['outcome'],'unknown_failure')
        self.assertEqual(failure_report['timing_status'],'invalid')
        self.assertEqual(sum(item['holds_capacity'] for item in fixture.rows()),48)
        with self.assertRaises(ValueError):
            ledger.reserve_replacement(failed['id'],{'kind':'explicit_operator_request'})
        with self.assertRaises(fixture.nc.ControllerError):fixture.open().release_all()


if __name__=='__main__':unittest.main()
