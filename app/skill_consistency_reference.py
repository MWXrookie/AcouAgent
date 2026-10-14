"""Dormant exact-profile policy contract; no positive authorization available.

An admission receipt is not reuse permission. A separate owner-signed policy
binds the full four-case source and exact version/parameters/validator manifest.
Physical acceptance is incomplete; admission.verify_admitted_connection always
refuses. Owner signatures and consistency alone cannot register or consume code.
Public search remains metadata only; code is returned after a one-use commit.
No keys, network, execution, arbitrary source code, migrations or promotion.
"""
import hashlib
import json
import re
import sqlite3
import time
import uuid
from contextlib import nullcontext
from app import skill_consistency_source as source,skill_consistency_admission as admission
from app import skill_generation_decision as decisions,skill_library_service as service
from app import skill_retrieval,skill_deadline as deadline
from scripts.validation.host_forward_signature import canonical,unique,reject

FORMAT="consistency-reference.v1"
FIELDS=source.ROOT_FIELDS-{"action"}|{
    "skill_id","skill_version","batch_id","source_sha256","parameters_sha256","scenario_type"}
PROOF_FIELDS={"format","skill_id","version","batch_id","records","approval_document"}
REASON="periodic_consistency_reference_handoff"
MAX_PROOF_BYTES=2400000
ERRORS=(ValueError,TypeError,KeyError,IndexError,UnicodeError,OverflowError,RecursionError,
        sqlite3.Error,OSError,ImportError)


def policy_payload(*,skill_id,version,batch_id,records,revision,valid_from_ns,valid_until_ns):
    """Canonical statement for explicit operator review/signing, no approval."""
    sources={}
    for case in source.numerical.PROFILES:
        record=records[case];payload=source._observation(record["observation_document"])
        sources[case]=dict(run_id=payload["observation"]["run_id"],nonce=payload["observation"]["nonce"],
            observation_sha256=hashlib.sha256(canonical(payload)).hexdigest(),
            stdout_sha256=hashlib.sha256(record["stdout"].encode()).hexdigest())
    return dict(purpose="approve_consistency_reference_policy",root_id=source.ROOT_ID,
        profile=source.PROFILE,revision=revision,valid_from_ns=valid_from_ns,valid_until_ns=valid_until_ns,
        manifest_sha256=source.manifest()["sha256"],skill_id=skill_id,skill_version=version,
        batch_id=batch_id,source_sha256=hashlib.sha256(canonical(sources)).hexdigest(),
        parameters_sha256=hashlib.sha256(canonical(admission.parameters())).hexdigest(),
        scenario_type="initial_pressure_2d")


def verify_connection(db,proof,now):
    """No owner policy can substitute for missing physical acceptance."""
    raise ValueError("independent_complete_physical_acceptance_required")


def register(proof):
    return dict(recorded=False,reason="independent_complete_physical_acceptance_required")


def consume(*,decision_id,nonce,generation_run_id,scenario_type,parameters_json,original_context,skill_id,version):
    return dict(decisions.FALSE_TRUST,context=original_context,skills=[],fallback=True,
        consumed=False,reason="independent_complete_physical_acceptance_required")


def read_committed_context(*,decision_id,generation_run_id,scenario_type,parameters_json,original_context,_db=None):
    return original_context
