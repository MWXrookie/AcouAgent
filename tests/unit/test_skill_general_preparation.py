"""Pinned reference parsing and preparation fail closed; no external network in tests."""
import hashlib
import io
import struct
import zlib

import numpy as np
import pytest

from scripts.skill_library import prepare_general as setup


@pytest.fixture
def pinned(monkeypatch):
    field = np.asfortranarray((np.arange(16384, dtype=np.float32).reshape(128, 128) + 1j).astype(np.complex64))
    prefix = (struct.pack("<IIIIIIIIII", 14, 131136, 6, 8, 0x807, 0, 5, 8, 128, 128)
              + struct.pack("<II", 1, 7) + b"p_final\0" + struct.pack("<II", 7, 65536))
    matrix = prefix + field.real.astype('<f4').tobytes(order='F') + struct.pack('<II',7,65536) + field.imag.astype('<f4').tobytes(order='F')
    header = b' ' * 124 + b'\x00\x01IM'
    def raw(data):
        compressed = zlib.compress(data)
        result = header + struct.pack('<II',15,len(compressed)) + compressed
        monkeypatch.setitem(setup.MAT,'B01',('synthetic',hashlib.sha256(result).hexdigest()))
        return result
    stream = io.BytesIO();np.save(stream,field,allow_pickle=False)
    monkeypatch.setitem(setup.REFERENCES,'B01',hashlib.sha256(stream.getvalue()).hexdigest())
    return field,matrix,raw


def test_restricted_conversion_preserves_full_complex_fortran_bytes(pinned):
    field,matrix,raw=pinned
    result=setup.convert_pinned_mat('B01',raw(matrix))
    array=np.load(io.BytesIO(result),allow_pickle=False)
    assert np.array_equal(field,array) and array.flags.f_contiguous and array.dtype==np.complex64


@pytest.mark.parametrize('kind',['digest','name','shape','dtype','extra','bomb','truncated'])
def test_changed_or_unsupported_asset_rejected_before_any_install(pinned,kind):
    _,matrix,raw=pinned
    if kind=='name':matrix=matrix[:48]+b'not_ref\0'+matrix[56:]
    elif kind=='shape':matrix=matrix[:32]+struct.pack('<II',64,256)+matrix[40:]
    elif kind=='dtype':matrix=matrix[:56]+struct.pack('<II',9,65536)+matrix[64:]
    elif kind=='extra':matrix+=b'extra'
    elif kind=='bomb':matrix=b'\0'*3_000_000
    elif kind=='truncated':matrix=matrix[:-1]
    value=raw(matrix)
    if kind=='digest':value=value[:-1]+bytes([value[-1]^1])
    with pytest.raises(ValueError):setup.convert_pinned_mat('B01',value)


def test_no_reference_digest_self_attestation(pinned,monkeypatch):
    _,matrix,raw=pinned
    monkeypatch.setitem(setup.REFERENCES,'B01','f'*64)
    with pytest.raises(ValueError,match='reference_npy_digest_mismatch'):setup.convert_pinned_mat('B01',raw(matrix))


def test_disabled_prepare_has_no_filesystem_or_network_side_effect(tmp_path,monkeypatch):
    monkeypatch.setattr(setup.config,'SKILL_AUTHORING_ENABLED',False)
    monkeypatch.setattr(setup,'download_reference',lambda case:pytest.fail('must not download'))
    with pytest.raises(ValueError,match='explicit_preparation_flags_required'):
        setup.prepare(backup_dir=tmp_path/'backup',download=True)
    assert not list(tmp_path.iterdir())


def test_backup_integrity_and_missing_database_no_create(tmp_path):
    import sqlite3
    missing=tmp_path/'missing.db';destination=tmp_path/'copy.db'
    assert setup.backup_existing(missing,destination) is False
    assert not missing.exists() and not destination.exists()
    with sqlite3.connect(missing) as db:db.execute('CREATE TABLE example(value)');db.execute('INSERT INTO example VALUES(42)')
    assert setup.backup_existing(missing,destination)
    with sqlite3.connect(destination) as db:assert db.execute('SELECT value FROM example').fetchone()[0]==42


def test_redirects_not_followed():
    with pytest.raises(ValueError,match='reference_redirect_rejected'):
        setup._NoRedirect().redirect_request(None,None,None,None,None,None)
