import copy
import unittest
from agenttime.pilot_status import project_status
from agenttime import pilot_status


class PilotStatusTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {
            'campaign_id': 'synthetic-status',
            'tasks': [{'task_id': 'gpqa-01', 'label': 'Question 1', 'benchmark': 'GPQA Diamond'}],
            'target_concurrency': 50,
            'blockers': [{'code': 'worker_unqualified', 'message': 'Worker qualification pending'}],
        }
        self.now = '2026-10-09T12:00:40Z'

    def test_worker_alive_does_not_count_as_native_execution(self):
        result = project_status(self.manifest, {'gpqa-01': {'worker_alive': True}}, self.now)
        self.assertEqual(result['counts']['active_native'], 0)
        self.assertEqual(result['tasks'][0]['state'], 'preparing')
        self.assertIsNone(result['tasks'][0]['timing']['runtime_seconds'])

    def test_native_release_requires_observation_and_heartbeat(self):
        obs = {'attempt_id': 'a1', 'prompt_released_at': '2026-10-09T12:00:00Z',
               'heartbeat_at': '2026-10-09T12:00:38Z', 'native_release_verified': True,
               'elapsed_seconds': 38.0}
        result = project_status(self.manifest, {'gpqa-01': obs}, self.now)
        self.assertEqual(result['counts']['active_native'], 1)
        self.assertEqual(result['tasks'][0]['state'], 'running')
        self.assertEqual(result['tasks'][0]['timing']['elapsed_seconds'], 38.0)

    def test_drained_work_is_collecting_not_active_and_keeps_capacity(self):
        obs = {'attempt_id': 'a1', 'native_release_verified': True,
               'prompt_released_at': '2026-10-09T12:00:00Z',
               'native_terminal_at': '2026-10-09T12:00:30Z',
               'owned_work_drained_at': '2026-10-09T12:00:31Z',
               'heartbeat_at': '2026-10-09T12:00:38Z', 'stop_verified': False}
        result = project_status(self.manifest, {'gpqa-01': obs}, self.now)
        self.assertEqual(result['tasks'][0]['state'], 'collecting')
        self.assertEqual(result['counts']['active_native'], 0)
        self.assertEqual(result['counts']['capacity_held'], 1)
        self.assertEqual(result['campaign']['phase'], 'collecting')
        self.assertIsNone(result['tasks'][0]['timing']['runtime_seconds'])
        obs['heartbeat_at'] = '2026-10-09T12:00:01Z'
        self.assertEqual(project_status(self.manifest, {'gpqa-01': obs}, self.now)
                         ['tasks'][0]['state'], 'unknown')

    def test_stale_released_worker_is_unknown_and_keeps_reservation(self):
        obs = {'attempt_id': 'a1', 'prompt_released_at': '2026-10-09T12:00:00Z',
               'heartbeat_at': '2026-10-09T12:00:01Z', 'native_release_verified': True,
               'elapsed_seconds': 1.0}
        result = project_status(self.manifest, {'gpqa-01': obs}, self.now)
        self.assertEqual(result['tasks'][0]['state'], 'unknown')
        self.assertEqual(result['counts']['unknown'], 1)
        self.assertEqual(result['counts']['active_native'], 0)
        self.assertEqual(result['counts']['capacity_held'], 1)
        self.assertEqual(result['tasks'][0]['timing']['elapsed_seconds'], 1.0)

    def test_process_exit_without_native_terminal_never_means_finished(self):
        obs = {'attempt_id': 'a1', 'native_release_verified': True,
               'prompt_released_at': '2026-10-09T12:00:00Z', 'stop_verified': True,
               'worker_alive': False, 'heartbeat_at': '2026-10-09T12:00:38Z'}
        result = project_status(self.manifest, {'gpqa-01': obs}, self.now)
        self.assertEqual(result['tasks'][0]['state'], 'blocked')
        self.assertIsNone(result['tasks'][0]['timing']['runtime_seconds'])

    def test_failed_stopped_attempt_keeps_invalid_timing_visible(self):
        obs = {'attempt_id': 'a1', 'native_release_verified': True,
               'prompt_released_at': '2026-10-09T12:00:00Z', 'stop_verified': True,
               'owned_work_drained_at': '2026-10-09T12:00:30Z',
               'timing': {'status': 'invalid', 'runtime_seconds': None}}
        result = project_status(self.manifest, {'gpqa-01': obs}, self.now)
        self.assertEqual(result['tasks'][0]['state'], 'blocked')
        self.assertEqual(result['tasks'][0]['timing']['status'], 'invalid')
        self.assertIsNone(result['tasks'][0]['timing']['runtime_seconds'])

    def test_staged_worker_does_not_claim_qualification(self):
        obs = {'attempt_id': 'a1', 'admission_ready': True}
        result = project_status(self.manifest, {'gpqa-01': obs}, self.now)
        self.assertEqual(result['tasks'][0]['state'], 'ready')
        self.assertEqual(result['tasks'][0]['detail'],
                         'Staged at the release barrier; final launch checks remain.')

    def test_corrupt_observation_cannot_erase_previous_release(self):
        obs = {'attempt_id': 'a1', 'native_release_verified': True,
               'prompt_released_at': '2026-10-09T12:00:00Z',
               'heartbeat_at': '2026-10-09T12:00:38Z', 'observation_invalid': True}
        result = project_status(self.manifest, {'gpqa-01': obs}, self.now)
        self.assertEqual(result['tasks'][0]['state'], 'unknown')
        self.assertEqual(result['counts']['capacity_held'], 1)

    def test_finished_grade_and_archive_are_independent(self):
        obs = {'attempt_id': 'a1', 'native_release_verified': True,
               'prompt_released_at': '2026-10-09T12:00:00Z', 'stop_verified': True,
               'native_terminal_at': '2026-10-09T12:00:30Z',
               'owned_work_drained_at': '2026-10-09T12:00:30Z',
               'heartbeat_at': '2026-10-09T12:00:30Z',
               'timing': {'status': 'valid', 'runtime_seconds': 29.2, 'reason': None},
               'grade': {'status': 'failed', 'value': None, 'scale': None}}
        result = project_status(self.manifest, {'gpqa-01': obs}, self.now)
        row = result['tasks'][0]
        self.assertEqual(row['state'], 'finished')
        self.assertEqual(row['timing']['runtime_seconds'], 29.2)
        self.assertIsNone(row['grade']['value'])
        self.assertEqual(row['archive']['status'], 'none')
        self.assertEqual(result['counts']['capacity_held'], 0)

    def test_untrusted_payloads_and_forecasts_are_never_projected(self):
        obs = {'prompt': 'PRIVATE', 'answer': 'SECRET', 'forecast_minutes': 480,
               'grade': {'status': 'failed', 'value': 1, 'scale': 'fraction', 'answer': 'SECRET'}}
        result = project_status(self.manifest, {'gpqa-01': obs}, self.now)
        self.assertNotIn('SECRET', str(result))
        self.assertNotIn('PRIVATE', str(result))
        self.assertNotIn('forecast', str(result))
        self.assertIsNone(result['tasks'][0]['grade']['value'])

    def test_projection_does_not_mutate_sources(self):
        original = copy.deepcopy(self.manifest)
        project_status(self.manifest, {}, self.now)
        self.assertEqual(original, self.manifest)

    def test_invalid_replacement_preserves_known_claim_and_release(self):
        previous = {'attempt_id': 'a1', 'native_release_verified': True,
                    'prompt_released_at': '2026-10-09T12:00:00Z',
                    'heartbeat_at': '2026-10-09T12:00:38Z'}
        for replacement in ([], {}, {'attempt_id': 'a2'}, {'attempt_id': 'a1', 'blocked': True}):
            with self.subTest(replacement=replacement):
                self.assertTrue(hasattr(pilot_status, 'merge_observation'))
                observed = pilot_status.merge_observation(previous, replacement)
                result = project_status(self.manifest, {'gpqa-01': observed}, self.now)
                self.assertEqual(result['counts']['unknown'], 1)
                self.assertEqual(result['counts']['capacity_held'], 1)

    def test_available_without_numeric_grade_does_not_complete_campaign(self):
        obs = {'attempt_id': 'a1', 'native_release_verified': True,
               'prompt_released_at': '2026-10-09T12:00:00Z', 'stop_verified': True,
               'native_terminal_at': '2026-10-09T12:00:30Z',
               'owned_work_drained_at': '2026-10-09T12:00:30Z',
               'grade': {'status': 'available', 'value': None, 'scale': 'fraction'},
               'archive': {'status': 'acknowledged', 'restore_status': 'verified'}}
        result = project_status(self.manifest, {'gpqa-01': obs}, self.now)
        self.assertEqual(result['campaign']['phase'], 'collecting')
        self.assertEqual(result['tasks'][0]['grade']['status'], 'unavailable')

    def test_invalid_observation_revokes_ready_display_without_losing_claim(self):
        previous = {'attempt_id': 'a1', 'admission_ready': True}
        observed = pilot_status.merge_observation(previous, {})
        result = project_status(self.manifest, {'gpqa-01': observed}, self.now)
        self.assertEqual(result['tasks'][0]['state'], 'blocked')
        self.assertEqual(result['counts']['capacity_held'], 1)


if __name__ == '__main__':
    unittest.main()
