"""Approved replacements must be explicit, fresh, and identity preserving."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import build_inventory
import verify_inventory


class ReplacementRowsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.evidence = Path(self.temp.name)
        self.keep = {'slot_id': 'ale-01', 'family_id': 'ale', 'candidate_id': 'keep',
                     'source_pin': 'retained-pin', 'selection_status': 'earlier_candidate'}
        self.old = {'slot_id': 'ale-02', 'family_id': 'ale', 'candidate_id': 'retire',
                    'source_pin': 'old-pin', 'selection_status': 'earlier_candidate'}
        self.new = {'slot_id': 'ale-13', 'family_id': 'ale', 'candidate_id': 'replacement',
                    'selection_status': 'approved_replacement', 'runtime_qualified': False,
                    'admitted': False, 'source_pin': 'new-pin'}
        self.change = {'decision_source': 'user', 'before': [self.keep, self.old],
                       'removed': [self.old], 'added': [self.new]}

    def run_replacement(self, replacement=None, change=None, rows=None):
        bundle = {'schema_version': 1, 'rows': [replacement or self.new]}
        payload = (json.dumps(bundle) + '\n').encode()
        (self.evidence / 'new-rows.json').write_bytes(payload)
        decision = copy.deepcopy(change or self.change)
        decision['replacement_rows'] = {'path': 'new-rows.json',
                                        'sha256': hashlib.sha256(payload).hexdigest()}
        self.assertTrue(hasattr(build_inventory, 'apply_recorded_replacements'),
                        'builder must apply only receipt-bound replacement rows')
        return build_inventory.apply_recorded_replacements(
            rows or [self.keep, self.old], [decision], self.evidence)

    def test_replacement_keeps_other_row_bytes_and_does_not_mutate_inputs(self):
        before = copy.deepcopy([self.keep, self.old])
        actual = self.run_replacement()
        self.assertEqual([r['slot_id'] for r in actual], ['ale-01', 'ale-13'])
        self.assertEqual(actual[0], before[0])
        self.assertEqual([self.keep, self.old], before)

    def test_changed_retained_identity_is_rejected(self):
        with self.assertRaises(ValueError):
            self.run_replacement(rows=[{**self.keep, 'candidate_id': 'changed'}, self.old])

    def test_reusing_retired_slot_is_rejected(self):
        new = {**self.new, 'slot_id': 'ale-02'}
        change = {**self.change, 'added': [new]}
        with self.assertRaises(ValueError):
            self.run_replacement(new, change)

    def test_duplicate_candidate_on_fresh_slot_is_rejected(self):
        new = {**self.new, 'candidate_id': 'keep'}
        change = {**self.change, 'added': [new]}
        with self.assertRaises(ValueError):
            self.run_replacement(new, change)

    def test_row_not_recorded_in_decision_is_rejected(self):
        with self.assertRaises(ValueError):
            self.run_replacement({**self.new, 'candidate_id': 'unrecorded'})

    def test_replacement_cannot_claim_runtime_admission(self):
        with self.assertRaises(ValueError):
            self.run_replacement({**self.new, 'admitted': True})

    def test_replacement_bytes_must_match_receipt_hash(self):
        (self.evidence / 'new-rows.json').write_text(json.dumps({'rows': [self.new]}))
        decision = {**self.change, 'replacement_rows': {'path': 'new-rows.json', 'sha256': '0' * 64}}
        self.assertTrue(hasattr(build_inventory, 'apply_recorded_replacements'))
        with self.assertRaises(ValueError):
            build_inventory.apply_recorded_replacements([self.keep, self.old], [decision], self.evidence)


class HistoricalCandidateTests(unittest.TestCase):
    def setUp(self):
        self.origin = [{'slot_id': f'ale-{i:02}', 'family_id': 'ale', 'candidate_id': f'old-{i}',
                        'selection_status': 'earlier_candidate'} for i in range(1, 101)]
        self.base = {'families': [{'family_id': 'ale', 'known_candidates':
                                  [{'task_id': f'old-{i}'} for i in range(1, 101)]}]}
        self.added = [{'slot_id': f'ale-{i:02}', 'family_id': 'ale', 'candidate_id': f'new-{i}',
                       'selection_status': 'approved_replacement'} for i in range(101, 106)]
        self.change = {'decision_source': 'user', 'before': self.origin,
                       'removed': self.origin[:5], 'added': self.added}
        self.active = self.origin[5:] + self.added

    def check(self, rows=None, history=None):
        self.assertTrue(hasattr(verify_inventory, 'check_historical_candidates'),
                        'original 100 must be verified before recorded retirements are applied')
        return verify_inventory.check_historical_candidates(
            rows or self.active, self.base, self.origin, history if history is not None else [self.change])

    def test_historical_hundred_becomes_ninety_five_after_recorded_retirements(self):
        self.assertEqual(self.check(), {'historical': 100, 'active': 95, 'retired': 5})

    def test_changed_remaining_original_is_rejected(self):
        with self.assertRaises(ValueError):
            self.check(rows=[{**self.active[0], 'candidate_id': 'changed'}] + self.active[1:])

    def test_removal_without_recorded_decision_is_rejected(self):
        with self.assertRaises(ValueError):
            self.check(history=[])

    def test_original_selection_map_cannot_lose_one_of_hundred(self):
        self.base['families'][0]['known_candidates'].pop()
        with self.assertRaises(ValueError):
            self.check()


if __name__ == '__main__':
    unittest.main()
