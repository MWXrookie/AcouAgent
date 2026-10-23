"""Explicit, additive authoring extension. Never installed by default migrate()."""
import sqlite3

AUTHORING_SCHEMA_VERSION = 18
DDL = """

CREATE TABLE skill_drafts (
 draft_id TEXT PRIMARY KEY,
 origin TEXT NOT NULL CHECK(origin IN ('manual','automatic')),
 actor_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('editing','submitted','archived')),
 current_version INTEGER NOT NULL CHECK(current_version>0),
 revision INTEGER NOT NULL CHECK(revision>0),
 created_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL
);
CREATE TABLE skill_draft_versions (
 draft_id TEXT NOT NULL REFERENCES skill_drafts(draft_id),
 version INTEGER NOT NULL CHECK(version>0),
 spec_version TEXT NOT NULL, spec_json TEXT NOT NULL CHECK(json_valid(spec_json)),
 content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
 normalizer_version TEXT NOT NULL,
 fingerprint_version TEXT NOT NULL,
 case_fingerprint TEXT NOT NULL CHECK(length(case_fingerprint)=64),
 code_sha256 TEXT NOT NULL CHECK(length(code_sha256)=64),
 source_run_id TEXT, created_ms INTEGER NOT NULL,
 PRIMARY KEY(draft_id,version), UNIQUE(draft_id,content_sha256)
);
CREATE INDEX idx_drafts_updated ON skill_drafts(updated_ms,draft_id);
CREATE INDEX idx_draft_case ON skill_draft_versions(case_fingerprint);

CREATE TABLE skill_validation_profiles (
 profile_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0),
 policy_json TEXT NOT NULL CHECK(json_valid(policy_json)),
 policy_sha256 TEXT NOT NULL CHECK(length(policy_sha256)=64),
 validator_id TEXT NOT NULL, validator_sha256 TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('enabled','retired')),
 created_ms INTEGER NOT NULL,
 PRIMARY KEY(profile_id,version), UNIQUE(profile_id,policy_sha256)
);
CREATE TABLE skill_validation_assets (
 asset_sha256 TEXT PRIMARY KEY CHECK(length(asset_sha256)=64),
 storage_key TEXT NOT NULL UNIQUE,
 byte_count INTEGER NOT NULL CHECK(byte_count>=0),
 media_type TEXT NOT NULL, metadata_json TEXT NOT NULL CHECK(json_valid(metadata_json)),
 role TEXT NOT NULL CHECK(role IN ('reference','result','evidence')),
 provenance TEXT NOT NULL CHECK(provenance IN ('unreviewed','server_reviewed','execution_bound')),
 created_ms INTEGER NOT NULL
);

CREATE TABLE skill_validation_jobs (
 job_id TEXT PRIMARY KEY,
 draft_id TEXT NOT NULL, draft_version INTEGER NOT NULL,
 profile_id TEXT NOT NULL, profile_version INTEGER NOT NULL,
 intent TEXT NOT NULL CHECK(intent IN ('validate','trial')),
 idempotency_scope TEXT NOT NULL, idempotency_key TEXT NOT NULL,
 request_sha256 TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN
 ('queued','running','needs_reconciliation','blocked_credentials','cancel_requested',
  'cancelled','failed','rejected','validated','admitted')),
 revision INTEGER NOT NULL CHECK(revision>0),
 attempt_no INTEGER NOT NULL DEFAULT 0,
 lease_owner TEXT, lease_until_ms INTEGER, fence INTEGER NOT NULL DEFAULT 0,
 cancel_ms INTEGER, reason_code TEXT,
 created_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL,
 FOREIGN KEY(draft_id,draft_version) REFERENCES skill_draft_versions(draft_id,version),
 FOREIGN KEY(profile_id,profile_version) REFERENCES skill_validation_profiles(profile_id,version),
 UNIQUE(idempotency_scope,idempotency_key)
);
CREATE INDEX idx_jobs_claim ON skill_validation_jobs(state,created_ms,job_id);
CREATE INDEX idx_jobs_lease ON skill_validation_jobs(state,lease_until_ms);
CREATE TABLE skill_validation_attempts (
 job_id TEXT NOT NULL REFERENCES skill_validation_jobs(job_id),
 attempt_no INTEGER NOT NULL, fence INTEGER NOT NULL,
 run_id TEXT NOT NULL UNIQUE, request_nonce TEXT NOT NULL UNIQUE,
 request_sha256 TEXT NOT NULL, code_sha256 TEXT NOT NULL, parameters_sha256 TEXT NOT NULL,
 runtime_sha256 TEXT NOT NULL,
 dispatch_state TEXT NOT NULL CHECK(dispatch_state IN
 ('prepared','dispatching','receipt_bound','unknown','terminal')),
 receipt_sha256 TEXT, output_manifest_sha256 TEXT,
 created_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL,
 PRIMARY KEY(job_id,attempt_no)
);
CREATE TABLE skill_validation_evidence (
 evidence_id TEXT PRIMARY KEY,
 job_id TEXT NOT NULL, attempt_no INTEGER NOT NULL,
 binding_sha256 TEXT NOT NULL UNIQUE,
 manifest_sha256 TEXT NOT NULL REFERENCES skill_validation_assets(asset_sha256),
 verdict TEXT NOT NULL CHECK(verdict IN ('passed','failed','unsupported')),
 created_ms INTEGER NOT NULL,
 FOREIGN KEY(job_id,attempt_no) REFERENCES skill_validation_attempts(job_id,attempt_no),
 UNIQUE(job_id,attempt_no)
);
CREATE TABLE skill_validation_admissions (
 job_id TEXT PRIMARY KEY REFERENCES skill_validation_jobs(job_id),
 evidence_id TEXT NOT NULL UNIQUE REFERENCES skill_validation_evidence(evidence_id),
 skill_id TEXT NOT NULL, skill_version INTEGER NOT NULL,
 admission_binding_sha256 TEXT NOT NULL UNIQUE,
 created_ms INTEGER NOT NULL,
 FOREIGN KEY(skill_id,skill_version) REFERENCES skill_versions(skill_id,version)
);
CREATE TABLE skill_validation_outbox (
 event_id TEXT PRIMARY KEY, dedupe_key TEXT NOT NULL UNIQUE,
 job_id TEXT NOT NULL REFERENCES skill_validation_jobs(job_id),
 event_type TEXT NOT NULL,
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
 state TEXT NOT NULL CHECK(state IN ('pending','processing','done','blocked')),
 revision INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 lease_owner TEXT, lease_until_ms INTEGER, next_ms INTEGER NOT NULL,
 created_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL
);
CREATE INDEX idx_outbox_pending ON skill_validation_outbox(state,next_ms,event_id);
CREATE TABLE skill_draft_requests (
 actor_id TEXT NOT NULL, operation TEXT NOT NULL,
 idempotency_key TEXT NOT NULL, request_sha256 TEXT NOT NULL,
 draft_id TEXT NOT NULL, version INTEGER NOT NULL, created_ms INTEGER NOT NULL,
 PRIMARY KEY(actor_id,operation,idempotency_key),
 FOREIGN KEY(draft_id,version) REFERENCES skill_draft_versions(draft_id,version)
);
CREATE INDEX idx_skill_drafts_actor_created ON skill_drafts(actor_id,created_ms,draft_id);
CREATE TRIGGER skill_draft_versions_no_update BEFORE UPDATE ON skill_draft_versions BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER skill_draft_versions_no_delete BEFORE DELETE ON skill_draft_versions BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER skill_draft_versions_no_replace BEFORE INSERT ON skill_draft_versions WHEN EXISTS(SELECT 1 FROM skill_draft_versions WHERE draft_id=NEW.draft_id AND (version=NEW.version OR content_sha256=NEW.content_sha256)) BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER skill_draft_requests_no_update BEFORE UPDATE ON skill_draft_requests BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER skill_draft_requests_no_delete BEFORE DELETE ON skill_draft_requests BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER skill_draft_requests_no_replace BEFORE INSERT ON skill_draft_requests WHEN EXISTS(SELECT 1 FROM skill_draft_requests WHERE actor_id=NEW.actor_id AND operation=NEW.operation AND idempotency_key=NEW.idempotency_key) BEGIN SELECT RAISE(ABORT,'append-only'); END;
"""
EXPECTED_COLUMNS = {'skill_drafts': ['draft_id',
                  'origin',
                  'actor_id',
                  'state',
                  'current_version',
                  'revision',
                  'created_ms',
                  'updated_ms'],
 'skill_draft_versions': ['draft_id',
                          'version',
                          'spec_version',
                          'spec_json',
                          'content_sha256',
                          'normalizer_version',
                          'fingerprint_version',
                          'case_fingerprint',
                          'code_sha256',
                          'source_run_id',
                          'created_ms'],
 'skill_validation_profiles': ['profile_id',
                               'version',
                               'policy_json',
                               'policy_sha256',
                               'validator_id',
                               'validator_sha256',
                               'state',
                               'created_ms'],
 'skill_validation_assets': ['asset_sha256',
                             'storage_key',
                             'byte_count',
                             'media_type',
                             'metadata_json',
                             'role',
                             'provenance',
                             'created_ms'],
 'skill_validation_jobs': ['job_id',
                           'draft_id',
                           'draft_version',
                           'profile_id',
                           'profile_version',
                           'intent',
                           'idempotency_scope',
                           'idempotency_key',
                           'request_sha256',
                           'state',
                           'revision',
                           'attempt_no',
                           'lease_owner',
                           'lease_until_ms',
                           'fence',
                           'cancel_ms',
                           'reason_code',
                           'created_ms',
                           'updated_ms'],
 'skill_validation_attempts': ['job_id',
                               'attempt_no',
                               'fence',
                               'run_id',
                               'request_nonce',
                               'request_sha256',
                               'code_sha256',
                               'parameters_sha256',
                               'runtime_sha256',
                               'dispatch_state',
                               'receipt_sha256',
                               'output_manifest_sha256',
                               'created_ms',
                               'updated_ms'],
 'skill_validation_evidence': ['evidence_id',
                               'job_id',
                               'attempt_no',
                               'binding_sha256',
                               'manifest_sha256',
                               'verdict',
                               'created_ms'],
 'skill_validation_admissions': ['job_id',
                                 'evidence_id',
                                 'skill_id',
                                 'skill_version',
                                 'admission_binding_sha256',
                                 'created_ms'],
 'skill_validation_outbox': ['event_id',
                             'dedupe_key',
                             'job_id',
                             'event_type',
                             'payload_json',
                             'state',
                             'revision',
                             'attempts',
                             'lease_owner',
                             'lease_until_ms',
                             'next_ms',
                             'created_ms',
                             'updated_ms'],
 'skill_draft_requests': ['actor_id',
                          'operation',
                          'idempotency_key',
                          'request_sha256',
                          'draft_id',
                          'version',
                          'created_ms']}
