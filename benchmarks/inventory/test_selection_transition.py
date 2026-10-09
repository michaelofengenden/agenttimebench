"""A roster change may retire only the exact identities in its saved decision."""
import copy
import hashlib
import json
import tempfile
from pathlib import Path
from unittest import mock
import unittest

import evidence_checks


class SelectionTransitionTests(unittest.TestCase):
    def setUp(self):
        self.keep = {'slot_id': 'core-01', 'family_id': 'core', 'candidate_id': 'retained'}
        self.old = {'slot_id': 'tau-01', 'family_id': 'tau', 'candidate_id': 'old-task'}
        self.new = {'slot_id': 'automation-01', 'family_id': 'automation', 'candidate_id': 'new-task'}
        self.change = {'decision_source': 'user', 'before': [dict(self.keep), dict(self.old)],
                       'removed': [dict(self.old)], 'added': [dict(self.new)]}

    def check(self, rows, change=None):
        self.assertTrue(hasattr(evidence_checks, 'check_selection_transition'),
                        'selection transitions need identity checks before retired updates can be skipped')
        return evidence_checks.check_selection_transition(rows, change or self.change)

    def test_explicit_replacement_preserves_other_slots(self):
        self.assertEqual(self.check([self.keep, self.new]), {'tau-01': 'old-task'})

    def test_cannot_silently_change_a_retained_identity(self):
        changed = {**self.keep, 'candidate_id': 'unreviewed-other-task'}
        with self.assertRaises(ValueError):
            self.check([changed, self.new])

    def test_removed_slot_must_have_existed(self):
        change = copy.deepcopy(self.change)
        change['removed'][0]['candidate_id'] = 'different-task'
        with self.assertRaises(ValueError):
            self.check([self.keep, self.new], change)

    def test_resurrecting_retired_slot_is_rejected(self):
        with self.assertRaises(ValueError):
            self.check([self.keep, self.old, self.new])

    def test_ambiguous_duplicate_slot_is_rejected(self):
        change = copy.deepcopy(self.change)
        change['added'].append({**self.new, 'candidate_id': 'another-task'})
        with self.assertRaises(ValueError):
            self.check([self.keep, self.new], change)

    def test_retirement_requires_recorded_user_decision(self):
        change = copy.deepcopy(self.change)
        change['decision_source'] = 'automatic'
        with self.assertRaises(ValueError):
            self.check([self.keep, self.new], change)


class SelectionHistoryTests(unittest.TestCase):
    def setUp(self):
        SelectionTransitionTests.setUp(self)
        self.extra = {'slot_id': 'automation-02', 'family_id': 'automation', 'candidate_id': 'second-task'}
        self.second = {'decision_source': 'user', 'before': [self.keep, self.new],
                       'removed': [], 'added': [self.extra]}

    def history(self, rows, changes):
        self.assertTrue(hasattr(evidence_checks, 'check_selection_history'),
                        'multiple roster changes must be checked as one connected history')
        return evidence_checks.check_selection_history(rows, changes, origin_rows=[self.keep, self.old])

    def test_extension_preserves_the_first_replacement_receipt(self):
        original = copy.deepcopy(self.change)
        self.assertEqual(self.history([self.keep, self.new, self.extra], [self.change, self.second]),
                         {'tau-01': 'old-task'})
        self.assertEqual(self.change, original)

    def test_missing_prior_transition_is_rejected(self):
        bad = copy.deepcopy(self.second)
        bad['before'][0]['candidate_id'] = 'silently-replaced'
        with self.assertRaises(ValueError):
            self.history([bad['before'][0], self.new, self.extra], [self.change, bad])

    def test_past_retired_slot_cannot_be_reused_in_a_later_step(self):
        bad = copy.deepcopy(self.second)
        bad['added'] = [{**self.old, 'candidate_id': 'new-occupant'}]
        with self.assertRaises(ValueError):
            self.history([self.keep, self.new, bad['added'][0]], [self.change, bad])

    def test_out_of_order_history_is_rejected(self):
        with self.assertRaises(ValueError):
            self.history([self.keep, self.new, self.extra], [self.second, self.change])

    def test_final_inventory_must_match_last_transition(self):
        with self.assertRaises(ValueError):
            self.history([self.keep, self.new], [self.change, self.second])

    def test_retirements_accumulate_across_transitions(self):
        second = copy.deepcopy(self.second)
        second['removed'] = [self.new]
        self.assertEqual(self.history([self.keep, self.extra], [self.change, second]),
                         {'tau-01': 'old-task', 'automation-01': 'new-task'})

    def test_omitting_first_receipt_is_rejected(self):
        with self.assertRaises(ValueError):
            self.history([self.keep, self.new, self.extra], [self.second])

    def test_omitted_origin_cannot_hide_a_retired_slot_reuse(self):
        bad = copy.deepcopy(self.second)
        bad['added'] = [self.old]
        with self.assertRaises(ValueError):
            self.history([self.keep, self.new, self.old], [bad])

    def test_single_transition_keeps_existing_behavior(self):
        self.assertEqual(self.history([self.keep, self.new], [self.change]), {'tau-01': 'old-task'})


class AnchoredHistoryLoaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.relative = 'selection-changes/2026-10-05-automationbench.json'
        self.origin = self.root / self.relative
        self.origin.parent.mkdir()
        self.origin.write_text(json.dumps({'before': [], 'removed': [], 'added': [], 'decision_source': 'user'}))
        self.manifest = self.root / 'slot-identities.json'
        self.manifest.write_text(json.dumps({'selection_changes': [self.relative]}))

    def read(self):
        # The test fixture replaces the independently pinned real receipt bytes.
        expected = hashlib.sha256(self.origin.read_bytes()).hexdigest()
        with mock.patch.object(evidence_checks, 'SELECTION_ORIGIN_SHA256', expected, create=True):
            return evidence_checks.read_selection_history(self.root)

    def test_explicit_empty_history_cannot_skip_existing_origin(self):
        self.manifest.write_text(json.dumps({'selection_changes': []}))
        with self.assertRaises(ValueError):
            self.read()

    def test_omitted_origin_reference_is_rejected(self):
        second = 'selection-changes/second.json'
        (self.root / second).write_text('{}')
        self.manifest.write_text(json.dumps({'selection_changes': [second]}))
        with self.assertRaises(ValueError):
            self.read()

    def test_origin_bytes_are_bound_independently(self):
        with mock.patch.object(evidence_checks, 'SELECTION_ORIGIN_SHA256', '0' * 64, create=True):
            with self.assertRaises(ValueError):
                evidence_checks.read_selection_history(self.root)

    def test_extension_binds_predecessor_receipt(self):
        second = 'selection-changes/second.json'
        (self.root / second).write_text(json.dumps({'previous_change': self.relative, 'previous_change_sha256': '0' * 64}))
        self.manifest.write_text(json.dumps({'selection_changes': [self.relative, second]}))
        with self.assertRaises(ValueError):
            self.read()


if __name__ == '__main__':
    unittest.main()
