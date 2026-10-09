"""Regression checks for false source-presence and remapped task evidence."""
from pathlib import Path
import copy
import hashlib
import json
import tempfile
import unittest
from unittest import mock
import evidence_checks

from verify_inventory import verify


class InventoryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.folder = self.repo / 'benchmarks/inventory'
        (self.folder / 'evidence').mkdir(parents=True)
        (self.repo / 'configs').mkdir()
        self.cache = self.repo / 'benchmarks/cache'
        self.cache.mkdir()
        body = b'complete source body\n'
        (self.cache / 'source.txt').write_bytes(body)
        self.file = {'path': 'benchmarks/cache/source.txt', 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
        self.rows = []
        for n in range(220):
            ref = {'root': 'repository', 'exists': True, **self.file, 'visibility': 'preparation_only'}
            self.rows.append({'slot_id': f'f-{n:03}', 'family_id': 'f', 'candidate_id': f'task-{n}',
                'selection_status': 'earlier_candidate' if n < 100 else 'draft_for_review',
                'runtime_qualified': False, 'admitted': False, 'natural_prompt': {'status': 'needs_checking'},
                'components': {'prompt': {'status': 'present', 'evidence': [ref]},
                               'environment': {'status': 'needs_checking', 'evidence': []}}})
        self.lock = [{k:r[k] for k in ['slot_id','family_id','candidate_id']} for r in self.rows]
        self.files = [copy.deepcopy(self.file)]
        origin = self.folder / 'evidence' / evidence_checks.SELECTION_ORIGIN_PATH
        origin.parent.mkdir()
        origin.write_text(json.dumps({'decision_source': 'user', 'before': self.lock, 'removed': [], 'added': []}))
        pin = mock.patch.object(evidence_checks, 'SELECTION_ORIGIN_SHA256', hashlib.sha256(origin.read_bytes()).hexdigest())
        pin.start()
        self.addCleanup(pin.stop)

    def write(self):
        def save(path, data): path.write_text(json.dumps(data))
        save(self.repo/'configs/suite.json', {'families':[{'id':'f','count':220}]})
        save(self.folder/'evidence/selection-map.json', {'families':[{'family_id':'f','known_candidates':[{'task_id':f'task-{i}'} for i in range(100)]}]})
        save(self.folder/'evidence/slot-identities.json', {'rows':self.lock, 'selection_changes': [evidence_checks.SELECTION_ORIGIN_PATH]})
        save(self.folder/'evidence/source-manifest.json', {'files':self.files})
        save(self.folder/'tasks.json', {'rows':self.rows,'totals':{'selection':{'draft_for_review':120},'verified_cached_bytes':sum(f['bytes'] for f in self.files),'verified_cached_files':len(self.files)}})
        save(self.folder/'recommendations.json', {'rows':[{'slot_id':r['slot_id']} for r in self.rows if r['selection_status']=='draft_for_review']})

    def assert_rejected(self):
        self.write()
        with self.assertRaises((AssertionError, ValueError)):
            verify(self.repo)

    def test_complete_matching_file_is_accepted(self):
        self.write()
        self.assertEqual(verify(self.repo)['status'], 'passed_for_inventory_only')

    def test_present_component_rejects_one_missing_required_file(self):
        self.rows[0]['components']['prompt']['evidence'].append({'root':'repository','path':'benchmarks/cache/missing.txt','exists':False})
        self.assert_rejected()

    def test_present_component_rejects_empty_directory_as_evidence(self):
        (self.cache/'empty').mkdir()
        self.rows[0]['components']['prompt']['evidence']=[{'root':'repository','path':'benchmarks/cache/empty','exists':True}]
        self.assert_rejected()

    def test_component_hash_must_agree_with_verified_source_manifest(self):
        self.rows[0]['components']['prompt']['evidence'][0]['sha256']='0'*64
        self.assert_rejected()

    def test_pointer_stub_cannot_count_as_source_content(self):
        b=b'version https://git-lfs.github.com/spec/v1\noid sha256:'+b'a'*64+b'\nsize 2222\n'
        (self.cache/'source.txt').write_bytes(b)
        self.files[0].update(bytes=len(b),sha256=hashlib.sha256(b).hexdigest())
        for row in self.rows:row['components']['prompt']['evidence'][0].update(self.files[0])
        self.assert_rejected()

    def test_reordering_must_not_change_slot_identity(self):
        self.rows[-1]['slot_id'],self.rows[-2]['slot_id']=self.rows[-2]['slot_id'],self.rows[-1]['slot_id']
        self.assert_rejected()

    def test_unmanifested_file_cannot_be_present_evidence(self):
        p=self.cache/'uncounted.txt';p.write_text('source not in receipts')
        self.rows[0]['components']['prompt']['evidence']=[{'root':'repository','path':str(p.relative_to(self.repo)),'exists':True}]
        self.assert_rejected()

    def test_directory_with_an_unmanifested_file_is_rejected(self):
        (self.cache/'uncounted.txt').write_text('source not in receipts')
        self.rows[0]['components']['prompt']['evidence']=[{'root':'repository','path':'benchmarks/cache','exists':True}]
        self.assert_rejected()

    def add_link(self, target):
        link=self.cache/'link.txt';link.symlink_to(target)
        entry={'path':'benchmarks/cache/link.txt','type':'symlink','target':target,'bytes':len(target.encode()),'sha256':hashlib.sha256(target.encode()).hexdigest()}
        self.files.append(entry)
        self.rows[0]['components']['prompt']['evidence']=[{'root':'repository','exists':True,**entry}]

    def test_symlink_target_must_be_manifested(self):
        (self.cache/'untracked.txt').write_text('unverified target bytes')
        self.add_link('untracked.txt')
        self.assert_rejected()

    def test_symlink_to_pointer_stub_is_rejected(self):
        body=b'version https://git-lfs.github.com/spec/v1\noid sha256:'+b'a'*64+b'\nsize 2222\n'
        (self.cache/'pointer.txt').write_bytes(body)
        self.files.append({'path':'benchmarks/cache/pointer.txt','bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()})
        self.add_link('pointer.txt')
        self.assert_rejected()

    def test_symlink_to_verified_source_is_accepted(self):
        self.add_link('source.txt')
        self.write()
        self.assertEqual(verify(self.repo)['status'],'passed_for_inventory_only')

    def test_record_id_must_resolve_to_the_expected_record(self):
        body=json.dumps([{'id':'one','value':3}]).encode()
        (self.cache/'records.json').write_bytes(body)
        entry={'path':'benchmarks/cache/records.json','bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()}
        self.files.append(entry)
        self.rows[0]['components']['prompt']['evidence']=[{'root':'repository','exists':True,**entry,'record_id':'missing','record_sha256':'0'*64}]
        self.assert_rejected()

    def test_csv_record_hash_must_match_the_parsed_row(self):
        body=b'topic,value\nmath,3\n'
        (self.cache/'records.csv').write_bytes(body)
        entry={'path':'benchmarks/cache/records.csv','bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()}
        self.files.append(entry)
        self.rows[0]['components']['prompt']['evidence']=[{'root':'repository','exists':True,**entry,'data_row_index_zero_based':0,'record_sha256':'0'*64}]
        self.assert_rejected()

    def test_symlink_preserves_record_validation(self):
        body=json.dumps([{'id':'one','value':3}]).encode()
        (self.cache/'records.json').write_bytes(body)
        self.files.append({'path':'benchmarks/cache/records.json','bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()})
        self.add_link('records.json')
        self.rows[0]['components']['prompt']['evidence'][0].update(record_id='missing',record_sha256='0'*64)
        self.assert_rejected()


if __name__=='__main__':unittest.main()
