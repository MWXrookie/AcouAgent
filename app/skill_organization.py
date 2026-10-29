"""Read-only browsing categories and historical counts, never reuse authority.

The family is deliberately a coarse scenario folder. Case identity remains the
store's existing parameter/result fingerprint. No row, evidence or version is
rewritten, merged or removed.
"""
import json
import math

FAMILIES = {
    'point_source_2d': '二维点声源', 'point_source_3d': '三维点声源',
    'plane_wave_2d': '二维平面波', 'initial_pressure_2d': '二维初始压力传播',
    'interface_2d': '介质界面传播', 'attenuation_helmholtz_2d': '频域衰减',
    'heterogeneous_2d': '二维异质介质',
}


def display_parameters(text):
    """Only bounded, explicitly unit-labelled numeric inputs may reach cards."""
    if not isinstance(text, str) or len(text) > 8192:
        return []
    try:
        raw = json.loads(text)
        if not isinstance(raw, dict): return []
        params = raw.get('parameters') if raw.get('normalizer_version') == 'normalizer_v2' else raw
        if not isinstance(params, dict): return []
        result = []
        for names, label, unit in [
            (('frequency_hz','source_frequency'),'频率','Hz'),
            (('sound_speed_m_s','sound_speed'),'声速','m/s'),
            (('grid_shape','domain_N'),'网格',''),
            (('t_end_s',),'结束时间','s')]:
            value = next((params[name] for name in names if name in params), None)
            values = value if isinstance(value,list) else [value]
            if 1 <= len(values) <= 3 and all(type(v) in (int,float) and math.isfinite(v) and v>0 for v in values):
                result.append(dict(label=label,value=value,unit=unit))
        return result
    except (ValueError, TypeError, OverflowError):
        return []

# UNION removes identical links; a run with conflicting case/version identities
# is excluded, as in read_execution_links. These are historical recorded proofs,
# not a current runtime/feedback/physics authorization decision.
_CASES = '''WITH proofs AS (
    SELECT d.run_id,d.skill_id,d.version FROM skill_local_reference_dispatches d
    JOIN skill_local_reference_completions c ON c.run_id=d.run_id WHERE c.outcome='verified'
    UNION SELECT d.run_id,d.skill_id,d.version FROM skill_reference_dispatches d
    JOIN skill_reference_executions e ON e.run_id=d.run_id
), unambiguous AS (
    SELECT run_id,MIN(skill_id) skill_id FROM proofs GROUP BY run_id HAVING COUNT(*)=1
), versions AS (SELECT skill_id,COUNT(*) version_count FROM skill_versions GROUP BY skill_id),
reexecutions AS (SELECT skill_id,COUNT(*) reexecution_count FROM unambiguous GROUP BY skill_id)
SELECT s.skill_id,s.scenario_type,COALESCE(v.version_count,0) version_count,
       COALESCE(r.reexecution_count,0) reexecution_count
FROM simulation_skills s LEFT JOIN versions v ON v.skill_id=s.skill_id
LEFT JOIN reexecutions r ON r.skill_id=s.skill_id WHERE (? OR s.archived=0)'''


def read(db, include_archived, visible_ids):
    groups, per_case = {}, {}
    visible = set(visible_ids)
    for row in db.execute(_CASES, (include_archived,)):
        scenario = row['scenario_type'] if row['scenario_type'] in FAMILIES else 'unclassified'
        group = groups.setdefault(scenario, dict(family_id=scenario,
            label=FAMILIES.get(scenario,'未分类'), case_count=0, version_count=0, reexecution_count=0))
        group['case_count'] += 1
        group['version_count'] += row['version_count']
        group['reexecution_count'] += row['reexecution_count']
        if row['skill_id'] in visible:
            per_case[row['skill_id']] = dict(family_id=scenario, version_count=row['version_count'],
                                           reexecution_count=row['reexecution_count'])
    summary = dict(family_count=len(groups), case_count=sum(g['case_count'] for g in groups.values()),
        version_count=sum(g['version_count'] for g in groups.values()),
        reexecution_count=sum(g['reexecution_count'] for g in groups.values()),
        includes_archived=include_archived, families=sorted(groups.values(), key=lambda x:x['family_id']),
        scope='whole_registered_library', trust_verified=False,
        explanation='家族是浏览分类；案例按既有精确参数与结果合同登记。版本不是新案例。复用次数仅为历史已核对执行记录，不是当前复用许可或模型采纳证明。')
    return summary, per_case
