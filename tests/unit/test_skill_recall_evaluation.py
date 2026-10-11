import json
import pytest
from scripts.validation.skill_recall_evaluation import evaluate
def dataset():
    return dict(version="skill-recall.v1",annotation_status="operator_declared",queries=[
        dict(id="q"+str(i),scenario_type="plane_wave_2d",parameters={"frequency_hz":100000+i},
            relevant_skill_ids=["skill-one"] if i<20 else []) for i in range(30)])
def search(scene,params,limit):
    value=json.loads(params)["frequency_hz"]
    return dict(skills=[{"skill_id":"skill-one"}] if value<100020 else [],reason=
        "fixed_profile_metadata_only" if value<100020 else "no_applicable_skill")
def test_actual_top3_and_false_result_metrics_without_dataset_certification():
    got=evaluate(json.dumps(dataset()),search)
    assert got["metric_targets_met"] and got["top3_hit_rate"]==1 and got["false_result_query_rate"]==0
    assert not got["annotation_truth_authenticated"] and not got["overall_contract_passed"]
def test_wrong_results_and_disabled_retrieval_do_not_pass():
    wrong=evaluate(json.dumps(dataset()),lambda *_:dict(skills=[{"skill_id":"wrong"}],reason="fixed_profile_metadata_only"))
    assert wrong["top3_hit_rate"]==0 and wrong["false_result_query_rate"]==1 and not wrong["metric_targets_met"]
    off=evaluate(json.dumps(dataset()),lambda *_:dict(skills=[],reason="retrieval_disabled"))
    assert not off["operational"] and not off["metric_targets_met"]
@pytest.mark.parametrize("fault",["short","duplicate","same_query","all_positive","all_negative","trust","unreviewed"])
def test_missing_or_padded_annotation_refused(fault):
    data=dataset()
    if fault=="short":data["queries"].pop()
    if fault=="duplicate":data["queries"][1]["id"]="q0"
    if fault=="same_query":data["queries"][1]["parameters"]=data["queries"][0]["parameters"]
    if fault=="all_positive":
        for q in data["queries"]:q["relevant_skill_ids"]=["skill-one"]
    if fault=="all_negative":
        for q in data["queries"]:q["relevant_skill_ids"]=[]
    if fault=="trust":data["queries"][0]["trusted_run"]=True
    if fault=="unreviewed":data["annotation_status"]="authenticated"
    with pytest.raises(ValueError):evaluate(json.dumps(data),search)


@pytest.mark.parametrize("change",["none","commit","rollback","replace","retarget","restore"])
def test_configured_evaluation_rejects_mixed_database_state(tmp_path,monkeypatch,change):
    import sqlite3
    from app import config,skill_library_service,skill_retrieval
    from app.skill_store import SkillStore
    store=SkillStore(tmp_path/"skills.db")
    store.migrate()
    from tests.unit.test_skill_store import candidate
    sid=candidate(store)
    monkeypatch.setattr(skill_library_service,"STORE",store)
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",True)
    monkeypatch.setattr(config,"SKILL_RETRIEVAL_ENABLED",True)
    calls=[]
    def actual(scene,params,limit):
        calls.append(params)
        if len(calls)==10:
            if change in {"commit","rollback","restore"}:
                with store._db_existing_write() as db:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("UPDATE simulation_skills SET status='questioned' WHERE skill_id=?",(sid,))
                    db.execute("INSERT INTO skill_events(skill_id,event_type,detail,created_at) VALUES (?,'evaluation_change','{}','test')",(sid,))
                    if change=="rollback":db.rollback()
            if change=="retarget":
                other_store=SkillStore(tmp_path/"retargeted.db")
                other_store.migrate()
                monkeypatch.setattr(skill_library_service,"STORE",other_store)
            if change=="replace":
                replacement=tmp_path/"replacement.db"
                other=sqlite3.connect(replacement)
                other.execute("CREATE TABLE replacement_marker(id INTEGER)")
                other.commit();other.close()
                replacement.replace(store.path)
        if len(calls)==20 and change=="restore":
            with store._db_existing_write() as db:
                db.execute("UPDATE simulation_skills SET status='candidate' WHERE skill_id=?",(sid,))
        return search(scene,params,limit)
    monkeypatch.setattr(skill_retrieval,"search",actual)
    got=evaluate(json.dumps(dataset()))
    assert len(got["rows"])==30 and got["top3_hit_rate"]==1
    assert got["source_kind"]=="configured_readonly_search"
    assert got["store_consistency"]==("changed" if change in {"commit","replace","retarget","restore"} else "unchanged")
    assert got["metric_targets_met"]==(change in {"none","rollback"})
    assert not got["overall_contract_passed"]
    if change=="restore":assert store.get_skill(sid)["status"]=="candidate"

