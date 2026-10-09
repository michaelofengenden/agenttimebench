"""Durable natural-attempt claims in a schema separate from fixture admission.

This ledger validates identities, receipts and state transitions. The controller
must independently verify native qualification, artifacts and external stop
evidence before submitting their pinned receipts. Passing these database tests,
or registering a qualification hash, does not establish study readiness.
"""
from contextlib import contextmanager
import hashlib
import re
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .evidence import IntegrityError, canonical_json


ADMISSION_LOCK = 871103011
OUTCOMES = {"completed", "wrong_answer", "refusal", "voluntary_early_finish", "infrastructure_failure",
            "unknown_failure", "agent_resource_exhaustion", "cancelled"}


def document_sha256(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ValueError(f"Invalid {name}")


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("Expected a SHA-256 digest")


def _pins(value):
    if not isinstance(value, dict) or not value: raise ValueError("Runtime pins are required")
    for name, digest in value.items():
        _text(name, "runtime pin name"); _digest(digest)


def validate_manifest(value):
    keys = {"schema_version", "executor", "cohort_id", "agent", "arm", "capacity", "automatic_replacement",
            "runtime_pins", "qualification_proof_sha256", "tasks"}
    if not isinstance(value, dict) or set(value) != keys: raise ValueError("Unexpected natural manifest shape")
    if (type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["executor"] != "claude-natural-v1" or value["arm"] != "natural"):
        raise ValueError("Natural admission requires claude-natural-v1 and the natural arm")
    if type(value["capacity"]) is not int or not 1 <= value["capacity"] <= 220: raise ValueError("Capacity must be 1 through 220")
    if value["automatic_replacement"] is not False: raise ValueError("Automatic replacement is disabled")
    _text(value["cohort_id"], "cohort identity")
    agent = value["agent"]
    if not isinstance(agent, dict) or set(agent) != {"id", "model", "effort", "route"}: raise ValueError("Incomplete agent identity")
    for field in agent: _text(agent[field], "agent " + field)
    if not agent["model"].startswith("claude-"): raise ValueError("The executor requires an explicit Claude model")
    if agent["route"] not in {"subscription", "anthropic_api"}: raise ValueError("Unsupported explicit Claude route")
    _pins(value["runtime_pins"])
    if not {"cli_sha256", "worker_sha256", "controller_sha256"}.issubset(value["runtime_pins"]):
        raise ValueError("CLI, worker and controller pins are required")
    _digest(value["qualification_proof_sha256"])
    tasks = value["tasks"]
    if not isinstance(tasks, list) or not tasks: raise ValueError("At least one task is required")
    seen = set()
    for task in tasks:
        if not isinstance(task, dict) or set(task) != {"id", "contract_sha256", "input_sha256", "runtime_pins"}:
            raise ValueError("Task identity, contract, input and environment pins are required")
        _text(task["id"], "task identity")
        if task["id"] in seen: raise ValueError("Duplicate task identity")
        seen.add(task["id"]); _digest(task["contract_sha256"]); _digest(task["input_sha256"]); _pins(task["runtime_pins"])
    canonical_json(value)


def validate_identity(value):
    if not isinstance(value, dict) or set(value) != {"worker_id", "session_id", "host_id", "daemon_id", "docker_endpoint", "container_id"}:
        raise ValueError("A complete worker, session and container identity is required")
    for field in value: _text(value[field], field)
    try:
        if str(UUID(value["session_id"])) != value["session_id"]: raise ValueError
    except (ValueError, TypeError, AttributeError): raise ValueError("Invalid native session identity") from None
    _digest(value["container_id"])


def _agent_sha(manifest):
    return document_sha256({"agent": manifest["agent"], "runtime_pins": manifest["runtime_pins"]})


class NaturalLedger:
    def __init__(self, dsn):
        self.dsn = dsn

    @contextmanager
    def transaction(self):
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5) as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (ADMISSION_LOCK,))
            yield conn

    @staticmethod
    def _corrupt(conn, message):
        conn.execute("UPDATE agenttime_natural.gate SET paused_reason=COALESCE(paused_reason,'persisted_integrity_failure') WHERE id=1")
        conn.commit()  # The hold must survive the exception and every restart.
        raise IntegrityError(message)

    def initialize(self, capacity=50):
        if type(capacity) is not int or not 1 <= capacity <= 220: raise ValueError("Capacity must be 1 through 220")
        with self.transaction() as conn:
            conn.execute("CREATE SCHEMA IF NOT EXISTS agenttime_natural")
            conn.execute('''CREATE TABLE IF NOT EXISTS agenttime_natural.gate (
                id integer PRIMARY KEY CHECK(id=1), schema_version integer NOT NULL CHECK(schema_version=1),
                capacity integer NOT NULL CHECK(capacity BETWEEN 1 AND 220), paused_reason text)''')
            conn.execute("INSERT INTO agenttime_natural.gate VALUES(1,1,%s,NULL) ON CONFLICT(id) DO NOTHING", (capacity,))
            gate = conn.execute("SELECT * FROM agenttime_natural.gate WHERE id=1").fetchone()
            if gate["capacity"] != capacity or gate["schema_version"] != 1: raise ValueError("Natural ledger capacity/schema is immutable")
            conn.execute('''CREATE TABLE IF NOT EXISTS agenttime_natural.campaigns (
                id text PRIMARY KEY, cohort_id text NOT NULL, agent_sha256 text NOT NULL,
                manifest jsonb NOT NULL, manifest_sha256 text NOT NULL,
                created_at timestamptz NOT NULL DEFAULT now())''')
            conn.execute('''CREATE TABLE IF NOT EXISTS agenttime_natural.attempts (
                id text PRIMARY KEY, campaign_id text NOT NULL REFERENCES agenttime_natural.campaigns(id),
                cohort_id text NOT NULL, task_id text NOT NULL, contract_sha256 text NOT NULL, input_sha256 text NOT NULL,
                runtime_pins jsonb NOT NULL, runtime_sha256 text NOT NULL,
                ordinal integer NOT NULL CHECK(ordinal IN (0,1)), original_id text UNIQUE REFERENCES agenttime_natural.attempts(id),
                replacement_authorization jsonb, replacement_authorization_sha256 text,
                state text NOT NULL CHECK(state IN ('reserved','claimed','released','quarantined','finished')),
                holds_capacity boolean NOT NULL DEFAULT true, identity jsonb, identity_sha256 text,
                baseline_ack jsonb, baseline_ack_sha256 text, release_authorized boolean NOT NULL DEFAULT false,
                release_authorized_at timestamptz, prompt_release jsonb, prompt_release_sha256 text,
                reason text, report jsonb, report_sha256 text, created_at timestamptz NOT NULL DEFAULT now(),
                UNIQUE(cohort_id,task_id,ordinal),
                CHECK((ordinal=0 AND original_id IS NULL AND replacement_authorization IS NULL)
                    OR (ordinal=1 AND original_id IS NOT NULL AND replacement_authorization IS NOT NULL)),
                CHECK(holds_capacity=(state<>'finished')),
                CHECK(state<>'finished' OR (identity IS NOT NULL AND report IS NOT NULL)))''')
            conn.execute('''CREATE UNIQUE INDEX IF NOT EXISTS native_session_once
                ON agenttime_natural.attempts ((identity->>'session_id')) WHERE identity IS NOT NULL''')
            conn.execute('''CREATE UNIQUE INDEX IF NOT EXISTS native_container_once
                ON agenttime_natural.attempts ((identity->>'daemon_id'),(identity->>'container_id')) WHERE identity IS NOT NULL''')

    @classmethod
    def _campaign(cls, conn, campaign_id):
        row = conn.execute("SELECT * FROM agenttime_natural.campaigns WHERE id=%s", (campaign_id,)).fetchone()
        if row is None: raise ValueError("Unknown natural campaign")
        try:
            validate_manifest(row["manifest"])
            valid = (document_sha256(row["manifest"]) == row["manifest_sha256"]
                     and _agent_sha(row["manifest"]) == row["agent_sha256"]
                     and row["cohort_id"] == row["manifest"]["cohort_id"])
        except (TypeError, ValueError, KeyError): valid = False
        if not valid: cls._corrupt(conn, "Persisted natural campaign disagrees with its immutable digest")
        return row

    def create_campaign(self, campaign_id, manifest):
        _text(campaign_id, "campaign identity"); validate_manifest(manifest)
        with self.transaction() as conn:
            gate = conn.execute("SELECT * FROM agenttime_natural.gate WHERE id=1").fetchone()
            if manifest["capacity"] > gate["capacity"]: raise ValueError("Campaign capacity exceeds the global gate")
            existing = conn.execute("SELECT id FROM agenttime_natural.campaigns WHERE cohort_id=%s", (manifest["cohort_id"],)).fetchall()
            incoming = {task["id"]: task for task in manifest["tasks"]}
            for old in existing:
                previous = self._campaign(conn, old["id"])
                if previous["agent_sha256"] != _agent_sha(manifest): raise ValueError("A cohort cannot change its agent configuration")
                for task in previous["manifest"]["tasks"]:
                    if task["id"] in incoming and task != incoming[task["id"]]: raise ValueError("A logical task cannot change its frozen pins")
            digest = document_sha256(manifest)
            conn.execute('''INSERT INTO agenttime_natural.campaigns(id,cohort_id,agent_sha256,manifest,manifest_sha256)
                VALUES(%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING''',
                         (campaign_id, manifest["cohort_id"], _agent_sha(manifest), Jsonb(manifest), digest))
            if self._campaign(conn, campaign_id)["manifest_sha256"] != digest: raise ValueError("Campaign manifest is immutable")

    def campaign(self, campaign_id):
        with self.transaction() as conn: return self._campaign(conn, campaign_id)

    @staticmethod
    def _validate_ack(row, ack):
        keys = {"schema_version", "attempt_id", "identity_sha256", "session_id", "contract_sha256", "input_sha256",
                "inventory_sha256", "archive_manifest_sha256", "archive_verified", "independent_archive", "archive_location"}
        if not isinstance(ack, dict) or set(ack) != keys: raise ValueError("Incomplete independent baseline acknowledgement")
        expected = {"schema_version": "agenttime.natural-baseline-ack.v1", "attempt_id": row["id"],
                    "identity_sha256": row["identity_sha256"], "session_id": row["identity"]["session_id"],
                    "contract_sha256": row["contract_sha256"], "input_sha256": row["input_sha256"]}
        if any(ack.get(key) != value for key, value in expected.items()): raise ValueError("Baseline acknowledgement belongs to other state")
        if ack["archive_verified"] is not True or ack["independent_archive"] is not True: raise ValueError("Independent verified baseline is required")
        _digest(ack["inventory_sha256"]); _digest(ack["archive_manifest_sha256"]); _text(ack["archive_location"], "archive location")

    @staticmethod
    def _validate_release(row, event):
        expected = {"kind": "prompt_released", "attempt_id": row["id"], "identity_sha256": row["identity_sha256"], "input_sha256": row["input_sha256"]}
        if not isinstance(event, dict) or set(event) != set(expected) | {"clock_id", "monotonic_ns", "event_sha256"}:
            raise ValueError("Incomplete native release evidence")
        if any(event.get(key) != value for key, value in expected.items()): raise ValueError("Native release belongs to other state")
        if type(event["monotonic_ns"]) is not int or event["monotonic_ns"] < 0: raise ValueError("Invalid native release clock")
        _text(event["clock_id"], "native clock"); _digest(event["event_sha256"])

    @staticmethod
    def _validate_report(row, report):
        if not isinstance(report, dict): raise ValueError("A final report is required")
        for key, expected in (("attempt_id", row["id"]), ("identity_sha256", row["identity_sha256"]),
                              ("contract_sha256", row["contract_sha256"]), ("input_sha256", row["input_sha256"])):
            if report.get(key) != expected: raise ValueError("Final report belongs to other state")
        if report.get("outcome") not in OUTCOMES: raise ValueError("Unknown final outcome")
        if report.get("timing_status") not in {"valid", "invalid", "censored", "unavailable"}: raise ValueError("Explicit timing status is required")
        if report["timing_status"] == "valid" and row["prompt_release"] is None: raise ValueError("Valid timing requires observed native release")
        stop = report.get("stop_evidence")
        if (not isinstance(stop, dict) or stop.get("identity_sha256") != row["identity_sha256"]
                or stop.get("stop_verified") is not True or stop.get("owned_work_drained") is not True):
            raise ValueError("Matching verified execution stop and owned-work drain are required")
        _digest(stop.get("proof_sha256"))
        if report["outcome"] == "infrastructure_failure":
            if report.get("failure_class") != "external_infrastructure": raise ValueError("Typed external infrastructure evidence is required")
            _digest(report.get("failure_evidence_sha256"))
            if report.get("continuation_status") not in {"impossible", "possible", "unknown"}: raise ValueError("Explicit continuation status is required")
            if report["continuation_status"] == "impossible": _digest(report.get("continuation_evidence_sha256"))
        canonical_json(report)

    @staticmethod
    def _validate_replacement(original, authorization):
        if original["ordinal"] != 0 or original["state"] != "finished":
            raise ValueError("Replacement requires the stopped original attempt")
        report = original["report"]
        if (report["outcome"] != "infrastructure_failure" or report.get("failure_class") != "external_infrastructure"
                or report.get("continuation_status") != "impossible"):
            raise ValueError("Only an external infrastructure failure with impossible continuation qualifies")
        expected = {"kind": "explicit_operator_request", "original_attempt_id": original["id"],
                    "failure_evidence_sha256": report["failure_evidence_sha256"],
                    "continuation_evidence_sha256": report["continuation_evidence_sha256"]}
        if (not isinstance(authorization, dict) or set(authorization) != set(expected) | {"request_id"}
                or any(authorization.get(k) != v for k, v in expected.items())):
            raise ValueError("An explicit matching replacement authorization is required")
        _text(authorization["request_id"], "operator request identity")

    @classmethod
    def _attempt(cls, conn, attempt_id):
        row = conn.execute("SELECT * FROM agenttime_natural.attempts WHERE id=%s", (attempt_id,)).fetchone()
        if row is None: raise ValueError("Unknown natural attempt")
        campaign = cls._campaign(conn, row["campaign_id"])
        try:
            task = next(t for t in campaign["manifest"]["tasks"] if t["id"] == row["task_id"])
            if row["cohort_id"] != campaign["cohort_id"] or any(row[key] != task[key] for key in ("contract_sha256", "input_sha256", "runtime_pins")):
                raise ValueError("Attempt pins changed")
            if document_sha256(row["runtime_pins"]) != row["runtime_sha256"]: raise ValueError("Runtime pins changed")
            for field in ("identity", "baseline_ack", "prompt_release", "report", "replacement_authorization"):
                value, pin = row[field], row[field + "_sha256"]
                if (value is None and pin is not None) or (value is not None and document_sha256(value) != pin): raise ValueError("Receipt digest mismatch")
            if row["identity"] is not None: validate_identity(row["identity"])
            if row["state"] in {"claimed", "released", "finished"} and row["identity"] is None: raise ValueError("Missing claimed identity")
            if row["baseline_ack"] is not None: cls._validate_ack(row, row["baseline_ack"])
            if row["release_authorized"] and (row["baseline_ack"] is None or row["release_authorized_at"] is None): raise ValueError("Release lacks baseline")
            if row["prompt_release"] is not None:
                cls._validate_release(row, row["prompt_release"])
                if not row["release_authorized"]: raise ValueError("Unpermitted release")
            if row["state"] == "released" and row["prompt_release"] is None: raise ValueError("Missing native release")
            if row["report"] is not None: cls._validate_report(row, row["report"])
            if row["ordinal"] == 1:
                # Reject malformed or cyclic lineage before the recursive read.
                parent = conn.execute("SELECT ordinal FROM agenttime_natural.attempts WHERE id=%s", (row["original_id"],)).fetchone()
                if parent is None or parent["ordinal"] != 0: raise ValueError("Replacement lacks its original")
                original = cls._attempt(conn, row["original_id"])
                if any(row[key] != original[key] for key in ("campaign_id", "cohort_id", "task_id", "contract_sha256", "input_sha256", "runtime_sha256")):
                    raise ValueError("Replacement lineage changed")
                cls._validate_replacement(original, row["replacement_authorization"])
        except (TypeError, ValueError, KeyError, StopIteration):
            cls._corrupt(conn, "Persisted natural attempt disagrees with its identity, pins or receipts")
        return row

    @staticmethod
    def _owner(row, identity):
        validate_identity(identity)
        if row["identity"] != identity or row["identity_sha256"] != document_sha256(identity): raise ValueError("Operation is not from the original execution identity")

    @classmethod
    def _active(cls, conn):
        rows = conn.execute("SELECT id FROM agenttime_natural.attempts WHERE holds_capacity").fetchall()
        attempts = [cls._attempt(conn, row["id"]) for row in rows]
        campaigns = {row["campaign_id"]: cls._campaign(conn, row["campaign_id"]) for row in attempts}
        return attempts, campaigns

    @classmethod
    def _block(cls, conn, campaign):
        gate = conn.execute("SELECT * FROM agenttime_natural.gate WHERE id=1").fetchone()
        attempts, campaigns = cls._active(conn)
        if gate["paused_reason"] is not None: return "paused"
        if any(c["agent_sha256"] != campaign["agent_sha256"] for c in campaigns.values()): return "another_agent"
        if len(attempts) >= gate["capacity"]: return "capacity"
        if sum(a["campaign_id"] == campaign["id"] for a in attempts) >= campaign["manifest"]["capacity"]: return "capacity"
        return None

    def admission_block_reason(self, campaign_id):
        with self.transaction() as conn: return self._block(conn, self._campaign(conn, campaign_id))

    @staticmethod
    def _insert(conn, campaign, task, *, original=None, authorization=None):
        return conn.execute('''INSERT INTO agenttime_natural.attempts
            (id,campaign_id,cohort_id,task_id,contract_sha256,input_sha256,runtime_pins,runtime_sha256,
             ordinal,original_id,replacement_authorization,replacement_authorization_sha256,state)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'reserved') RETURNING *''',
                            (str(uuid4()), campaign["id"], campaign["cohort_id"], task["id"], task["contract_sha256"], task["input_sha256"],
                             Jsonb(task["runtime_pins"]), document_sha256(task["runtime_pins"]), 1 if original else 0,
                             original["id"] if original else None, Jsonb(authorization) if authorization else None,
                             document_sha256(authorization) if authorization else None)).fetchone()

    def reserve(self, campaign_id):
        """Commit one permanent dispatch intent, independent of any worker lease."""
        with self.transaction() as conn:
            campaign = self._campaign(conn, campaign_id)
            if self._block(conn, campaign): return None
            existing = {row["task_id"] for row in conn.execute("SELECT task_id FROM agenttime_natural.attempts WHERE cohort_id=%s", (campaign["cohort_id"],))}
            task = next((t for t in campaign["manifest"]["tasks"] if t["id"] not in existing), None)
            return self._insert(conn, campaign, task) if task else None

    def claim(self, attempt_id, identity):
        validate_identity(identity)
        with self.transaction() as conn:
            row = self._attempt(conn, attempt_id)
            self._active(conn)
            if conn.execute("SELECT paused_reason FROM agenttime_natural.gate WHERE id=1").fetchone()["paused_reason"] is not None: return False
            if row["state"] != "reserved" or row["identity"] is not None: return False
            reused = conn.execute('''SELECT id FROM agenttime_natural.attempts WHERE
                identity->>'session_id'=%s OR (identity->>'daemon_id'=%s AND identity->>'container_id'=%s)''',
                                  (identity["session_id"], identity["daemon_id"], identity["container_id"])).fetchone()
            if reused: raise ValueError("A native session or container cannot be reused for another attempt")
            conn.execute("UPDATE agenttime_natural.attempts SET identity=%s,identity_sha256=%s,state='claimed' WHERE id=%s",
                         (Jsonb(identity), document_sha256(identity), attempt_id))
            return True

    def acknowledge_baseline(self, attempt_id, identity, acknowledgement):
        with self.transaction() as conn:
            row = self._attempt(conn, attempt_id); self._owner(row, identity); self._validate_ack(row, acknowledgement)
            if row["baseline_ack"] is not None:
                if row["baseline_ack"] != acknowledgement: raise ValueError("Baseline acknowledgement is immutable")
                return
            if row["state"] != "claimed" or row["release_authorized"]: raise ValueError("Baseline must precede release")
            conn.execute("UPDATE agenttime_natural.attempts SET baseline_ack=%s,baseline_ack_sha256=%s WHERE id=%s",
                         (Jsonb(acknowledgement), document_sha256(acknowledgement), attempt_id))

    def authorize_release(self, attempt_id, identity):
        """One-use permission before onset; this is not observed native release."""
        with self.transaction() as conn:
            row = self._attempt(conn, attempt_id); self._owner(row, identity)
            self._active(conn)  # Validate unresolved identities without denying a fully reserved fleet.
            paused = conn.execute("SELECT paused_reason FROM agenttime_natural.gate WHERE id=1").fetchone()["paused_reason"]
            if paused is not None or row["state"] != "claimed" or row["baseline_ack"] is None or row["release_authorized"]: return False
            conn.execute("UPDATE agenttime_natural.attempts SET release_authorized=true,release_authorized_at=now() WHERE id=%s", (attempt_id,))
            return True

    def mark_prompt_released(self, attempt_id, identity, event):
        """Record observed onset. Repeating identical evidence grants no new launch."""
        with self.transaction() as conn:
            row = self._attempt(conn, attempt_id); self._owner(row, identity); self._validate_release(row, event)
            if row["prompt_release"] is not None:
                if row["prompt_release"] != event: raise ValueError("Native release evidence is immutable")
                return False
            if not row["release_authorized"] or row["state"] not in {"claimed", "quarantined"}: raise ValueError("Release was not authorized")
            # A shared pause does not erase a native onset already observed by its owner.
            conn.execute("UPDATE agenttime_natural.attempts SET prompt_release=%s,prompt_release_sha256=%s,state=CASE WHEN state='quarantined' THEN state ELSE 'released' END WHERE id=%s",
                         (Jsonb(event), document_sha256(event), attempt_id))
            return True

    def quarantine(self, attempt_id, reason):
        _text(reason, "quarantine reason")
        with self.transaction() as conn:
            row = self._attempt(conn, attempt_id)
            if row["state"] == "finished": return False
            conn.execute("UPDATE agenttime_natural.attempts SET state='quarantined',reason=COALESCE(reason,%s) WHERE id=%s", (reason, attempt_id))
            return True

    def finish(self, attempt_id, identity, report):
        with self.transaction() as conn:
            row = self._attempt(conn, attempt_id); self._owner(row, identity); self._validate_report(row, report)
            if row["state"] == "finished":
                if row["report"] != report: raise ValueError("Final report is immutable")
                return
            if row["state"] == "reserved": raise ValueError("Unclaimed work cannot finish")
            conn.execute("UPDATE agenttime_natural.attempts SET report=%s,report_sha256=%s,state='finished',holds_capacity=false WHERE id=%s",
                         (Jsonb(report), document_sha256(report), attempt_id))

    def reserve_replacement(self, original_id, authorization):
        """Explicit operator request only, with one allowance per cohort/task."""
        with self.transaction() as conn:
            original = self._attempt(conn, original_id)
            self._validate_replacement(original, authorization)
            if conn.execute("SELECT id FROM agenttime_natural.attempts WHERE cohort_id=%s AND task_id=%s AND ordinal=1", (original["cohort_id"], original["task_id"])).fetchone(): return None
            campaign = self._campaign(conn, original["campaign_id"])
            if self._block(conn, campaign): return None
            task = next(t for t in campaign["manifest"]["tasks"] if t["id"] == original["task_id"])
            return self._insert(conn, campaign, task, original=original, authorization=authorization)

    def attempt(self, attempt_id):
        with self.transaction() as conn: return self._attempt(conn, attempt_id)

    def attempts(self, campaign_id):
        """Diagnostic rows, including corrupted or held evidence; not admission proof."""
        with self.transaction() as conn:
            return conn.execute("SELECT * FROM agenttime_natural.attempts WHERE campaign_id=%s ORDER BY created_at,id", (campaign_id,)).fetchall()

    def gate(self):
        with self.transaction() as conn: return conn.execute("SELECT * FROM agenttime_natural.gate WHERE id=1").fetchone()

    def pause(self, reason):
        _text(reason, "pause reason")
        with self.transaction() as conn:
            conn.execute("UPDATE agenttime_natural.gate SET paused_reason=COALESCE(paused_reason,%s) WHERE id=1", (reason,))
