"""Bounded internal complex field assets; never awards reference or skill trust."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid
import zlib

import numpy as np

from app.skill_drafts import DraftStore

MAX_ASSET_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_STDOUT_BYTES = 1024 * 1024
MAX_SCAN_ENTRIES = 4096
ORPHAN_GRACE_SECONDS = 24 * 60 * 60


class AssetError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _contract(spec):
    if (type(spec) is not dict or set(spec) != {'kind', 'units', 'axis_order', 'grid_shape', 'full_complex', 'downsample'}
            or spec['kind'] != 'steady_complex_pressure' or spec['units'] != 'numerical_source_normalization'
            or spec['axis_order'] != ['x', 'y'] or spec['full_complex'] is not True
            or type(spec['downsample']) is not int or spec['downsample'] != 1):
        raise AssetError('invalid_result_contract')
    shape = spec['grid_shape']
    if type(shape) is not list or len(shape) != 2 or any(type(x) is not int or not 32 <= x <= 1024 for x in shape):
        raise AssetError('invalid_result_shape')
    return tuple(shape)


def _field(field, spec):
    shape = _contract(spec)
    if (not isinstance(field, np.ndarray) or field.shape != shape
            or field.dtype not in (np.dtype('complex64'), np.dtype('complex128'))
            or field.nbytes > MAX_ASSET_BYTES or not np.isfinite(field).all()):
        raise AssetError('invalid_complex_field')
    return field


def _load(raw, spec):
    if len(raw) > MAX_ASSET_BYTES:
        raise AssetError('asset_too_large')
    try:
        stream = io.BytesIO(raw)
        # Check header BEFORE numpy allocates: adversarial shapes must not allocate.
        version = np.lib.format.read_magic(stream)
        if version == (1, 0):
            shape, fortran, dtype = np.lib.format.read_array_header_1_0(stream)
        elif version == (2, 0):
            shape, fortran, dtype = np.lib.format.read_array_header_2_0(stream)
        else:
            raise ValueError('version')
        if shape != _contract(spec) or dtype not in (np.dtype('complex64'), np.dtype('complex128')):
            raise ValueError('shape or dtype')
        expected = int(np.prod(shape)) * dtype.itemsize
        if expected > MAX_ASSET_BYTES or len(raw) - stream.tell() != expected:
            raise ValueError('size')
        return _field(np.load(io.BytesIO(raw), allow_pickle=False), spec)
    except (ValueError, EOFError, TypeError, OverflowError) as exc:
        raise AssetError('invalid_npy') from exc


def decode_executor_field(response, result_spec):
    """Parse untrusted output only; caller must separately verify executor receipt."""
    _contract(result_spec)
    if (type(response) is not dict or type(response.get('exit_code')) is not int
            or response['exit_code'] != 0 or response.get('timed_out') is not False):
        raise AssetError('execution_failed')
    stdout = response.get('stdout')
    try:
        bounded = type(stdout) is str and len(stdout.encode('utf-8')) <= MAX_STDOUT_BYTES
    except UnicodeError:
        bounded = False
    if not bounded:
        raise AssetError('stdout_budget')
    start, end = '__SKILL_PROBE_START__', '__SKILL_PROBE_END__'
    if stdout.count(start) != 1 or stdout.count(end) != 1:
        raise AssetError('one_full_field_required')
    try:
        payload = json.loads(stdout.split(start, 1)[1].split(end, 1)[0])
        if payload.get('quantity') != 'complex_pressure':
            raise ValueError('quantity')
        compressed = base64.b64decode(payload['npy_zlib_base64'], validate=True)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, MAX_ASSET_BYTES + 1)
        if len(raw) > MAX_ASSET_BYTES or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('compressed budget')
    except (ValueError, KeyError, TypeError, AttributeError, zlib.error) as exc:
        raise AssetError('invalid_field_payload') from exc
    return _load(raw, result_spec)


class ResultAssetStore:
    """Server-selected dedicated root. All writers serialize on the schema18 DB.

    No user path is accepted. Existing references are never converted to results.
    Orphans count against quota and are retained for operator reconciliation.
    """
    def __init__(self, store, root):
        self.drafts = DraftStore(store)
        self.root = Path(root).absolute()

    def _root(self):
        # Refuse symlinks in every existing component, including parent roots.
        for path in [self.root, *self.root.parents]:
            if path.is_symlink():
                raise AssetError('unsafe_asset_path')
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise AssetError('unsafe_asset_path')

    def _path(self, key):
        if type(key) is not str or re.fullmatch(r'[0-9a-f]{64}\.(?:npy|json)', key) is None:
            raise AssetError('unsafe_asset_key')
        path = self.root / key
        if path.is_symlink():
            raise AssetError('unsafe_asset_path')
        return path

    def _scan(self):
        # Flat dedicated root: reject directories instead of recursively traversing.
        entries = {}
        with os.scandir(self.root) as iterator:
            for entry in iterator:
                if len(entries) >= MAX_SCAN_ENTRIES:
                    raise AssetError('asset_scan_budget')
                info = entry.stat(follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    raise AssetError('unsafe_asset_path')
                entries[entry.name] = info
        return entries

    def _usage(self):
        entries = self._scan()
        # One slot remains necessary for atomic temporary writes.
        if len(entries) >= MAX_SCAN_ENTRIES:
            raise AssetError('asset_scan_budget')
        return sum(info.st_size for info in entries.values())

    def _bytes(self, path):
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ASSET_BYTES:
                raise AssetError('invalid_asset_file')
            return stream.read(MAX_ASSET_BYTES + 1)

    def put_field(self, field, result_spec):
        _field(field, result_spec)
        buffer = io.BytesIO()
        np.save(buffer, field, allow_pickle=False)
        raw = buffer.getvalue()
        if len(raw) > MAX_ASSET_BYTES:
            raise AssetError('asset_too_large')
        return self._put(raw, {'result_spec': result_spec, 'dtype': str(field.dtype)}, 'result', 'npy')

    def _put(self, raw, metadata, role, suffix):
        digest = hashlib.sha256(raw).hexdigest()
        key = digest + '.' + suffix
        temporary = None
        with self.drafts._connection(write=True) as db:
            self._root()
            path = self._path(key)
            old = db.execute('SELECT * FROM skill_validation_assets WHERE asset_sha256=?', (digest,)).fetchone()
            if old is not None:
                if (old['role'] != role or old['provenance'] != 'unreviewed'
                        or old['storage_key'] != key or old['metadata_json'] != json.dumps(metadata, sort_keys=True)):
                    raise AssetError('asset_role_conflict')
                if self._bytes(path) != raw:
                    raise AssetError('asset_digest_mismatch')
                return dict(old)
            # Reserve temporary bytes even if the final path is already an orphan.
            if self._usage() + len(raw) > MAX_TOTAL_BYTES:
                raise AssetError('asset_quota')
            try:
                temporary = self.root / ('.pending-' + uuid.uuid4().hex)
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                if hashlib.sha256(self._bytes(temporary)).hexdigest() != digest:
                    raise AssetError('asset_digest_mismatch')
                if path.exists() and self._bytes(path) != raw:
                    raise AssetError('asset_digest_mismatch')
                os.replace(temporary, path)
                temporary = None
                db.execute('INSERT INTO skill_validation_assets VALUES(?,?,?,?,?,?,?,?)',
                           (digest, key, len(raw), 'application/x-npy' if suffix == 'npy' else 'application/json', json.dumps(metadata, sort_keys=True),
                            role, 'unreviewed', int(time.time() * 1000)))
                return dict(db.execute('SELECT * FROM skill_validation_assets WHERE asset_sha256=?', (digest,)).fetchone())
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def ingest_executor(self, response, result_spec):
        return self.put_field(decode_executor_field(response, result_spec), result_spec)

    def read_field(self, digest, result_spec):
        _contract(result_spec)
        with self.drafts._connection() as db:
            self._root()
            path = self._path(digest + '.npy' if type(digest) is str else None)
            row = db.execute('SELECT * FROM skill_validation_assets WHERE asset_sha256=?', (digest,)).fetchone()
            if row is None:
                raise AssetError('asset_not_found')
            row = dict(row)
        if (row['storage_key'] != path.name or row['role'] != 'result'
                or row['provenance'] != 'unreviewed' or row['media_type'] != 'application/x-npy'):
            raise AssetError('asset_contract_mismatch')
        try:
            metadata = json.loads(row['metadata_json'])
            if (type(metadata) is not dict or set(metadata) != {'result_spec', 'dtype'}
                    or metadata['result_spec'] != result_spec or metadata['dtype'] not in ('complex64', 'complex128')):
                raise ValueError('metadata')
        except (ValueError, TypeError, RecursionError):
            raise AssetError('asset_contract_mismatch') from None
        raw = self._bytes(path)
        if len(raw) != row['byte_count'] or hashlib.sha256(raw).hexdigest() != digest:
            raise AssetError('asset_digest_mismatch')
        field = _load(raw, result_spec)
        if str(field.dtype) != metadata['dtype']:
            raise AssetError('asset_contract_mismatch')
        return field

    def present(self, digest, result_spec):
        field = self.read_field(digest, result_spec)
        amplitude = np.abs(field.astype(np.complex128))
        if not np.isfinite(amplitude).all():
            raise AssetError('magnitude_overflow')
        peak = float(amplitude.max())
        rms = peak * float(np.sqrt(np.mean((amplitude / peak) ** 2))) if peak else 0.0
        return {'asset_sha256': digest, 'axis_order': ['x', 'y'], 'units': result_spec['units'],
                'shape': list(field.shape), 'amplitude': amplitude.tolist(),
                'max_pressure': peak,
                'rms_pressure': rms, 'grants_skill_trust': False}

    @staticmethod
    def _validate_manifest(manifest):
        """Restricted server binding only: no arbitrary text, credentials or claims."""
        allowed = {'job_id', 'attempt_no', 'run_id', 'request_nonce', 'request_sha256',
                   'code_sha256', 'parameters_sha256', 'runtime_sha256', 'content_sha256',
                   'case_fingerprint', 'result_spec', 'field_asset_sha256', 'receipt_sha256'}
        if type(manifest) is not dict or set(manifest) != allowed:
            raise AssetError('invalid_manifest')
        _contract(manifest['result_spec'])
        for key, value in manifest.items():
            if key == 'result_spec':
                continue
            if key == 'attempt_no':
                if type(value) is not int or not 1 <= value <= 2**31-1:
                    raise AssetError('invalid_manifest')
            elif key.endswith('sha256') or key == 'case_fingerprint':
                if type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None:
                    raise AssetError('invalid_manifest')
            elif type(value) is not str or re.fullmatch('[a-zA-Z0-9_-]{1,128}', value) is None:
                raise AssetError('invalid_manifest')
    def put_manifest(self, manifest):
        self._validate_manifest(manifest)
        # Require a real intact result before referencing it; no trust promotion.
        self.read_field(manifest['field_asset_sha256'], manifest['result_spec'])
        raw = json.dumps(manifest, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        if len(raw) > MAX_ASSET_BYTES:
            raise AssetError('asset_too_large')
        return self._put(raw, {'kind': 'execution_binding_v1'}, 'evidence', 'json')

    def read_manifest(self, digest):
        with self.drafts._connection() as db:
            self._root()
            path = self._path(digest + '.json' if type(digest) is str else None)
            row = db.execute('SELECT * FROM skill_validation_assets WHERE asset_sha256=?', (digest,)).fetchone()
            if (row is None or row['role'] != 'evidence' or row['provenance'] != 'unreviewed' or row['storage_key'] != path.name
                    or row['media_type'] != 'application/json'
                    or row['metadata_json'] != json.dumps({'kind': 'execution_binding_v1'}, sort_keys=True)):
                raise AssetError('asset_not_found')
            raw = self._bytes(path)
            if len(raw) != row['byte_count'] or hashlib.sha256(raw).hexdigest() != digest:
                raise AssetError('asset_digest_mismatch')
        try:
            manifest = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise AssetError('invalid_manifest') from None
        self._validate_manifest(manifest)
        self.read_field(manifest['field_asset_sha256'], manifest['result_spec'])
        return manifest

    def audit_assets(self):
        """Read-only bounded reconciliation; returns only relative storage keys."""
        with self.drafts._connection() as db:
            # No directory creation during audit.
            if not self.root.exists():
                entries = {}
            else:
                self._root()
                entries = self._scan()
            rows = db.execute('SELECT * FROM skill_validation_assets LIMIT ?',
                              (MAX_SCAN_ENTRIES + 1,)).fetchall()
            if len(rows) > MAX_SCAN_ENTRIES:
                raise AssetError('asset_scan_budget')
        missing, corrupt, indexed = [], [], set()
        for row in rows:
            key = row['storage_key']
            self._path(key)  # never expose an invalid indexed path
            indexed.add(key)
            if key not in entries:
                missing.append(key)
                continue
            try:
                raw = self._bytes(self.root / key)
                if len(raw) != row['byte_count'] or hashlib.sha256(raw).hexdigest() != row['asset_sha256']:
                    corrupt.append(key)
            except (OSError, AssetError):
                corrupt.append(key)
        return {'missing': sorted(missing), 'corrupt': sorted(corrupt),
                'orphan': sorted(set(entries) - indexed)}

    def cleanup_orphans(self):
        """Explicit operator action; never removes any indexed asset."""
        deleted = []
        with self.drafts._connection(write=True) as db:
            self._root()
            entries = self._scan()
            rows = db.execute('SELECT storage_key FROM skill_validation_assets LIMIT ?',
                              (MAX_SCAN_ENTRIES + 1,)).fetchall()
            if len(rows) > MAX_SCAN_ENTRIES:
                raise AssetError('asset_scan_budget')
            indexed = {row[0] for row in rows}
            cutoff = time.time() - ORPHAN_GRACE_SECONDS
            for key, info in entries.items():
                if key in indexed or info.st_mtime > cutoff:
                    continue
                path = self.root / key
                current = path.lstat()
                if (not stat.S_ISREG(current.st_mode) or (info.st_ino and current.st_ino != info.st_ino)
                        or current.st_size != info.st_size or current.st_mtime != info.st_mtime or current.st_mtime > cutoff):
                    continue
                path.unlink()
                deleted.append(key)
        return {'deleted': sorted(deleted)}