def test_disabled_configured_evaluation_never_opens_store(tmp_path,monkeypatch):
    from app import config,skill_library_service,skill_retrieval
    from app.skill_store import SkillStore
    path=tmp_path/"missing.db"
    monkeypatch.setattr(skill_library_service,"STORE",SkillStore(path))
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",False)
    monkeypatch.setattr(config,"SKILL_RETRIEVAL_ENABLED",True)
    monkeypatch.setattr(skill_retrieval,"search",search)
    got=evaluate(json.dumps(dataset()))
    assert got["store_consistency"]=="disabled" and not got["metric_targets_met"]
    assert not path.exists()

def test_missing_configured_database_is_not_created_or_scored_as_accepted(tmp_path,monkeypatch):
    from app import config,skill_library_service,skill_retrieval
    from app.skill_store import SkillStore
    path=tmp_path/"missing.db"
    monkeypatch.setattr(skill_library_service,"STORE",SkillStore(path))
    monkeypatch.setattr(config,"SKILL_LIBRARY_ENABLED",True)
    monkeypatch.setattr(config,"SKILL_RETRIEVAL_ENABLED",True)
    monkeypatch.setattr(skill_retrieval,"search",search)
    got=evaluate(json.dumps(dataset()))
    assert got["store_consistency"]=="unavailable" and not got["metric_targets_met"]
    assert len(got["rows"])==30 and not path.exists()


def test_dataset_reader_regular_utf8_and_nonblocking_fifo(tmp_path):
    import os
    from scripts.validation.skill_recall_evaluation import read_dataset
    path=tmp_path/"annotated.json"
    path.write_text(json.dumps(dataset(),ensure_ascii=False),encoding="utf-8")
    assert json.loads(read_dataset(path))==dataset()
    fifo=tmp_path/"unopened.pipe"
    os.mkfifo(fifo)
    with pytest.raises(ValueError,match="regular_file"):read_dataset(fifo)
    with pytest.raises(ValueError,match="regular_file"):read_dataset(tmp_path)

@pytest.mark.parametrize("fault",["too_large","grown","modified","invalid_utf8"])
def test_dataset_reader_refuses_overflow_and_mid_read_changes(tmp_path,monkeypatch,fault):
    import os
    from scripts.validation.skill_recall_evaluation import read_dataset
    path=tmp_path/"annotated.json"
    path.write_bytes(b"x"*200001 if fault=="too_large" else b"\xff" if fault=="invalid_utf8" else b"original")
    if fault in {"grown","modified"}:
        original_read=os.read
        original_stat=path.stat()
        def changing_read(fd,size):
            raw=original_read(fd,size)
            current=os.fstat(fd)
            if (current.st_dev,current.st_ino)!=(original_stat.st_dev,original_stat.st_ino):return raw
            if fault=="grown":
                with path.open("ab") as writer:writer.write(b"x")
            else:
                with path.open("wb") as writer:writer.write(b"modified")
            return raw
        monkeypatch.setattr(os,"read",changing_read)
    with pytest.raises((ValueError,UnicodeError)):read_dataset(path)
