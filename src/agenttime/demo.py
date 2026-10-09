"""A credential-free demonstration using an isolated disposable PostgreSQL server."""
from pathlib import Path
import tempfile

from .evidence import canonical_json
from .fixture import _write_once, build_manifest, run_campaign
from .ledger import Ledger


def run_demo(root: Path):
    try:
        import pgserver
    except ImportError as error:
        raise ValueError('Install the locked test group: uv sync --locked --group test') from error
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    manifest = build_manifest(root, [
        {'id': 'terminal-submission', 'behavior': 'post_terminal_write', 'work_seconds': .1},
        {'id': 'missing-grade', 'behavior': 'grade_failure', 'work_seconds': .1},
        {'id': 'wrong-answer', 'behavior': 'wrong_answer', 'work_seconds': .1},
    ])
    _write_once(root / 'manifest.json', canonical_json(manifest))
    with tempfile.TemporaryDirectory(prefix='agenttime-demo-pg-', dir='/tmp') as temporary:
        with pgserver.get_server(Path(temporary) / 'data') as server:
            ledger = Ledger(server.get_uri())
            ledger.initialize(2)
            ledger.create_campaign('local-demo', manifest)
            report = run_campaign(ledger, 'local-demo')
            _write_once(root / 'report.json', canonical_json(report))
    return report
