"""Disposable PostgreSQL owned exclusively by this test process."""
from pathlib import Path
import tempfile
import unittest

import pgserver
import psycopg


class DatabaseTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pgtemp = tempfile.TemporaryDirectory(prefix='agenttime-pg-', dir='/tmp')
        cls.server = pgserver.get_server(Path(cls.pgtemp.name) / 'data')
        cls.dsn = cls.server.get_uri()

    @classmethod
    def tearDownClass(cls):
        cls.server.cleanup()
        cls.pgtemp.cleanup()

    def setUp(self):
        # Only this class's newly created disposable database is ever reset.
        with psycopg.connect(self.dsn) as conn:
            conn.execute('DROP SCHEMA IF EXISTS agenttime_fixture CASCADE')
