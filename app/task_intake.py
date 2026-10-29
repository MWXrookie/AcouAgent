"""Conservative task capability routing before model extraction or execution.

This first increment identifies room eigenmode requests; it does not claim to
solve them or extract their physical parameters. Source text remains unchanged.
"""
import re

_ROOM = re.compile(r'房间|房內|房内|矩形腔|腔体|\broom\b|\bcavity\b', re.I)
_MODES = re.compile(r'(?:模态|本征|固有).{0,16}(?:频率|分析)|前\s*(?:\d+|[一二三四五六七八九十]+)\s*(?:个|阶)?\s*模态|eigen(?:frequenc\w*|modes?)|room\s+modes?', re.I | re.S)
_DRIVEN = re.compile(r'点声源|点源|扬声器|强迫响应|受迫声场|point\s+source|driven\s+field', re.I)


def capability_notice(query):
    """Return a no-execution notice only for an identified unsupported task.

    A reference to periodic initial modes is not a room eigenfrequency request.
    Mixed eigenmode/source tasks require clarification rather than silently
    selecting a solver or guessing that a negation overrides the other clause.
    """
    if not isinstance(query, str) or not (_ROOM.search(query) and _MODES.search(query)):
        return None
    mixed = _DRIVEN.search(query) is not None
    if mixed:
        report = ('这段需求同时提到了房间模态频率和声源驱动声场。请确认本次要计算哪一种：'
                  '房间自身的模态频率，还是指定声源下的声场响应？本次尚未执行，也不会学习技能。')
    else:
        report = ('已识别为房间模态／本征频率分析。当前版本尚未接入这类任务的受控求解与验证，'
                  '因此本次没有派发声场仿真，也没有新增学习记录。原始问题会保留在会话中。'
                  '\n\n这类任务与声源驱动的声场不同，不应为套用现有流程而要求补充声源频率、网格或PML。'
                  '此提示表示能力暂未接入，不表示你给出的参数不足。')
        from app import config
        if config.ROOM_MODAL_ENABLED:
            return dict(task_kind='room_eigenmodes', capability_state='confirmation_required',
                        error_code='task_confirmation_required', execution_performed=False,
                        report='已识别为房间模态分析。请在声学仿真页面核对参数与计算约定后确认计算；此入口不会绕过确认，也尚未执行或学习。')
    return dict(task_kind='mixed_room_analysis' if mixed else 'room_eigenmodes',
                capability_state='clarification_required' if mixed else 'not_implemented',
                error_code='task_intent_ambiguous' if mixed else 'task_capability_unavailable',
                report=report, execution_performed=False)
