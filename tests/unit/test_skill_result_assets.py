import base64
import io
import json
import sqlite3
import zlib

import numpy as np
import pytest

from app import config, skill_result_assets as assets
from app.skill_drafts import DraftError
from app.skill_store import SkillStore

SPEC = {'kind':'steady_complex_pressure', 'units':'numerical_source_normalization',
        'axis_order':['x','y'], 'grid_shape':[32,32], 'full_complex':True, 'downsample':1}


def response(field=None, raw=None):
    if raw is None:
        stream = io.BytesIO()
        np.save(stream, np.full((32,32), 3+4j, dtype=np.complex64) if field is None else field, allow_pickle=False)
        raw = stream.getvalue()
    payload = {'quantity':'complex_pressure','npy_zlib_base64':base64.b64encode(zlib.compress(raw)).decode()}
    return {'exit_code':0,'timed_out':False,'stdout':'__SKILL_PROBE_START__'+json.dumps(payload)+'__SKILL_PROBE_END__'}


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(config,'SKILL_AUTHORING_ENABLED',True)
    store = SkillStore(tmp_path/'skill.db')
    store.migrate_skill_authoring()
    return assets.ResultAssetStore(store,tmp_path/'assets')


def test_round_trip_and_consistent_display(library):
    field = assets.decode_executor_field(response(),SPEC)
    row = library.put_field(field,SPEC)
    assert row['role']=='result' and row['provenance']=='unreviewed'
    assert library.put_field(field,SPEC)==row
    np.testing.assert_array_equal(library.read_field(row['asset_sha256'],SPEC),field)
    view=library.present(row['asset_sha256'],SPEC)
    assert view['max_pressure']==5 and view['rms_pressure']==5
    assert np.array(view['amplitude']).shape==(32,32) and not view['grants_skill_trust']


@pytest.mark.parametrize('field',[np.zeros((32,32)),np.zeros((64,32),dtype=complex),
    np.full((32,32),complex(float('nan'),1)),np.full((32,32),complex(float('inf'),1))])
def test_invalid_fields(field):
    with pytest.raises(assets.AssetError): assets.decode_executor_field(response(field),SPEC)


@pytest.mark.parametrize('change',[{'axis_order':['y','x']},{'units':'Pa'},{'downsample':2},{'grid_shape':[32.0,32]}])
def test_contract(change):
    with pytest.raises(assets.AssetError): assets.decode_executor_field(response(),dict(SPEC,**change))


@pytest.mark.parametrize('raw',[b'bad',b'x'*(assets.MAX_ASSET_BYTES+1)],ids=['bad','bomb'])
def test_bad_or_bomb_payload(raw):
    with pytest.raises(assets.AssetError): assets.decode_executor_field(response(raw=raw),SPEC)


def test_stdout_and_trailing_compressed_data():
    bad=response(); bad['stdout']+='x'*assets.MAX_STDOUT_BYTES
    with pytest.raises(assets.AssetError): assets.decode_executor_field(bad,SPEC)
    bad=response(); payload=json.loads(bad['stdout'].split('__SKILL_PROBE_START__')[1].split('__SKILL_PROBE_END__')[0])
    payload['npy_zlib_base64']=base64.b64encode(base64.b64decode(payload['npy_zlib_base64'])+b'junk').decode()
    bad['stdout']='__SKILL_PROBE_START__'+json.dumps(payload)+'__SKILL_PROBE_END__'
    with pytest.raises(assets.AssetError): assets.decode_executor_field(bad,SPEC)


def test_digest_rechecked(library):
    row=library.ingest_executor(response(),SPEC)
    (library.root/row['storage_key']).write_bytes(b'corrupt')
    with pytest.raises(assets.AssetError): library.read_field(row['asset_sha256'],SPEC)


def test_paths_and_orphans(library,monkeypatch):
    with pytest.raises(assets.AssetError): library.read_field('../secret',SPEC)
    library.root.mkdir(exist_ok=True)
    (library.root/'.pending-orphan').write_bytes(b'x'*100)
    monkeypatch.setattr(assets,'MAX_TOTAL_BYTES',100)
    with pytest.raises(assets.AssetError,match='quota'): library.ingest_executor(response(),SPEC)
    with library.drafts._connection() as db:
        assert db.execute('SELECT count(*) FROM skill_validation_assets').fetchone()[0]==0


def test_write_failure_has_no_success_index(library,monkeypatch):
    def fail(*args): raise OSError('disk full')
    monkeypatch.setattr(assets.os,'replace',fail)
    with pytest.raises(DraftError): library.ingest_executor(response(),SPEC)
    with library.drafts._connection() as db:
        assert db.execute('SELECT count(*) FROM skill_validation_assets').fetchone()[0]==0
    assert list(library.root.iterdir())==[]


def test_reference_collision_never_grants_authority(library):
    row=library.ingest_executor(response(),SPEC)
    with library.drafts._connection(write=True) as db:
        db.execute("UPDATE skill_validation_assets SET role='reference',provenance='server_reviewed'")
    with pytest.raises(assets.AssetError,match='role_conflict'): library.ingest_executor(response(),SPEC)
    with pytest.raises(assets.AssetError): library.read_field(row['asset_sha256'],SPEC)


