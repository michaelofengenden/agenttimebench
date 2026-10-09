"""Transactional admission for model-free fixtures; no lease-based replay."""
from contextlib import contextmanager
import hashlib
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .evidence import IntegrityError, canonical_json

# One lock serializes admission across every campaign in this database.
ADMISSION_LOCK = 871103001


def encoded(value):
    return canonical_json(value)


class Ledger:
    def __init__(self, dsn: str):
        self.dsn = dsn

    @contextmanager
    def transaction(self):
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5) as conn:
            yield conn

    def initialize(self, capacity: int = 4):
        if type(capacity) is not int or not 1 <= capacity <= 220:
            raise ValueError('Capacity must be an integer from 1 to 220')
        with self.transaction() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (ADMISSION_LOCK,))
            conn.execute('CREATE SCHEMA IF NOT EXISTS agenttime_fixture')
            conn.execute('''CREATE TABLE IF NOT EXISTS agenttime_fixture.gate (
                id integer PRIMARY KEY CHECK (id=1), schema_version integer NOT NULL CHECK(schema_version=1),
                capacity integer NOT NULL CHECK(capacity BETWEEN 1 AND 220), paused_reason text)''')
            conn.execute('''INSERT INTO agenttime_fixture.gate VALUES (1,1,%s,NULL)
                            ON CONFLICT(id) DO NOTHING''', (capacity,))
            gate = conn.execute('SELECT * FROM agenttime_fixture.gate WHERE id=1').fetchone()
            if gate['capacity'] != capacity or gate['schema_version'] != 1:
                raise ValueError('Ledger capacity/schema is immutable; use a separate fixture database')
            conn.execute('''CREATE TABLE IF NOT EXISTS agenttime_fixture.campaigns (
                id text PRIMARY KEY, agent_id text NOT NULL, manifest jsonb NOT NULL,
                manifest_sha256 text NOT NULL, created_at timestamptz NOT NULL DEFAULT now())''')
            conn.execute('''CREATE TABLE IF NOT EXISTS agenttime_fixture.attempts (
                id text PRIMARY KEY, campaign_id text NOT NULL REFERENCES agenttime_fixture.campaigns(id),
                task_id text NOT NULL, ordinal integer NOT NULL DEFAULT 0 CHECK(ordinal IN (0,1)),
                original_id text REFERENCES agenttime_fixture.attempts(id),
                state text NOT NULL CHECK(state IN ('reserved','active','quarantined','finished')),
                worker_id text, holds_capacity boolean NOT NULL DEFAULT true,
                reason text, report jsonb, created_at timestamptz NOT NULL DEFAULT now(),
                UNIQUE(campaign_id,task_id,ordinal),
                CHECK((ordinal=0 AND original_id IS NULL) OR (ordinal=1 AND original_id IS NOT NULL)),
                CHECK(holds_capacity = (state <> 'finished')),
                CHECK(state <> 'finished' OR (worker_id IS NOT NULL AND report IS NOT NULL)))''')

    def create_campaign(self, campaign_id: str, manifest: dict):
        if not isinstance(campaign_id, str) or not campaign_id:
            raise ValueError('Campaign ID is required')
        if (manifest.get('executor') not in {'fixture-v1', 'harbor-fixture-v1'} or manifest.get('arm') != 'natural'
                or type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1):
            raise ValueError('This ledger executes only explicitly supported model-free natural fixtures')
        if manifest['executor'] == 'harbor-fixture-v1':
            from .harbor_worker import validate_manifest
            validate_manifest(manifest)
        agent_id = manifest.get('agent_id')
        tasks = manifest.get('tasks')
        if not isinstance(agent_id, str) or not agent_id or not isinstance(tasks, list) or not tasks:
            raise ValueError('An agent identity and at least one task are required')
        ids = [t.get('id') if isinstance(t, dict) else None for t in tasks]
        if any(not isinstance(t, str) or not t for t in ids) or len(set(ids)) != len(ids):
            raise ValueError('Task identities must be nonempty and unique')
        digest = hashlib.sha256(encoded(manifest)).hexdigest()
        with self.transaction() as conn:
            conn.execute('''INSERT INTO agenttime_fixture.campaigns (id,agent_id,manifest,manifest_sha256)
                VALUES (%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING''',
                (campaign_id, agent_id, Jsonb(manifest), digest))
            row = self._verified_campaign(conn, campaign_id)
            if row['manifest_sha256'] != digest:
                raise ValueError('Campaign manifest differs from the persisted manifest')

    @staticmethod
    def _verified_campaign(conn, campaign_id):
        row = conn.execute('SELECT * FROM agenttime_fixture.campaigns WHERE id=%s', (campaign_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown fixture campaign')
        try:
            valid = (hashlib.sha256(encoded(row['manifest'])).hexdigest() == row['manifest_sha256']
                     and row['manifest']['agent_id'] == row['agent_id'])
        except (TypeError, ValueError, KeyError):
            valid = False
        if not valid:
            conn.execute("UPDATE agenttime_fixture.gate SET paused_reason=COALESCE(paused_reason,'persisted_manifest_integrity_failure') WHERE id=1")
            # Persist the pause before propagating the error out of this connection context.
            conn.commit()
            raise IntegrityError('Persisted manifest or agent identity disagrees with its digest')
        return row

    def campaign(self, campaign_id: str):
        with self.transaction() as conn:
            return self._verified_campaign(conn, campaign_id)

    @staticmethod
    def _admission_block(conn, campaign):
        gate = conn.execute('SELECT * FROM agenttime_fixture.gate WHERE id=1').fetchone()
        active = conn.execute("SELECT c.id AS campaign_id, c.agent_id FROM agenttime_fixture.attempts a JOIN agenttime_fixture.campaigns c ON c.id=a.campaign_id WHERE a.holds_capacity").fetchall()
        for identity in {a['campaign_id'] for a in active}:
            Ledger._verified_campaign(conn, identity)
        if gate['paused_reason'] is not None:
            return 'paused'
        if any(a['agent_id'] != campaign['agent_id'] for a in active):
            return 'another_agent'
        if len(active) >= gate['capacity']:
            return 'capacity'
        return None

    def admission_block_reason(self, campaign_id):
        with self.transaction() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (ADMISSION_LOCK,))
            campaign = self._verified_campaign(conn, campaign_id)
            return self._admission_block(conn, campaign)

    def gate(self):
        with self.transaction() as conn:
            return conn.execute('SELECT * FROM agenttime_fixture.gate WHERE id=1').fetchone()

    def reserve(self, campaign_id: str):
        """Commit one dispatch intent. None means no new work may be admitted."""
        with self.transaction() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (ADMISSION_LOCK,))
            campaign = self._verified_campaign(conn, campaign_id)
            if self._admission_block(conn, campaign) is not None:
                return None
            existing = {a['task_id'] for a in conn.execute(
                'SELECT task_id FROM agenttime_fixture.attempts WHERE campaign_id=%s', (campaign_id,))}
            task = next((t for t in campaign['manifest']['tasks'] if t['id'] not in existing), None)
            if task is None:
                return None
            return conn.execute('''INSERT INTO agenttime_fixture.attempts (id,campaign_id,task_id,state)
                VALUES (%s,%s,%s,'reserved') RETURNING *''',
                (str(uuid4()), campaign_id, task['id'])).fetchone()

    def claim(self, attempt_id: str, worker_id: str) -> bool:
        """A permanent single-use claim. Even an identical caller cannot claim twice."""
        if not isinstance(worker_id, str) or not worker_id:
            raise ValueError('Worker identity is required')
        with self.transaction() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (ADMISSION_LOCK,))
            if conn.execute('SELECT paused_reason FROM agenttime_fixture.gate WHERE id=1').fetchone()['paused_reason'] is not None:
                return False
            row = conn.execute('''UPDATE agenttime_fixture.attempts SET worker_id=%s, state='active'
                WHERE id=%s AND worker_id IS NULL AND state='reserved' RETURNING id''',
                (worker_id, attempt_id)).fetchone()
            return row is not None

    def attempt(self, attempt_id: str):
        with self.transaction() as conn:
            row = conn.execute('SELECT * FROM agenttime_fixture.attempts WHERE id=%s', (attempt_id,)).fetchone()
            if row is None:
                raise ValueError('Unknown fixture attempt')
            return row

    def attempts(self, campaign_id: str):
        with self.transaction() as conn:
            return conn.execute('''SELECT * FROM agenttime_fixture.attempts
                WHERE campaign_id=%s ORDER BY created_at,id''', (campaign_id,)).fetchall()

    def finish(self, attempt_id: str, worker_id: str, report: dict):
        """Only the original owner may confirm its owned work drained and record a result."""
        encoded(report)
        with self.transaction() as conn:
            row = conn.execute('SELECT * FROM agenttime_fixture.attempts WHERE id=%s FOR UPDATE', (attempt_id,)).fetchone()
            if row is None or row['worker_id'] != worker_id or row['state'] == 'reserved':
                raise ValueError('Finish does not belong to the original worker')
            if row['state'] == 'finished':
                if encoded(row['report']) != encoded(report):
                    raise ValueError('Conflicting final report')
                return
            conn.execute('''UPDATE agenttime_fixture.attempts SET state='finished', holds_capacity=false, report=%s
                WHERE id=%s''', (Jsonb(report), attempt_id))

    def quarantine(self, attempt_id: str, reason: str):
        with self.transaction() as conn:
            conn.execute('''UPDATE agenttime_fixture.attempts SET state='quarantined', reason=%s
                WHERE id=%s AND state <> 'finished' ''', (reason, attempt_id))

    def pause(self, reason: str):
        if not reason:
            raise ValueError('Pause reason is required')
        with self.transaction() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(%s)', (ADMISSION_LOCK,))
            conn.execute('''UPDATE agenttime_fixture.gate SET paused_reason=COALESCE(paused_reason,%s) WHERE id=1''', (reason,))
