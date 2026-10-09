"""Offline production-driver boundaries; synthetic contracts, no provider calls."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from agenttime import natural_controller as controller_module
from agenttime.evidence import canonical_json
from agenttime.natural_inputs import SELECTED_SLOTS
from agenttime.natural_ledger import document_sha256
from test_natural_inputs import contract, png
from test_natural_controller import MemoryLedger, FakeTransport
try:
    from agenttime import natural_pilot as subject
except ImportError:
    subject = None


def sha(value): return hashlib.sha256(value).hexdigest()
def write(path, value): path.write_bytes(controller_module.native_json(value))


class FakeGrader:
    def __init__(self):
        self.calls = []; self.fail = set(); self.bad = set(); self.pins_sha256 = 'a' * 64
    def grade(self, attempt):
        self.calls.append(attempt)
        if attempt in self.fail: raise RuntimeError('PRIVATE_TOKEN_AND_ANSWER')
        slot = self.tasks[attempt]; family = slot.split('-')[0]
        return {'schema_version': 'agenttime.natural-local-grade.v1', 'attempt_id': attempt,
                'task_id': slot, 'family_id': family, 'scorer_pins_sha256': self.pins_sha256,
                'scale': [0, 1], 'status': 'queued' if family in ('hle', 'browsecomp') else 'available',
                'score': 99 if attempt in self.bad else None if family in ('hle', 'browsecomp') else 1,
                'evidence': {'private_answer': 'PRIVATE_TOKEN_AND_ANSWER'}, 'reason': None}


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(subject, 'natural_pilot has not been implemented')
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve(); (self.repo/'contracts').mkdir()
        self.cli = self.repo/'claude'; self.cli.write_bytes(b'synthetic pinned cli, never executed')
        self.roster = self.repo/'manifest.json'
        rows = []
        for slot in SELECTED_SLOTS:
            value = contract(slot)
            if slot in ('hle-03', 'hle-07', 'hle-09', 'hle-19'):
                data=png(); asset=self.repo/'benchmarks/cache/hle-question-images-20261002/images'/ (value['candidate_id']+'.png')
                asset.parent.mkdir(parents=True, exist_ok=True); asset.write_bytes(data)
                value['allowed_materials']=[{'kind':'image','name':'question.png','media_type':'image/png',
                    'sha256':sha(data),'bytes':len(data),'width':1,'height':1}]
            path=self.repo/'contracts'/(slot+'.json'); write(path,value)
            rows.append({'task_id':slot,'label':'Synthetic '+slot,'benchmark':slot.split('-')[0],
                'contract_path':str(path.relative_to(self.repo)), 'contract_sha256':sha(path.read_bytes()),
                'candidate_id':value['candidate_id'],'interface':'closed_book' if slot.startswith(('gpqa','hle')) else 'web',
                'required_resources':value['intended_target']['hardware_allocation']})
        self.public={'campaign_id':'synthetic-pilot50','tasks':rows,'target_concurrency':50,
            'arm':'natural','model':'claude-opus-5-5','effort':'max','route':'michael_subscription',
            'duration_request':None,'experiment_cap_seconds':None,'launch_ready':False}
        write(self.roster,self.public)
        self.frozen_roster=self.repo/'frozen-roster.json';subject.freeze_roster(self.roster,self.frozen_roster)
        self.frozen=json.loads(self.frozen_roster.read_bytes())
        sources={name:sha((subject.SOURCE_ROOT/name).read_bytes()) for name in subject.DEPLOYED_FILES}
        sources['bin/claude']=sha(self.cli.read_bytes())
        bundle=sha(controller_module.native_json({'schema_version':'agenttime.runtime-files.v1','files':sources}))
        self.deployments=[]
        for index in range(3):
            path=self.repo/f'host{index}.json'
            write(path,{'host_id':f'host{index}','address':f'worker{index}.invalid','daemon_id':f'daemon{index}',
                'remote_root':'/srv/agenttime/pilot50-v2','runtime_bundle_sha256':bundle,
                'image_sha256':'sha256:'+'e'*64,'host_config_sha256':str(index)*64,
                'python_sha256':'f'*64,'source_files':sources,'study_attempts':0})
            self.deployments.append(path)
        self.proof=b'synthetic qualification proof, not native evidence'
        self.plan=self.compile()

    def compile(self):
        return subject.compile_pilot(self.repo,self.frozen_roster,self.deployments,self.cli,self.proof,
            key_path=self.repo/'nonexistent-key',known_hosts_path=self.repo/'nonexistent-known-hosts')

    def driver(self, qualifier=lambda *args: True):
        root=self.repo/str(uuid4()); self.plan.freeze(root,grader_pins={'synthetic':True},assistant_python=self.repo/'python')
        ledger=MemoryLedger(); remote=self.repo/str(uuid4()); remote.mkdir()
        from datetime import datetime,timezone
        transport=FakeTransport(controller_module,remote,ledger,datetime.now(timezone.utc))
        controller=controller_module.NaturalController(root,ledger,transport)
        grader=FakeGrader()
        driver=subject.NaturalPilot(controller,grader,qualification_verifier=qualifier)
        return driver,controller,ledger,transport,grader

    def test_compiles_exact_native_text_four_images_and_remote_python_pin(self):
        self.assertEqual(set(self.plan.tasks),set(SELECTED_SLOTS))
        self.assertEqual(len(self.plan.hosts),3)
        images=[]
        for slot,item in self.plan.tasks.items():
            event=json.loads(item['input_bytes'])
            self.assertEqual(event['message']['content'][0]['text'],contract(slot)['native_messages'][0]['content'])
            self.assertNotIn('CONTROLLER_',json.dumps(event))
            if any(x['type']=='image' for x in event['message']['content']): images.append(slot)
            self.assertEqual(item['template']['runtime_pins']['python_sha256'],'f'*64)
        self.assertEqual(images,['hle-03','hle-07','hle-09','hle-19'])
        self.assertFalse(self.plan.manifest['automatic_replacement'])

    def test_plan_repr_never_contains_native_materials(self):
        text=repr(self.plan)
        self.assertFalse('Solve this fictional problem' in text)
        self.assertFalse('CONTROLLER_' in text)
        self.assertFalse('synthetic qualification proof' in text)

    def test_open_wires_saved_dependencies_without_reading_token_or_dispatching(self):
        driver,controller,ledger,transport,grader=self.driver()
        token_reads=[]
        def token(): token_reads.append(True); raise RuntimeError('must not read')
        with patch.object(subject,'SSHTransport',return_value=transport) as remote, patch.object(subject,'LocalGrader',return_value=grader) as grade:
            reopened=subject.open_pilot(controller.root,self.repo,ledger,token)
            self.assertIsInstance(reopened,subject.NaturalPilot)
            self.assertEqual(len(remote.call_args.args[0]),3)
            self.assertIs(remote.call_args.kwargs['token_supplier'],token)
            self.assertEqual(grade.call_args.args[2],{'synthetic':True})
        self.assertEqual(token_reads,[]);self.assertEqual(transport.calls,[])

    def test_roster_duplicates_missing_tasks_hash_drift_and_path_escape_fail(self):
        for change in ('duplicate','missing','hash','escape','duration'):
            with self.subTest(change=change):
                value=copy.deepcopy(self.frozen)
                if change=='duplicate': value['tasks'][-1]=copy.deepcopy(value['tasks'][0])
                elif change=='missing': value['tasks'].pop()
                elif change=='hash': value['tasks'][0]['contract_sha256']='0'*64
                elif change=='escape': value['tasks'][0]['contract_path']='../outside.json'
                else: value['duration_request']=60
                self.frozen_roster.write_bytes(canonical_json(value))
                with self.assertRaises(subject.PilotError): self.compile()
        self.frozen_roster.write_bytes(canonical_json(self.frozen))

    def test_deployment_disagreement_duplicate_host_and_source_drift_fail(self):
        path=self.deployments[-1]; original=path.read_bytes()
        for change in ('python','host','source','bundle'):
            with self.subTest(change=change):
                value=json.loads(original)
                if change=='python': value['python_sha256']='1'*64
                elif change=='host': value['host_id']='host0'
                elif change=='source': value['source_files']['src/agenttime/natural_worker.py']='0'*64
                else: value['runtime_bundle_sha256']='0'*64
                write(path,value)
                with self.assertRaises(subject.PilotError): self.compile()
        path.write_bytes(original)

    def test_freeze_is_immutable_and_metadata_tamper_is_rejected(self):
        driver,controller,_,_,_=self.driver()
        with self.assertRaises(subject.PilotError): self.plan.freeze(controller.root,grader_pins={})
        path=controller.root/'pilot-driver.json'; value=json.loads(path.read_bytes()); value['repo_root']='/changed'; write(path,value)
        with self.assertRaisesRegex(subject.PilotError,'pilot_metadata_changed'): driver.observe_once()

    def test_default_deny_before_any_stage_or_release_side_effect(self):
        driver,controller,ledger,transport,_=self.driver(qualifier=None)
        with self.assertRaisesRegex(subject.PilotError,'qualification_unverified'): driver.stage()
        with self.assertRaises(subject.PilotError): driver.release()
        self.assertEqual(transport.calls,[]); self.assertEqual(ledger.attempts(controller.campaign_id),[])

    def test_partial_ready_fleet_never_opens_barrier(self):
        driver,controller,ledger,transport,_=self.driver(); transport.fail_prepare.add('assistant-06')
        self.assertEqual(driver.stage()['ready'],49)
        with self.assertRaisesRegex(subject.PilotError,'exact_fifty_ready_required'): driver.release()
        self.assertFalse(any(x[0]=='barrier' for x in transport.calls))
        self.assertFalse(any(r['release_authorized'] for r in ledger.attempts(controller.campaign_id)))

    def test_exact_fleet_releases_once_and_observer_never_replays(self):
        driver,controller,ledger,transport,_=self.driver(); self.assertEqual(driver.stage()['ready'],50)
        released=driver.release(); self.assertEqual(released['released_permissions'],50)
        with self.assertRaises(subject.PilotError): driver.release()
        mutations=[x for x in transport.calls if x[0] in ('allocate','prepare','barrier')]
        for attempt in ledger.rows: transport.observe_run(attempt)
        subject.NaturalPilot(controller,FakeGrader()).observe_once()
        self.assertEqual(mutations,[x for x in transport.calls if x[0] in ('allocate','prepare','barrier')])

    def test_inner_release_guard_detects_readiness_changed_after_driver_precheck(self):
        driver,controller,ledger,transport,_=self.driver(); driver.stage()
        original=controller.release_all
        def changed():
            row=next(iter(ledger.rows.values())); row['baseline_ack']=None
            return original()
        controller.release_all=changed
        with self.assertRaises(subject.PilotError): driver.release()
        self.assertFalse(any(x[0]=='barrier' for x in transport.calls))

    def test_bad_ready_task_binding_or_already_released_is_rejected(self):
        driver,controller,ledger,transport,_=self.driver(); driver.stage()
        row=next(iter(ledger.rows.values())); original=copy.deepcopy(row)
        for key,value in [('task_id','not-selected'),('input_sha256','0'*64),('release_authorized',True),('holds_capacity',False)]:
            with self.subTest(key=key):
                row.clear();row.update(copy.deepcopy(original));row[key]=value
                with self.assertRaises(subject.PilotError):driver.release()
        self.assertFalse(any(x[0]=='barrier' for x in transport.calls))

    def test_observer_grades_completed_only_and_keeps_native_judges_queued(self):
        driver,controller,ledger,transport,grader=self.driver(); driver.stage();driver.release();transport.stop=True
        chosen={}
        for row in ledger.rows.values():
            if row['task_id'] in ('gpqa-01','hle-01','browsecomp-01','assistant-01'):
                transport.observe_run(row['id'],finished=True);chosen[row['id']]=row['task_id']
            else:transport.observe_run(row['id'])
        grader.tasks=chosen
        observations=driver.observe_once(public_run=self.repo)
        self.assertEqual(set(grader.calls),set(chosen))
        for attempt,slot in chosen.items():
            expected='queued' if slot.startswith(('hle','browsecomp')) else 'available'
            self.assertEqual(observations[slot]['grade']['status'],expected)
            emitted=(self.repo/'observations'/(slot+'.json')).read_bytes()
            self.assertNotIn(b'PRIVATE_',emitted);self.assertNotIn(b'evidence',emitted)
        self.assertFalse((self.repo/'public-status.json').exists())
        driver.export_observations(self.repo)
        self.assertEqual(json.loads((self.repo/'observations/gpqa-01.json').read_bytes())['grade']['status'],'available')
        calls=list(grader.calls);driver.observe_once();self.assertEqual(grader.calls,calls)

    def test_grader_error_or_invalid_score_does_not_erase_completion_or_invent_zero(self):
        driver,controller,ledger,transport,grader=self.driver();driver.stage();driver.release();transport.stop=True
        selected=[r for r in ledger.rows.values() if r['task_id'] in ('gpqa-01','assistant-01')]
        grader.tasks={r['id']:r['task_id'] for r in selected};grader.fail.add(selected[0]['id']);grader.bad.add(selected[1]['id'])
        for row in ledger.rows.values(): transport.observe_run(row['id'],finished=row in selected)
        output=driver.observe_once()
        for row in selected:
            obs=output[row['task_id']];self.assertTrue(obs['stop_verified']);self.assertEqual(obs['grade'],{'status':'unavailable'})
        self.assertNotIn('PRIVATE_',json.dumps(output))

    def test_missing_observation_preserves_released_claim_on_export(self):
        driver,controller,_,_,_=self.driver()
        previous={'attempt_id':str(uuid4()),'native_release_verified':True,
            'prompt_released_at':'2026-10-09T12:00:00+00:00','heartbeat_at':'2026-10-09T12:00:00+00:00',
            'stop_verified':False,'archive':{'status':'none'},'grade':{'status':'not_started'}}
        directory=self.repo/'observations';directory.mkdir();write(directory/'gpqa-01.json',previous)
        controller.observations=lambda:{}
        driver.export_observations(self.repo)
        value=json.loads((directory/'gpqa-01.json').read_bytes())
        self.assertEqual(value['attempt_id'],previous['attempt_id']);self.assertTrue(value['observation_invalid'])
        self.assertEqual(value['heartbeat_at'],previous['heartbeat_at'])

    def test_public_operational_fields_may_change_without_changing_frozen_roster(self):
        driver,_,_,_,_=self.driver();value=copy.deepcopy(self.public)
        value.update(blockers=[],launch_ready=True,compute_proposal={'hosts':3},status='preparing')
        write(self.roster,value)
        driver.export_observations(self.repo)
        self.assertEqual(len(list((self.repo/'observations').glob('*.json'))),50)
        self.assertEqual(self.frozen_roster.read_bytes(),canonical_json(self.frozen))
        driver.controller._verify_store()

    def test_public_roster_drift_and_symlink_destination_fail_without_writing(self):
        driver,_,_,_,_=self.driver();value=copy.deepcopy(self.public);value['tasks'][0]['label']='Changed';write(self.roster,value)
        with self.assertRaises(subject.PilotError):driver.export_observations(self.repo)
        self.assertFalse((self.repo/'observations').exists())
        write(self.roster,self.public);outside=self.repo/'outside';outside.mkdir();(self.repo/'observations').symlink_to(outside)
        with self.assertRaises(subject.PilotError):driver.export_observations(self.repo)
        self.assertEqual(list(outside.iterdir()),[])

    def test_public_task_contract_and_campaign_changes_are_rejected(self):
        driver,_,_,_,_=self.driver()
        for change in ('task','contract','campaign'):
            with self.subTest(change=change):
                value=copy.deepcopy(self.public)
                if change=='task':value['tasks'][0]['task_id']='gpqa-99'
                elif change=='contract':value['tasks'][0]['contract_sha256']='0'*64
                else:value['campaign_id']='another-campaign'
                write(self.roster,value)
                with self.assertRaises(subject.PilotError):driver.export_observations(self.repo)
        write(self.roster,self.public)

    def test_token_supplier_is_lazy_private_and_never_exposes_error_text(self):
        path=self.repo/'synthetic-token';supplier=subject.private_token_supplier(path)
        with self.assertRaisesRegex(subject.PilotError,'subscription_token_unavailable'):supplier()
        path.write_text('synthetic-token');path.chmod(0o600);self.assertEqual(supplier(),'synthetic-token')
        path.chmod(0o644)
        with self.assertRaisesRegex(subject.PilotError,'subscription_token_unavailable'):supplier()
        path.chmod(0o600);other=self.repo/'link';other.symlink_to(path)
        with self.assertRaises(subject.PilotError):subject.private_token_supplier(other)()

    def test_observe_loop_has_no_dispatch_and_stops_without_extra_iteration(self):
        driver,_,_,transport,_=self.driver();event=threading.Event();calls=[]
        def observe(**kwargs):calls.append(kwargs);event.set();return {}
        driver.observe_once=observe;driver.observe(event,interval_seconds=.01)
        self.assertEqual(len(calls),1);self.assertEqual(transport.calls,[])


if __name__=='__main__':unittest.main()
