"""Real transactions must prevent duplicate execution and premature admission."""
from concurrent.futures import ThreadPoolExecutor
import importlib
import unittest

from db_support import DatabaseTestCase


def manifest(agent='fixture-a', count=2):
    return {'schema_version': 1, 'executor': 'fixture-v1', 'agent_id': agent,
            'arm': 'natural', 'tasks': [{'id': f'task-{i}'} for i in range(count)]}


class LedgerTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        try:
            module = importlib.import_module('agenttime.ledger')
        except ModuleNotFoundError:
            self.fail('Durable PostgreSQL ledger is not implemented')
        self.Ledger = module.Ledger
        self.ledger = self.Ledger(self.dsn)
        self.ledger.initialize(capacity=2)
        self.ledger.create_campaign('a', manifest())

    def test_competing_controllers_reserve_each_task_once(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.Ledger(self.dsn).reserve('a'), range(8)))
        attempts = [r for r in results if r is not None]
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len({a['id'] for a in attempts}), 2)
        self.assertEqual({a['task_id'] for a in attempts}, {'task-0', 'task-1'})
        self.assertEqual(len(self.ledger.attempts('a')), 2)

    def test_worker_claim_is_consumed_once_even_for_same_owner(self):
        attempt = self.ledger.reserve('a')
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.Ledger(self.dsn).claim(attempt['id'], 'same-worker'), range(8)))
        self.assertEqual(results.count(True), 1)
        self.assertFalse(self.ledger.claim(attempt['id'], 'other-worker'))

    def test_unknown_and_quarantined_work_still_counts_against_capacity(self):
        self.ledger.create_campaign('b', manifest(count=3))
        first = self.ledger.reserve('a')
        self.ledger.quarantine(first['id'], 'lost contact')
        self.assertIsNotNone(self.ledger.reserve('b'))
        self.assertIsNone(self.ledger.reserve('b'))
        self.assertFalse(self.ledger.claim(first['id'], 'new-worker'))

    def test_one_agent_at_a_time_including_other_campaigns(self):
        self.ledger.create_campaign('b', manifest(agent='fixture-b'))
        first = self.ledger.reserve('a')
        self.assertIsNone(self.ledger.reserve('b'))
        self.assertTrue(self.ledger.claim(first['id'], 'worker'))
        self.ledger.finish(first['id'], 'worker', {'score': None})
        self.assertIsNotNone(self.ledger.reserve('b'))

    def test_only_original_owner_can_finish_and_release_capacity(self):
        a = self.ledger.reserve('a')
        self.ledger.claim(a['id'], 'worker')
        with self.assertRaises(ValueError):
            self.ledger.finish(a['id'], 'impostor', {'score': 1})
        self.assertTrue(self.ledger.attempts('a')[0]['holds_capacity'])
        self.ledger.finish(a['id'], 'worker', {'score': None})
        self.ledger.finish(a['id'], 'worker', {'score': None})
        with self.assertRaises(ValueError):
            self.ledger.finish(a['id'], 'worker', {'score': 1})
        self.assertFalse(self.ledger.attempts('a')[0]['holds_capacity'])
        self.assertFalse(self.ledger.claim(a['id'], 'worker'))

    def test_manifest_cannot_change_on_restart(self):
        self.ledger.create_campaign('a', manifest())
        with self.assertRaises(ValueError):
            self.ledger.create_campaign('a', manifest(count=3))
        self.assertEqual(len(self.ledger.campaign('a')['manifest']['tasks']), 2)

    def test_shared_pause_persists_without_releasing_owned_work(self):
        a = self.ledger.reserve('a')
        self.ledger.pause('evidence corruption')
        self.assertIsNone(self.Ledger(self.dsn).reserve('a'))
        self.assertTrue(self.ledger.attempts('a')[0]['holds_capacity'])
        self.assertEqual(self.ledger.gate()['paused_reason'], 'evidence corruption')
        self.assertTrue(self.ledger.claim(a['id'], 'already-reserved-worker') is False)

    def test_capacity_reconfiguration_is_rejected_and_study_manifest_refused(self):
        with self.assertRaises(ValueError):
            self.ledger.initialize(capacity=220)
        with self.assertRaises(ValueError):
            self.ledger.create_campaign('real', dict(manifest(), executor='harbor'))
        with self.assertRaises(ValueError):
            self.ledger.create_campaign('timed', dict(manifest(), arm='short'))

    def test_duplicate_task_identity_cannot_create_extra_attempt(self):
        with self.assertRaises(ValueError):
            self.ledger.create_campaign('duplicate', dict(manifest(), tasks=[{'id':'same'}, {'id':'same'}]))


if __name__ == '__main__':
    unittest.main()

class LedgerIntegrityTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        from agenttime.ledger import Ledger
        self.ledger = Ledger(self.dsn)
        self.ledger.initialize(2)
        self.original = manifest()
        self.ledger.create_campaign('a', self.original)

    def test_corrupt_manifest_pauses_and_cannot_reserve_or_reopen(self):
        from psycopg.types.json import Jsonb
        with self.ledger.transaction() as conn:
            conn.execute('UPDATE agenttime_fixture.campaigns SET manifest=%s WHERE id=%s',
                         (Jsonb(dict(self.original, tasks=[{'id':'changed'}])), 'a'))
        for operation in (lambda: self.ledger.campaign('a'), lambda: self.ledger.reserve('a'),
                          lambda: self.ledger.create_campaign('a', self.original)):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                operation()
        self.assertIsNotNone(self.ledger.gate()['paused_reason'])
        self.assertEqual(self.ledger.attempts('a'), [])

    def test_denormalized_agent_identity_must_match_manifest(self):
        with self.ledger.transaction() as conn:
            conn.execute("UPDATE agenttime_fixture.campaigns SET agent_id='different'")
        with self.assertRaises(ValueError):
            self.ledger.reserve('a')
        self.assertIsNotNone(self.ledger.gate()['paused_reason'])
        self.assertEqual(self.ledger.attempts('a'), [])

    def test_fresh_admission_check_changes_after_work_drains(self):
        self.assertTrue(hasattr(self.ledger, 'admission_block_reason'), 'Fresh admission check is missing')
        self.assertIsNone(self.ledger.admission_block_reason('a'))
        first, second = self.ledger.reserve('a'), self.ledger.reserve('a')
        self.assertEqual(self.ledger.admission_block_reason('a'), 'capacity')
        self.ledger.claim(first['id'], 'w')
        self.ledger.finish(first['id'], 'w', {})
        self.assertIsNone(self.ledger.admission_block_reason('a'))
        self.ledger.create_campaign('b', manifest('another'))
        self.assertEqual(self.ledger.admission_block_reason('b'), 'another_agent')

    def test_corrupted_active_campaign_cannot_hide_an_agent_overlap(self):
        self.ledger.create_campaign('b', manifest('another'))
        self.ledger.reserve('a')
        with self.ledger.transaction() as conn:
            conn.execute("UPDATE agenttime_fixture.campaigns SET agent_id='another' WHERE id='a'")
        with self.assertRaises(ValueError):
            self.ledger.reserve('b')
        self.assertIsNotNone(self.ledger.gate()['paused_reason'])
        self.assertEqual(self.ledger.attempts('b'), [])
