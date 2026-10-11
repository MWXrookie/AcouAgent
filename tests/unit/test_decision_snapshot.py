import sqlite3
from app.skill_store import SkillStore
from tests.unit.test_skill_store import candidate,bounded_candidate,set_test_status

def test_existing_snapshot_is_reused_and_new_snapshot_sees_withdrawal(tmp_path):
    store=SkillStore(tmp_path/"skills.db")
    parent=candidate(store);child=bounded_candidate(store,reference_skill_ids=[parent])
    for skill in (parent,child):set_test_status(store,skill,"active")
    with store._read_db() as db:
        assert store._inspect_derivation_connection(db,child)["references_current"]
        set_test_status(store,parent,"questioned")
        assert store._inspect_derivation_connection(db,child)["references_current"]
        assert db.in_transaction
    assert not store.inspect_derivation(child)["references_current"]

def test_unstarted_transaction_is_rejected_without_commit(tmp_path):
    store=SkillStore(tmp_path/"skills.db");skill=candidate(store)
    with sqlite3.connect(store.path) as db:
        db.row_factory=sqlite3.Row
        result=store._inspect_derivation_connection(db,skill)
        assert result["reason"]=="decision_transaction_required"
        assert not result["trusted_run"] and not db.in_transaction

def test_existing_write_transaction_is_not_committed_by_derivation_check(tmp_path):
    store=SkillStore(tmp_path/"skills.db");skill=candidate(store);set_test_status(store,skill,"active")
    with sqlite3.connect(store.path) as db:
        db.row_factory=sqlite3.Row;db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE simulation_skills SET status='questioned' WHERE skill_id=?",(skill,))
        assert not store._inspect_derivation_connection(db,skill)["references_current"]
        assert db.in_transaction
        db.rollback()
    assert store.inspect_derivation(skill)["references_current"]
