"""Bounded task declarations and single-use confirmation drafts.

Parsing preserves original source spans. No LaTeX evaluation, model, database,
solver, skill lookup or default physical values are used by this module.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import secrets
import threading
import time
from decimal import Decimal, InvalidOperation

from app.task_intake import capability_notice

VERSION = 'task-contract.v1'
_NUMBER = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?:\s*\\(?:times|cdot)\s*10\s*\^\s*\{?\s*[+-]?\d+\s*\}?)?'
_SPACE = r'(?:\s|\\[ ,;!]|\\quad\b|\\qquad\b|\$)*'
_OPEN = r'(?:\\(?:text|mathrm)\s*\{\s*)?'
_LABELS = {
    'length_x_m': (r'L\s*_\s*\{?x\}?|Lx|长度|长|length(?:_x)?', r'mm|cm|m|毫米|厘米|米', 'm', {'mm': .001, '毫米': .001, 'cm': .01, '厘米': .01}),
    'length_y_m': (r'L\s*_\s*\{?y\}?|Ly|宽度|宽|width|length_y', r'mm|cm|m|毫米|厘米|米', 'm', {'mm': .001, '毫米': .001, 'cm': .01, '厘米': .01}),
    'sound_speed_m_s': (r'声速|speed\s+of\s+sound|sound_speed(?:_m_s)?|c(?:_?0)?', r'm\s*/\s*s|米\s*/\s*秒', 'm/s', {}),
    'density_kg_m3': (r'\\rho\s*_\s*\{?0\}?|rho_?0|ρ_?0|密度|density', r'kg\s*/\s*m(?:\s*\})?\s*(?:\^\s*\{?3\}?|³|3)', 'kg/m³', {}),
}
LABELS = {'length_x_m': '房间长度', 'length_y_m': '房间宽度', 'sound_speed_m_s': '声速',
          'density_kg_m3': '密度（不影响理想模态频率）', 'dimensions': '空间维数',
          'geometry': '房间形状', 'boundary_condition': '墙面条件', 'mode_count': '模态数量'}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _scalar(text, factor=1):
    parts = re.split(r'\\(?:times|cdot)', text)
    value = Decimal(parts[0].strip())
    if len(parts) == 2:
        exponent = re.fullmatch(r'\s*10\s*\^\s*\{?\s*([+-]?\d+)\s*\}?\s*', parts[1])
        if exponent is None or abs(int(exponent[1])) > 300:
            raise ValueError('numeric_exponent_invalid')
        value *= Decimal(10) ** int(exponent[1])
    value = float(value * Decimal(str(factor)))
    if not math.isfinite(value):
        raise ValueError('numeric_value_invalid')
    return value


def _source(query, match):
    start, end = match.span()
    return dict(start=start, end=end, fragment=query[start:end])


def _field(query, name, values, unit=''):
    sources = [_source(query, match) for _, match in values]
    unique = []
    for value, _ in values:
        if value not in unique:
            unique.append(value)
    state = 'missing' if not unique else 'conflict' if len(unique) > 1 else 'explicit'
    if unique and any(isinstance(v, (int, float)) and (not math.isfinite(v) or v <= 0) for v in unique):
        state = 'invalid'
    return dict(name=name, label=LABELS[name], value=unique[0] if len(unique) == 1 else None,
                unit=unit, status=state, alternatives=unique if len(unique) > 1 else [], sources=sources)


def _quantity(query, name):
    label, units, canonical, factors = _LABELS[name]
    pattern = (r'(?<![A-Za-z0-9])(?:' + label + ')' + _SPACE + r'(?:=|:|：|为|是)?' + _SPACE
               + r'(?P<number>' + _NUMBER + ')' + _SPACE + _OPEN
               + r'(?P<unit>' + units + r')(?![A-Za-z/])\}?')
    values = []
    converted = False
    for match in re.finditer(pattern, query, re.I):
        try:
            factor = factors.get(match['unit'].lower(), 1.)
            value = _scalar(match['number'], factor)
            converted = converted or factor != 1.
        except (ValueError, OverflowError, InvalidOperation):
            value = -1.
        values.append((value, match))
    field = _field(query, name, values, canonical)
    if field['status'] == 'explicit' and converted:
        field['status'] = 'converted'
    # A declaration without a recognised unit is not silently interpreted as SI.
    loose = re.compile(r'(?<![A-Za-z0-9])(?:' + label + ')' + _SPACE + r'(?:=|:|：|为|是)?' + _SPACE + _NUMBER, re.I)
    unresolved = [m for m in loose.finditer(query) if not any(s['start'] == m.start() for s in field['sources'])]
    if unresolved:
        field['status'] = 'unit_ambiguous'
        field['sources'] += [_source(query, m) for m in unresolved]
    return field


def _count(text):
    if text.isdigit():
        return int(text)
    digits = dict(zip('零一二三四五六七八九', range(10)))
    if text == '两':
        return 2
    if text.count('十') == 1:
        before, after = text.split('十')
        return (digits.get(before, 1) if before else 1) * 10 + (digits[after] if after else 0)
    return digits.get(text, 0)


def room_contract(query):
    if not isinstance(query, str) or not query.strip() or len(query.encode()) > 65536:
        raise ValueError('task_input_invalid')
    notice = capability_notice(query)
    if notice is None:
        return None
    fields = [_quantity(query, name) for name in _LABELS]
    def enumerated(name, patterns):
        values = [(value, m) for pattern, value in patterns for m in re.finditer(pattern, query, re.I)]
        fields.append(_field(query, name, values))
    enumerated('dimensions', [(r'二维|2\s*-?\s*d\b|two[- ]dimensional', 2), (r'三维|3\s*-?\s*d\b|three[- ]dimensional', 3)])
    enumerated('geometry', [(r'(?<!非)矩形|rectang\w*', 'rectangle'), (r'非矩形|不规则|L形|家具|障碍', 'unsupported')])
    enumerated('boundary_condition', [(r'(?<!非)刚性|声学硬边界|声硬边界|rigid|sound[- ]hard|零\s*Neumann', 'rigid'),
                                      (r'吸声|阻抗|非刚性|柔性|软边界|impedance|absorbing', 'unsupported'),
                                      (r'周期边界|periodic', 'periodic')])
    counts = []
    for pattern in (r'前\s*(\d+|[零一二两三四五六七八九十]+)\s*(?:个|阶|种)?[^。\n]{0,16}?(?:模态|本征|固有)',
                    r'(?:first|lowest|top)\s+(\d+)\s+(?:\w+\s+){0,3}?(?:modes|eigenfrequencies)'):
        counts.extend((_count(m[1]), m) for m in re.finditer(pattern, query, re.I))
    fields.append(_field(query, 'mode_count', counts))
    by_name = {f['name']: f for f in fields}
    required = set(LABELS) - {'density_kg_m3'}
    issues = []
    for field in fields:
        if field['status'] not in {'explicit', 'converted', 'missing'} or (field['status'] == 'missing' and field['name'] in required):
            issues.append(dict(field=field['name'], status=field['status'], message={
                'missing': '请补充' + field['label'], 'conflict': field['label']+'存在多个不同值，请确认',
                'invalid': field['label']+'必须是有限正值', 'unit_ambiguous': '请确认'+field['label']+'的单位',
            }[field['status']]))
    unsupported = []
    for name, allowed in (('dimensions', {2}), ('geometry', {'rectangle'}), ('boundary_condition', {'rigid'})):
        if by_name[name]['status'] == 'explicit' and by_name[name]['value'] not in allowed:
            unsupported.append(name)
    if by_name['mode_count']['status'] == 'explicit' and by_name['mode_count']['value'] > 20:
        unsupported.append('mode_count_over_20')
    if any(by_name[name]['status'] in {'explicit', 'converted'} and not 1e-6 <= by_name[name]['value'] <= 1e6
           for name in ('length_x_m', 'length_y_m', 'sound_speed_m_s')):
        unsupported.append('numeric_range')
    if notice['capability_state'] == 'clarification_required':
        issues.append(dict(field='task_kind', status='conflict', message='请区分模态频率与声源驱动声场'))
    params = {f['name']: f['value'] for f in fields if f['status'] in {'explicit', 'converted'}}
    include_match = re.search(r'(?<!不)(?:包含|计入|包括).{0,8}(?:零频|零模态|\(0,\s*0\))', query)
    exclude_match = re.search(r'(?:排除|不包含|不含|不计入|不计|不包括).{0,8}(?:零频|零模态|\(0,\s*0\))', query)
    include = include_match is not None
    explicit_zero = include_match is not None or exclude_match is not None
    if include_match and exclude_match:
        issues.append(dict(field='include_zero_mode', status='conflict', message='是否计入零频模态存在冲突，请确认'))
    fields.append(dict(name='include_zero_mode', label='零频静态模态', value=include, unit='',
                       status='conflict' if include_match and exclude_match else 'explicit' if explicit_zero else 'proposed',
                       alternatives=[], sources=[_source(query, m) for m in (include_match, exclude_match) if m]))
    proposals = [] if explicit_zero else [dict(id='exclude_zero_mode', label='不计入(0,0)零频静态模态', value=True)]
    proposals.append(dict(id='ideal_uniform_empty_room', label='采用空矩形房间、均匀介质的理想模型；不计算吸声或混响', value=True))
    params['include_zero_mode'] = include
    if re.search(r'非均匀|不均匀|异质|声速随|密度随|nonuniform|heterogeneous', query, re.I):
        unsupported.append('nonuniform_medium')
    result = dict(schema_version=VERSION, task_kind='room_eigenmodes_2d', source_text=query, fields=fields,
                  parameters=params, issues=issues, unsupported=unsupported, proposals=proposals,
                  result_contract=dict(quantity='modal_frequency', units='Hz', preserve_degenerate_modes=True),
                  status='unsupported' if unsupported else 'needs_input' if issues else 'ready_for_confirmation')
    result['contract_sha256'] = digest(result)
    return result


class ConfirmationDrafts:
    """Ephemeral bounded drafts. A consumed or expired id cannot dispatch again."""
    def __init__(self, clock=time.monotonic, ttl=600, limit=64):
        self.clock, self.ttl, self.limit = clock, ttl, limit
        self._items = {}
        self._lock = threading.Lock()

    def _expire(self):
        now = self.clock()
        for key in [key for key, row in self._items.items() if row['expires'] <= now]:
            self._items.pop(key)

    def put(self, contract, session, profile, previous=None):
        with self._lock:
            self._expire()
            if previous is not None:
                if not isinstance(previous, str) or not 1 <= len(previous) <= 128:
                    raise ValueError('task_draft_invalid')
                old = self._items.get(previous)
                if old and (old['session'] != session or old['profile'] != profile):
                    raise ValueError('task_confirmation_mismatch')
                self._items.pop(previous, None)
            if len(self._items) >= self.limit:
                raise ValueError('task_draft_capacity')
            key = secrets.token_urlsafe(32)
            self._items[key] = dict(contract=copy.deepcopy(contract), session=session, profile=profile,
                                    expires=self.clock()+self.ttl)
            return key

    def cancel(self, key, session, profile):
        with self._lock:
            self._expire()
            if not isinstance(key, str) or not 1 <= len(key) <= 128:
                raise ValueError('task_draft_invalid')
            old = self._items.get(key)
            if old and (old['session'] != session or old['profile'] != profile):
                raise ValueError('task_confirmation_mismatch')
            return self._items.pop(key, None) is not None

    def consume(self, key, sha256, session, profile, source_text, accepted):
        with self._lock:
            self._expire()
            if not isinstance(key, str) or not 1 <= len(key) <= 128:
                raise ValueError('task_draft_invalid')
            row = self._items.get(key)
            if row is None:
                raise ValueError('task_draft_expired_or_consumed')
            value = row['contract']
            if (row['session'] != session or row['profile'] != profile or value['contract_sha256'] != sha256
                    or value['source_text'] != source_text or value['status'] != 'ready_for_confirmation'):
                raise ValueError('task_confirmation_mismatch')
            required = {p['id'] for p in value['proposals']}
            if (not isinstance(accepted, list) or len(accepted) > 64 or any(not isinstance(x, str) for x in accepted)
                    or len(accepted) != len(set(accepted)) or set(accepted) != required):
                raise ValueError('task_proposals_not_confirmed')
            self._items.pop(key)
            return copy.deepcopy(value)
