import json
import pytest
from scripts.validation import host_observation_contract as contract


@pytest.fixture
def context():
    return {"run_id":"run-1","request_nonce":"a"*32,"container_id":"b"*64,
            "image_id":"sha256:"+"c"*64,"container_started_ns":1,
            "window_start_ns":10_000_000_000,"window_end_ns":20_000_000_000}


def document(context):
    return {**context,"version":contract.VERSION,
            "observed_before_ns":9_000_000_000,"observed_after_ns":21_000_000_000}


def check(context, doc, now=22_000_000_000):
    return contract.inspect_observation(json.dumps(doc), expected=context, now_ns=now)


def test_matching_forged_document_is_still_not_authenticated(context):
    result = check(context, document(context))
    assert result["format_matched"] and result["context_matched"]
    for flag in ("authenticated","trusted_run","runtime_approved","run_bound","parameters_bound"):
        assert result[flag] is False


@pytest.mark.parametrize("key,value", [
    ("run_id","run-2"),("request_nonce","d"*32),("container_id","e"*64),
    ("image_id","sha256:"+"f"*64),("container_started_ns",2),
    ("window_start_ns",11_000_000_000),("window_end_ns",19_000_000_000)])
def test_replayed_other_context_rejected(context, key, value):
    doc=document(context);doc[key]=value
    result=check(context,doc)
    assert not result["context_matched"] and result["reason"]=="dispatch_context_mismatch"


@pytest.mark.parametrize("key,value", [
    ("authenticated",True),("request_nonce",True),("container_id","short"),
    ("image_id","latest"),("observed_before_ns",True),
    ("observed_after_ns",21_000_000_000.0),("window_start_ns",-1),
    ("container_started_ns",30_000_000_000),("version","unknown")])
def test_malformed_or_self_authorizing_document_rejected(context,key,value):
    doc=document(context);doc[key]=value
    assert not check(context,doc)["context_matched"]


def test_stale_future_and_non_enclosing_window_rejected(context):
    assert check(context,document(context),400_000_000_000)["reason"]=="stale_observation"
    assert check(context,document(context),20_000_000_000)["reason"]=="invalid_observation_window"
    doc=document(context);doc["observed_before_ns"]=11_000_000_000
    assert check(context,doc)["reason"]=="invalid_observation_window"


@pytest.mark.parametrize("raw", ['{"version":1,"version":2}', '{"value":NaN}', b"\xff",
                                  "["*1500, "x"*8193, "汉"*3000, None])
def test_untrusted_parser_is_bounded_and_never_raises(context,raw):
    assert not contract.inspect_observation(raw,expected=context,now_ns=22_000_000_000)["context_matched"]


def test_invalid_expected_context_and_clock_fail_closed(context):
    doc=json.dumps(document(context))
    for expected in ({}, {**context,"run_id":"../escape"}, {**context,"window_end_ns":True}):
        assert not contract.inspect_observation(doc,expected=expected,now_ns=22_000_000_000)["context_matched"]
    assert not contract.inspect_observation(doc,expected=context,now_ns=True)["context_matched"]


def test_snapshot_adapter_preserves_nanoseconds_without_authenticating_request(context):
    context = {**context,"container_started_ns":7_000_000_001}
    before={"container_id":"b"*64,"image_id":"sha256:"+"c"*64,"running":True,
            "started_at":"1970-01-01T00:00:07.000000001Z","restart_count":0}
    result=contract.inspect_snapshot_pair(before,dict(before),expected=context,
        observed_before_ns=9_000_000_000,observed_after_ns=21_000_000_000,now_ns=22_000_000_000)
    assert result["context_matched"] and not result["authenticated"] and not result["request_observed"]
    wrong={**context,"container_started_ns":7_000_000_000}
    result=contract.inspect_snapshot_pair(before,dict(before),expected=wrong,
        observed_before_ns=9_000_000_000,observed_after_ns=21_000_000_000,now_ns=22_000_000_000)
    assert not result["context_matched"]


@pytest.mark.parametrize("change", [
    {"restart_count":1},{"container_id":"d"*64},{"image_id":"sha256:"+"e"*64},
    {"started_at":"1970-01-01T00:00:08Z"},{"running":False},{"env":{"SECRET":"fixture"}}])
def test_snapshot_drift_or_extra_sensitive_fields_rejected(context,change):
    before={"container_id":"b"*64,"image_id":"sha256:"+"c"*64,"running":True,
            "started_at":"1970-01-01T00:00:01Z","restart_count":0}
    result=contract.inspect_snapshot_pair(before,{**before,**change},expected=context,
        observed_before_ns=9_000_000_000,observed_after_ns=21_000_000_000,now_ns=22_000_000_000)
    assert not result["context_matched"] and not result["authenticated"]