EXPECTED_OBJECTS = [('idx_drafts_updated', 'index'),
 ('idx_draft_case', 'index'),
 ('idx_jobs_claim', 'index'),
 ('idx_jobs_lease', 'index'),
 ('idx_outbox_pending', 'index'),
 ('idx_skill_drafts_actor_created', 'index'),
 ('skill_draft_versions_no_update', 'trigger'),
 ('skill_draft_versions_no_delete', 'trigger'),
 ('skill_draft_versions_no_replace', 'trigger'),
 ('skill_draft_requests_no_update', 'trigger'),
 ('skill_draft_requests_no_delete', 'trigger'),
 ('skill_draft_requests_no_replace', 'trigger')]
EXPECTED_SQL = {'idx_draft_case': 'CREATE INDEX idx_draft_case ON skill_draft_versions(case_fingerprint)',
 'idx_drafts_updated': 'CREATE INDEX idx_drafts_updated ON skill_drafts(updated_ms,draft_id)',
 'idx_jobs_claim': 'CREATE INDEX idx_jobs_claim ON '
                   'skill_validation_jobs(state,created_ms,job_id)',
 'idx_jobs_lease': 'CREATE INDEX idx_jobs_lease ON skill_validation_jobs(state,lease_until_ms)',
 'idx_outbox_pending': 'CREATE INDEX idx_outbox_pending ON '
                       'skill_validation_outbox(state,next_ms,event_id)',
 'idx_skill_drafts_actor_created': 'CREATE INDEX idx_skill_drafts_actor_created ON '
                                   'skill_drafts(actor_id,created_ms,draft_id)',
 'skill_draft_requests': 'CREATE TABLE skill_draft_requests ( actor_id TEXT NOT NULL, operation '
                         'TEXT NOT NULL, idempotency_key TEXT NOT NULL, request_sha256 TEXT NOT '
                         'NULL, draft_id TEXT NOT NULL, version INTEGER NOT NULL, created_ms '
                         'INTEGER NOT NULL, PRIMARY KEY(actor_id,operation,idempotency_key), '
                         'FOREIGN KEY(draft_id,version) REFERENCES '
                         'skill_draft_versions(draft_id,version) )',
 'skill_draft_requests_no_delete': 'CREATE TRIGGER skill_draft_requests_no_delete BEFORE DELETE '
                                   'ON skill_draft_requests BEGIN SELECT '
                                   "RAISE(ABORT,'append-only'); END",
 'skill_draft_requests_no_replace': 'CREATE TRIGGER skill_draft_requests_no_replace BEFORE '
                                    'INSERT ON skill_draft_requests WHEN EXISTS(SELECT 1 FROM '
                                    'skill_draft_requests WHERE actor_id=NEW.actor_id AND '
                                    'operation=NEW.operation AND '
                                    'idempotency_key=NEW.idempotency_key) BEGIN SELECT '
                                    "RAISE(ABORT,'append-only'); END",
 'skill_draft_requests_no_update': 'CREATE TRIGGER skill_draft_requests_no_update BEFORE UPDATE '
                                   'ON skill_draft_requests BEGIN SELECT '
                                   "RAISE(ABORT,'append-only'); END",
 'skill_draft_versions': 'CREATE TABLE skill_draft_versions ( draft_id TEXT NOT NULL REFERENCES '
                         'skill_drafts(draft_id), version INTEGER NOT NULL CHECK(version>0), '
                         'spec_version TEXT NOT NULL, spec_json TEXT NOT NULL '
                         'CHECK(json_valid(spec_json)), content_sha256 TEXT NOT NULL '
                         'CHECK(length(content_sha256)=64), normalizer_version TEXT NOT NULL, '
                         'fingerprint_version TEXT NOT NULL, case_fingerprint TEXT NOT NULL '
                         'CHECK(length(case_fingerprint)=64), code_sha256 TEXT NOT NULL '
                         'CHECK(length(code_sha256)=64), source_run_id TEXT, created_ms INTEGER '
                         'NOT NULL, PRIMARY KEY(draft_id,version), '
                         'UNIQUE(draft_id,content_sha256) )',
 'skill_draft_versions_no_delete': 'CREATE TRIGGER skill_draft_versions_no_delete BEFORE DELETE '
                                   'ON skill_draft_versions BEGIN SELECT '
                                   "RAISE(ABORT,'append-only'); END",
 'skill_draft_versions_no_replace': 'CREATE TRIGGER skill_draft_versions_no_replace BEFORE '
                                    'INSERT ON skill_draft_versions WHEN EXISTS(SELECT 1 FROM '
                                    'skill_draft_versions WHERE draft_id=NEW.draft_id AND '
                                    '(version=NEW.version OR content_sha256=NEW.content_sha256)) '
                                    "BEGIN SELECT RAISE(ABORT,'append-only'); END",
 'skill_draft_versions_no_update': 'CREATE TRIGGER skill_draft_versions_no_update BEFORE UPDATE '
                                   'ON skill_draft_versions BEGIN SELECT '
                                   "RAISE(ABORT,'append-only'); END",
 'skill_drafts': 'CREATE TABLE skill_drafts ( draft_id TEXT PRIMARY KEY, origin TEXT NOT NULL '
                 "CHECK(origin IN ('manual','automatic')), actor_id TEXT NOT NULL, state TEXT "
                 "NOT NULL CHECK(state IN ('editing','submitted','archived')), current_version "
                 'INTEGER NOT NULL CHECK(current_version>0), revision INTEGER NOT NULL '
                 'CHECK(revision>0), created_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL )',
 'skill_validation_admissions': 'CREATE TABLE skill_validation_admissions ( job_id TEXT PRIMARY '
                                'KEY REFERENCES skill_validation_jobs(job_id), evidence_id TEXT '
                                'NOT NULL UNIQUE REFERENCES '
                                'skill_validation_evidence(evidence_id), skill_id TEXT NOT NULL, '
                                'skill_version INTEGER NOT NULL, admission_binding_sha256 TEXT '
                                'NOT NULL UNIQUE, created_ms INTEGER NOT NULL, FOREIGN '
                                'KEY(skill_id,skill_version) REFERENCES '
                                'skill_versions(skill_id,version) )',
 'skill_validation_assets': 'CREATE TABLE skill_validation_assets ( asset_sha256 TEXT PRIMARY '
                            'KEY CHECK(length(asset_sha256)=64), storage_key TEXT NOT NULL '
                            'UNIQUE, byte_count INTEGER NOT NULL CHECK(byte_count>=0), '
                            'media_type TEXT NOT NULL, metadata_json TEXT NOT NULL '
                            'CHECK(json_valid(metadata_json)), role TEXT NOT NULL CHECK(role IN '
                            "('reference','result','evidence')), provenance TEXT NOT NULL "
                            'CHECK(provenance IN '
                            "('unreviewed','server_reviewed','execution_bound')), created_ms "
                            'INTEGER NOT NULL )',
 'skill_validation_attempts': 'CREATE TABLE skill_validation_attempts ( job_id TEXT NOT NULL '
                              'REFERENCES skill_validation_jobs(job_id), attempt_no INTEGER NOT '
                              'NULL, fence INTEGER NOT NULL, run_id TEXT NOT NULL UNIQUE, '
                              'request_nonce TEXT NOT NULL UNIQUE, request_sha256 TEXT NOT NULL, '
                              'code_sha256 TEXT NOT NULL, parameters_sha256 TEXT NOT NULL, '
                              'runtime_sha256 TEXT NOT NULL, dispatch_state TEXT NOT NULL '
                              'CHECK(dispatch_state IN '
                              "('prepared','dispatching','receipt_bound','unknown','terminal')), "
                              'receipt_sha256 TEXT, output_manifest_sha256 TEXT, created_ms '
                              'INTEGER NOT NULL, updated_ms INTEGER NOT NULL, PRIMARY '
                              'KEY(job_id,attempt_no) )',
 'skill_validation_evidence': 'CREATE TABLE skill_validation_evidence ( evidence_id TEXT PRIMARY '
                              'KEY, job_id TEXT NOT NULL, attempt_no INTEGER NOT NULL, '
                              'binding_sha256 TEXT NOT NULL UNIQUE, manifest_sha256 TEXT NOT '
                              'NULL REFERENCES skill_validation_assets(asset_sha256), verdict '
                              'TEXT NOT NULL CHECK(verdict IN '
                              "('passed','failed','unsupported')), created_ms INTEGER NOT NULL, "
                              'FOREIGN KEY(job_id,attempt_no) REFERENCES '
                              'skill_validation_attempts(job_id,attempt_no), '
                              'UNIQUE(job_id,attempt_no) )',
 'skill_validation_jobs': 'CREATE TABLE skill_validation_jobs ( job_id TEXT PRIMARY KEY, '
                          'draft_id TEXT NOT NULL, draft_version INTEGER NOT NULL, profile_id '
                          'TEXT NOT NULL, profile_version INTEGER NOT NULL, intent TEXT NOT NULL '
                          "CHECK(intent IN ('validate','trial')), idempotency_scope TEXT NOT "
                          'NULL, idempotency_key TEXT NOT NULL, request_sha256 TEXT NOT NULL, '
                          'state TEXT NOT NULL CHECK(state IN '
                          "('queued','running','needs_reconciliation','blocked_credentials','cancel_requested', "
                          "'cancelled','failed','rejected','validated','admitted')), revision "
                          'INTEGER NOT NULL CHECK(revision>0), attempt_no INTEGER NOT NULL '
                          'DEFAULT 0, lease_owner TEXT, lease_until_ms INTEGER, fence INTEGER '
                          'NOT NULL DEFAULT 0, cancel_ms INTEGER, reason_code TEXT, created_ms '
                          'INTEGER NOT NULL, updated_ms INTEGER NOT NULL, FOREIGN '
                          'KEY(draft_id,draft_version) REFERENCES '
                          'skill_draft_versions(draft_id,version), FOREIGN '
                          'KEY(profile_id,profile_version) REFERENCES '
                          'skill_validation_profiles(profile_id,version), '
                          'UNIQUE(idempotency_scope,idempotency_key) )',
 'skill_validation_outbox': 'CREATE TABLE skill_validation_outbox ( event_id TEXT PRIMARY KEY, '
                            'dedupe_key TEXT NOT NULL UNIQUE, job_id TEXT NOT NULL REFERENCES '
                            'skill_validation_jobs(job_id), event_type TEXT NOT NULL, '
                            'payload_json TEXT NOT NULL CHECK(json_valid(payload_json)), state '
                            'TEXT NOT NULL CHECK(state IN '
                            "('pending','processing','done','blocked')), revision INTEGER NOT "
                            'NULL, attempts INTEGER NOT NULL DEFAULT 0, lease_owner TEXT, '
                            'lease_until_ms INTEGER, next_ms INTEGER NOT NULL, created_ms '
                            'INTEGER NOT NULL, updated_ms INTEGER NOT NULL )',
 'skill_validation_profiles': 'CREATE TABLE skill_validation_profiles ( profile_id TEXT NOT '
                              'NULL, version INTEGER NOT NULL CHECK(version>0), policy_json TEXT '
                              'NOT NULL CHECK(json_valid(policy_json)), policy_sha256 TEXT NOT '
                              'NULL CHECK(length(policy_sha256)=64), validator_id TEXT NOT NULL, '
                              'validator_sha256 TEXT NOT NULL, state TEXT NOT NULL CHECK(state '
                              "IN ('enabled','retired')), created_ms INTEGER NOT NULL, PRIMARY "
                              'KEY(profile_id,version), UNIQUE(profile_id,policy_sha256) )'}


def schema_ready(db):
    """Recognize the complete extension, never a forged version marker alone."""
    try:
        versions = [r[0] for r in db.execute("SELECT version FROM schema_migrations ORDER BY version")]
        if versions != list(range(1, AUTHORING_SCHEMA_VERSION + 1)):
            return False
        for name, columns in EXPECTED_COLUMNS.items():
            if [r[1] for r in db.execute("PRAGMA table_info(" + name + ")")] != columns:
                return False
        for name, kind in EXPECTED_OBJECTS:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name=? AND type=?", (name, kind)).fetchone() is None:
                return False
        for name, expected in EXPECTED_SQL.items():
            row = db.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
            if row is None or not isinstance(row[0], str) or " ".join(row[0].split()) != expected:
                return False
        return True
    except sqlite3.Error:
        return False


def install(db):
    """Called within the store's explicit migration transaction; no commits."""
    statement = ""
    for line in DDL.splitlines():
        statement += line + "\n"
        if sqlite3.complete_statement(statement):
            db.execute(statement)
            statement = ""
    if statement.strip():
        raise ValueError("incomplete_authoring_migration")