def test_disabled_no_directory_or_database(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'SKILL_AUTHORING_ENABLED',False)
    library=assets.ResultAssetStore(SkillStore(tmp_path/'absent.db'),tmp_path/'assets')
    with pytest.raises(DraftError): library.ingest_executor(response(),SPEC)
    assert not library.root.exists() and not (tmp_path/'absent.db').exists()


def test_manifest_binding(library):
    row=library.ingest_executor(response(),SPEC)
    manifest={key:'a'*64 for key in ('request_sha256','code_sha256','parameters_sha256','runtime_sha256',
              'content_sha256','case_fingerprint','receipt_sha256')}
    manifest.update(job_id='job-1',attempt_no=1,run_id='run-1',request_nonce='nonce',result_spec=SPEC,field_asset_sha256=row['asset_sha256'])
    receipt=library.put_manifest(manifest)
    assert receipt['role']=='evidence' and receipt['provenance']=='unreviewed'
    assert library.read_manifest(receipt['asset_sha256'])==manifest
    with pytest.raises(assets.AssetError): library.put_manifest(dict(manifest,secret='credential'))
    (library.root/receipt['storage_key']).write_bytes(b'{}')
    with pytest.raises(assets.AssetError): library.read_manifest(receipt['asset_sha256'])


def test_symlink_root_rejected(library,tmp_path):
    target=tmp_path/'target'; target.mkdir()
    try: library.root.symlink_to(target,target_is_directory=True)
    except OSError: pytest.skip('OS symlink privilege unavailable')
    with pytest.raises(assets.AssetError): library.ingest_executor(response(),SPEC)


def test_header_allocation_and_trailing_npy_are_rejected():
    stream=io.BytesIO()
    np.lib.format.write_array_header_1_0(stream,{'descr':'<c16','fortran_order':False,'shape':(10**12,10**12)})
    with pytest.raises(assets.AssetError): assets.decode_executor_field(response(raw=stream.getvalue()),SPEC)
    stream=io.BytesIO(); np.save(stream,np.zeros((32,32),dtype=np.complex64),allow_pickle=False)
    with pytest.raises(assets.AssetError): assets.decode_executor_field(response(raw=stream.getvalue()+b'extra'),SPEC)


def test_multiple_markers_and_unsuccessful_execution():
    bad=response(); bad['stdout']+=bad['stdout']
    with pytest.raises(assets.AssetError): assets.decode_executor_field(bad,SPEC)
    bad=response(); bad['exit_code']=False
    with pytest.raises(assets.AssetError): assets.decode_executor_field(bad,SPEC)
    bad=response(); bad['stdout']='\ud800'
    with pytest.raises(assets.AssetError): assets.decode_executor_field(bad,SPEC)


def test_large_finite_metrics_do_not_square_to_infinity(library):
    row=library.put_field(np.full((32,32),1e300+1e300j),SPEC)
    shown=library.present(row['asset_sha256'],SPEC)
    assert np.isfinite(shown['rms_pressure']) and shown['rms_pressure']==shown['max_pressure']

def test_audit_and_explicit_cleanup(library):
    import os
    import time
    row=library.ingest_executor(response(),SPEC)
    old=library.root/'old.tmp'; old.write_bytes(b'old')
    fresh=library.root/'fresh.tmp'; fresh.write_bytes(b'fresh')
    os.utime(old,(time.time()-90000,)*2)
    indexed=library.root/row['storage_key']
    os.utime(indexed,(time.time()-90000,)*2)
    audit=library.audit_assets()
    assert audit=={'missing':[],'corrupt':[],'orphan':['fresh.tmp','old.tmp']}
    assert library.cleanup_orphans()=={'deleted':['old.tmp']}
    assert fresh.exists() and indexed.exists()
    indexed.write_bytes(b'broken')
    assert library.audit_assets()['corrupt']==[indexed.name]
    indexed.unlink()
    assert library.audit_assets()['missing']==[indexed.name]


def test_zero_byte_orphans_cannot_bypass_scan_budget(library,monkeypatch):
    library.root.mkdir()
    monkeypatch.setattr(assets,'MAX_SCAN_ENTRIES',3)
    for i in range(4): (library.root/str(i)).touch()
    for method in (library.audit_assets,library.cleanup_orphans,lambda:library.ingest_executor(response(),SPEC)):
        with pytest.raises(assets.AssetError,match='asset_scan_budget'): method()
    assert len(list(library.root.iterdir()))==4


def test_read_rejects_tampered_provenance_and_manifest_schema(library):
    import hashlib
    row=library.ingest_executor(response(),SPEC)
    with library.drafts._connection(write=True) as db:
        db.execute("UPDATE skill_validation_assets SET provenance='server_reviewed'")
    with pytest.raises(assets.AssetError): library.read_field(row['asset_sha256'],SPEC)
    malformed={'secret':'not allowed'}
    raw=json.dumps(malformed).encode()
    asset=library._put(raw,{'kind':'execution_binding_v1'},'evidence','json')
    with pytest.raises(assets.AssetError,match='invalid_manifest'): library.read_manifest(asset['asset_sha256'])
    with library.drafts._connection(write=True) as db:
        db.execute("UPDATE skill_validation_assets SET metadata_json='{}' WHERE asset_sha256=?",(asset['asset_sha256'],))
    with pytest.raises(assets.AssetError): library.read_manifest(asset['asset_sha256'])
