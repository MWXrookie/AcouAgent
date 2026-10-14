"""Fixed-path skill administration. Not automatic learning or activation."""
import secrets
from pathlib import Path

from app import config
from app.skill_store import SkillStore

STORE = SkillStore(Path("/app/data/skill_store.db"))


def authorize(token):
    expected = config.PORTAL_ADMIN_TOKEN
    if (not isinstance(expected, str) or len(expected) < 32
            or not isinstance(token, str)
            or not secrets.compare_digest(token.encode(), expected.encode())):
        raise PermissionError("skill_library_admin_required")


def archive(token, skill_id, *, archived, expected_updated_at, reason):
    authorize(token)
    return STORE.set_archived(skill_id, archived=archived, expected_updated_at=expected_updated_at,
                              actor="portal-admin", reason=reason)


def negative_feedback(token, skill_id, *, run_id, reason):
    authorize(token)
    return STORE.record_negative_feedback(skill_id, run_id, actor="portal-admin", reason=reason)


def feedback_statement(token, skill_id, *, run_id, confirmed, request_id, reason):
    authorize(token)
    return STORE.record_feedback_statement(skill_id, run_id, confirmed=confirmed,
        request_id=request_id, actor="portal-admin", reason=reason)


def request_revalidation(token,skill_id,*,expected_updated_at,expected_event_id,reason):
    authorize(token)
    return STORE.request_revalidation(skill_id,expected_updated_at=expected_updated_at,
        expected_event_id=expected_event_id,actor="portal-admin",reason=reason)
