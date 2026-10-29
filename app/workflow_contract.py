"""Conservative previews for existing workflows; no execution or skill lookup.

Model-extracted values without independently matched source spans are proposals,
never attributed to the user. The full frozen extraction stays server-side.
"""
import copy
import json
import math
import re
import uuid
from dataclasses import dataclass

from app import local_workflow, model_profiles
from app.task_contract import VERSION, _NUMBER, _SPACE, _OPEN, _quantity, _scalar, _source, digest

LABELS = {
    'sound_speed':('声速','m/s'),'sound_speed_m_s':('声速','m/s'),
    'density':('密度','kg/m³'),'density_kg_m3':('密度','kg/m³'),
    'source_frequency':('驱动频率','Hz'),'frequency_hz':('驱动频率','Hz'),
    'domain_N':('网格点数',''),'grid_shape':('网格点数',''),
    'domain_dx':('网格间距','m'),'dx_m':('网格间距','m'),
    't_end':('仿真结束时间','s'),'t_end_s':('仿真结束时间','s'),
    'cfl':('时间步稳定性系数CFL',''),'pml_size':('吸收层网格数',''),
    'source_type':('激励类型',''),'source_kind':('激励类型',''),
    'source_geometry':('声源形状',''),'source_index':('声源网格位置',''),
    'sensor_index':('探针网格位置',''),'sensor_indices':('探针网格位置',''),
    'dimensions':('空间维数',''),'solver_mode':('求解类型',''),
    'medium_type':('介质模型',''),'boundary_condition':('边界条件',''),
    'initial_pressure_pa':('初始峰值压力','Pa'),'initial_sigma_m':('初压宽度','m'),
    'initial_velocity':('初始速度',''),'smooth_initial':('平滑初始压力',''),
    'modal_amplitudes_pa':('各模态幅值','Pa'),'modal_wavenumber_rad_per_m':('各方向波数','rad/m'),
    'helmholtz_source_coefficient':('数值源系数',''),
    'background_attenuation_db_mhz2_cm':('背景衰减系数','dB/(MHz²·cm)'),
    'material_regions':('材料区域',''),'secondary_sound_speed_m_s':('第二介质声速','m/s'),
    'interface_position_m':('介质分界位置','m'),
    'source_amplitude_pa':('附加源项压力系数','Pa'),
    'source_cycles':('脉冲周期数',''),
    'source_normalization':('源项物理约定',''),
}
_SCENARIOS = {'point_source_2d','plane_wave_2d','interface_2d','heterogeneous_2d',
              'initial_pressure_2d','attenuation_2d','attenuation_helmholtz_2d','point_source_3d','initial_pressure_3d'}


@dataclass(frozen=True)
class ConfirmedExtraction:
    source_text: str
    profile_id: str | None
    extracted: dict
    audit: dict
    parameter_extraction_available: bool
    extraction_origin: str


def frozen_extraction(contract, profile_id):
    if contract.get('status') != 'ready_for_confirmation' or not isinstance(contract.get('_extracted'),dict):
        raise ValueError('workflow_contract_not_ready')
    unhashed = {k:v for k,v in contract.items() if k != 'contract_sha256'}
    if digest(unhashed) != contract.get('contract_sha256'):
        raise ValueError('workflow_contract_changed')
    if contract.get('_profile_id') != profile_id:
        raise ValueError('workflow_profile_changed')
    return ConfirmedExtraction(contract['source_text'],profile_id,copy.deepcopy(contract['_extracted']),
        copy.deepcopy(contract['_audit']),contract['_parameter_extraction_available'],contract['_extraction_origin'])


def _explicit_span(query, key, value):
    """Locate a literal JSON declaration, including arrays, without evaluation."""
    decoder = json.JSONDecoder()
    sources = []
    for match in re.finditer(r'"'+re.escape(key)+r'"\s*:\s*',query):
        try:
            parsed,end = decoder.raw_decode(query[match.end():])
        except ValueError:
            continue
        if parsed == value:
            stop = match.end()+end
            sources.append(dict(start=match.start(),end=stop,fragment=query[match.start():stop]))
    return sources


def _numeric_mentions(query, name):
    if name in {'source_frequency','frequency_hz'}:
        label,units,factors = (r'source_frequency|frequency_hz|(?:驱动|声源)?频率|frequency|f',
            r'MHz|kHz|Hz|兆赫兹?|千赫兹?|赫兹',{'mhz':1000000,'兆赫':1000000,'兆赫兹':1000000,
                'khz':1000,'千赫':1000,'千赫兹':1000})
    elif name in {'t_end','t_end_s'}:
        label,units,factors = (r't_end_s|t_\{?end\}?|仿真结束时间|结束时间|仿真时长|duration',
            r'ns|μs|µs|us|ms|s|纳秒|微秒|毫秒|秒',{'ns':1e-9,'纳秒':1e-9,'μs':1e-6,
                'µs':1e-6,'us':1e-6,'微秒':1e-6,'ms':.001,'毫秒':.001})
    elif name=='source_amplitude_pa':
        label,units,factors = (r'source_amplitude_pa|附加源项压力系数|源项系数',
            r'MPa|kPa|Pa|兆帕|千帕|帕',{'mpa':1000000,'兆帕':1000000,'kpa':1000,'千帕':1000})
    else:
        return None
    prefix = r'(?<![A-Za-z0-9])(?:'+label+')'+_SPACE+r'(?:=|:|：|为|是)?'+_SPACE
    pattern = prefix+r'(?P<number>'+_NUMBER+')'+_SPACE+_OPEN+r'(?P<unit>'+units+r')(?![A-Za-z/])\}?'
    values,sources = [],[]
    for match in re.finditer(pattern,query,re.I):
        try: value = _scalar(match['number'],factors.get(match['unit'].lower(),1))
        except (ValueError,OverflowError): value = -1.
        values.append(value);sources.append(_source(query,match))
    loose = [m for m in re.finditer(prefix+_NUMBER,query,re.I)
             if not any(m.start()==s['start'] for s in sources)]
    unique = list(dict.fromkeys(values))
    status = 'missing' if not unique else 'conflict' if len(unique)>1 else 'explicit'
    if any(v<=0 for v in unique): status='invalid'
    if loose: status='unit_ambiguous';sources += [_source(query,m) for m in loose]
    return dict(value=unique[0] if len(unique)==1 else None,status=status,sources=sources,alternatives=unique)


def _grid_mentions(query,name):
    if name in {'domain_N','grid_shape'}:
        pattern = r'(?:网格(?:点数|大小|形状)?|grid(?:_shape|\s+shape)?|domain_N)\s*(?:=|为|:|：)?\s*([+-]?\d+)\s*[×x*]\s*([+-]?\d+)(?:\s*[×x*]\s*([+-]?\d+))?'
        def vector(m): return [int(v) for v in m.groups() if v is not None]
    elif name in {'domain_dx','dx_m'}:
        q = r'('+_NUMBER+r')'+_SPACE+_OPEN+r'(mm|cm|m|毫米|厘米|米)\}?'
        pattern = r'(?:网格间距|网格步长|domain_dx|dx_m|dx|grid\s+spacing)\s*(?:=|为|:|：)?\s*'+q+r'\s*[×x*]\s*'+q
        def vector(m): return [_scalar(m[1],{'mm':.001,'cm':.01,'毫米':.001,'厘米':.01}.get(m[2].lower(),1)),
                               _scalar(m[3],{'mm':.001,'cm':.01,'毫米':.001,'厘米':.01}.get(m[4].lower(),1))]
    else:
        return None
    values,sources = [],[]
    for match in re.finditer(pattern,query,re.I):
        value = vector(match)
        if value not in values: values.append(value)
        sources.append(_source(query,match))
    return dict(value=values[0] if len(values)==1 else None,sources=sources,alternatives=values,
                status='missing' if not values else 'conflict' if len(values)>1 else 'explicit')


def _structural_mentions(query,name):
    labels = {'source_type':r'source_type','source_geometry':r'source_geometry',
        'source_index':r'source_index','source_cycles':r'source_cycles|脉冲周期数',
        'source_normalization':r'source_normalization','boundary_condition':r'boundary_condition',
        'solver_mode':r'solver_mode','medium_type':r'medium_type','dimensions':r'dimensions',
        'cfl':r'cfl','pml_size':r'pml_size'}
    if name not in labels: return None
    values,sources = [],[]
    decoder = json.JSONDecoder()
    pattern = r'(?<![A-Za-z0-9_])(?:'+labels[name]+r')\s*(?:=|:|：|为|是)\s*'
    for match in re.finditer(pattern,query,re.I):
        tail = query[match.end():]
        try: value,end = decoder.raw_decode(tail)
        except ValueError:
            word = re.match(r'[A-Za-z][A-Za-z0-9_.-]*',tail)
            if not word: continue
            value,end = word[0],word.end()
        if value not in values: values.append(value)
        stop = match.end()+end
        sources.append(dict(start=match.start(),end=stop,fragment=query[match.start():stop]))
    return dict(value=values[0] if len(values)==1 else None,status='missing' if not values else 'conflict' if len(values)>1 else 'explicit',
                sources=sources,alternatives=values)


def build_contract(query, extracted, audit, profile_id, available, *, explicit=False):
    extracted = copy.deepcopy(extracted)
    if len(json.dumps(extracted,ensure_ascii=False,allow_nan=False).encode()) > 16384:
        raise ValueError('task_parameter_contract_too_large')
    params = extracted.get('simulation_params')
    scenario = extracted.get('scenario_type')
    if not isinstance(params,dict) or not isinstance(scenario,str) or len(params)>48:
        raise ValueError('task_parameter_schema_invalid')
    frequency_mentioned = bool(re.search(r'频域|稳态|Helmholtz|frequency[- ]domain|steady[- ]state',query,re.I))
    frequency = 'result_spec' in extracted or params.get('solver_mode') == 'frequency_domain' or frequency_mentioned
    initial = params.get('source_type',params.get('source_kind')) in {'modal_initial_pressure','gaussian_initial_pressure'}
    kind = 'frequency_domain_field' if frequency else 'initial_pressure_propagation' if initial else 'driven_time_field'
    issues,fields,proposals,unsupported = [],[],[],[]
    if not frequency and not initial:
        aliases = {'sound_speed_m_s':'sound_speed','density_kg_m3':'density',
            'frequency_hz':'source_frequency','source_frequency_hz':'source_frequency',
            'grid_shape':'domain_N','dx_m':'domain_dx','domain_dx_m':'domain_dx','t_end_s':'t_end'}
        canonical = {}
        for name,value in params.items():
            target = aliases.get(name,name)
            if target in canonical and canonical[target]!=value:
                issues.append(dict(field=target,status='conflict',message=LABELS.get(target,(target,''))[0]+'的别名声明冲突，请澄清'))
            else:
                canonical[target] = value
        params = extracted['simulation_params'] = canonical
    if scenario not in _SCENARIOS:
        unsupported.append('workflow_task_kind')
    scenario_dimension = 3 if scenario.endswith('_3d') else 2 if scenario.endswith('_2d') else None
    dimensions = params.get('dimensions',scenario_dimension)
    source_dimensions = {value for pattern,value in [(r'二维|\b2\s*d\b|two[- ]dimensional',2),
        (r'三维|\b3\s*d\b|three[- ]dimensional',3)] if re.search(pattern,query,re.I)}
    if (len(source_dimensions)>1 or source_dimensions and dimensions not in source_dimensions
            or dimensions!=scenario_dimension):
        issues.append(dict(field='dimensions',status='conflict',message='原文、网格或所选场景的空间维数不一致，请核对'))
    if not explicit:
        initial_mentioned = bool(re.search(r'初压|初始压力|initial\s+pressure',query,re.I))
        if initial_mentioned != initial or frequency_mentioned and params.get('solver_mode')!='frequency_domain':
            issues.append(dict(field='task_kind',status='conflict',message='识别的求解类型与原文不一致，请澄清时域初压传播或频域稳态声场'))
        for pattern, expected in [(r'周期边界|periodic\s+boundar','periodic'),(r'刚性壁|声硬边界|rigid\s+wall','rigid')]:
            if re.search(pattern,query,re.I) and params.get('boundary_condition') != expected:
                issues.append(dict(field='boundary_condition',status='conflict',message='识别的边界条件与原文明示不一致，请重新核对'))
    # No hidden numerical defaults: even fields treated as optional by the old
    # general validator must be declared/confirmed before this new path runs.
    required = ['dimensions','solver_mode','medium_type','sound_speed_m_s','density_kg_m3','grid_shape',
                'dx_m','frequency_hz','pml_size','source_index','source_type','source_geometry',
                'helmholtz_source_coefficient','background_attenuation_db_mhz2_cm','material_regions',
                'boundary_condition'] if frequency else [] if initial else [
                    'sound_speed','density','source_frequency','domain_N','domain_dx','t_end','cfl','pml_size']
    if not frequency and not initial:
        required += ['solver_mode','medium_type','source_type','source_geometry','source_index',
                     'source_amplitude_pa','source_normalization','boundary_condition']
        if params.get('source_type')=='tone_burst': required.append('source_cycles')
    if initial:
        alternatives = [('source_type','source_kind'),('sound_speed_m_s','sound_speed'),
            ('density_kg_m3','density'),('grid_shape','domain_N'),('dx_m','domain_dx'),('t_end_s','t_end')]
        required = [next((name for name in pair if name in params),pair[0]) for pair in alternatives]
        required += ['solver_mode','medium_type','cfl','pml_size','initial_pressure_pa','initial_velocity','source_geometry']
        required += ['modal_amplitudes_pa','modal_wavenumber_rad_per_m','boundary_condition'] if params.get('source_type',params.get('source_kind'))=='modal_initial_pressure' else ['initial_sigma_m']
    if frequency and 'dimensions' in params and params['dimensions'] != 2:
        unsupported.append('frequency_2d_only')
    names = list(dict.fromkeys(required+list(params)))
    for name in names:
        value = params.get(name)
        label,unit = LABELS.get(name,(name,''))
        sources = _explicit_span(query,name,value) if explicit and name in params else []
        status = 'missing' if name in required and value is None else 'explicit' if sources else 'model_proposed' if name in params else 'missing'
        alternatives = []
        # Match only supported scalar mentions. Multiple media/contradictory
        # scalar mentions require clarification rather than silently choosing.
        numeric_name = {'sound_speed':'sound_speed_m_s','sound_speed_m_s':'sound_speed_m_s',
                        'density':'density_kg_m3','density_kg_m3':'density_kg_m3'}.get(name)
        if not explicit and type(value) in (int,float):
            mention = _quantity(query,numeric_name) if numeric_name else _numeric_mentions(query,name)
        else:
            mention = None
        if mention is not None:
            if mention['status'] in {'explicit','converted'}:
                sources = mention['sources']
                if value == mention['value']:
                    status = mention['status']
                else:
                    status,alternatives = 'conflict',[mention['value'],value]
            elif mention['status'] in {'conflict','unit_ambiguous','invalid'}:
                sources,status,alternatives = mention['sources'],mention['status'],mention['alternatives']
        grid = _grid_mentions(query,name) if not explicit else None
        if grid is not None and grid['status']!='missing':
            sources,alternatives = grid['sources'],grid['alternatives']
            if grid['status']=='conflict':
                status='conflict'
            elif value==grid['value']:
                status='explicit'
            elif type(value) in (int,float) and all(v==value for v in grid['value']):
                # A scalar repeated over explicitly declared equal axes is
                # structural expansion, not an invented grid or resolution.
                value=params[name]=grid['value']
                status='derived'
            else:
                status='conflict'
        structural = _structural_mentions(query,name) if not explicit else None
        if structural is not None and structural['status']!='missing':
            sources,alternatives = structural['sources'],structural['alternatives']
            status='explicit' if structural['status']=='explicit' and value==structural['value'] else 'conflict'
        if status in {'missing','conflict','unit_ambiguous','invalid'}:
            issues.append(dict(field=name,status=status,message=label+('未提供' if status=='missing' else '与原文不一致或需要澄清，请修改问题后重新识别')))
        if unit and name in params and value is not None:
            numbers = value if isinstance(value,list) else [value]
            if any(type(number) not in (int,float,list) for number in numbers):
                status='invalid'
                issues.append(dict(field=name,status='invalid',message=label+'需要明确数值，请检查单位和数值类型'))
        if name=='cfl' and value is not None and (type(value) not in (int,float) or not 0<value<=.3):
            status='invalid'
            issues.append(dict(field=name,status='invalid',message='CFL须为大于0且不超过0.3的数值'))
        if status == 'model_proposed':
            proposals.append(dict(id='parameter:'+name,label='核对'+label+'，采用上表识别值',value=True))
        fields.append(dict(name=name,label=label,value=value,unit=unit,status=status,alternatives=alternatives,sources=sources))
    if frequency:
        result_spec = extracted.get('result_spec')
        if not isinstance(result_spec,dict):
            issues.append(dict(field='result_spec',status='missing',message='请明确结果量、单位、轴序与是否保留完整复压力场'))
        fields.append(dict(name='result_spec',label='结果要求',value=result_spec,unit='',
                           status='explicit' if explicit else 'model_proposed',alternatives=[],
                           sources=_explicit_span(query,'result_spec',result_spec) if explicit else []))
        if not explicit and result_spec is not None:
            proposals.append(dict(id='result_spec',label='核对完整稳态复压力结果合同（单位、轴序、网格、不降采样）',value=True))
    if not issues and not unsupported:
        shape = params.get('grid_shape',params.get('domain_N'))
        if isinstance(shape,list) and len(shape)!=dimensions:
            issues.append(dict(field='dimensions',status='conflict',message='网格轴数与空间维数不一致，请核对'))
    if not issues and not unsupported:
        if frequency:
            valid = local_workflow._skill_query(extracted) is not None
            validation = dict(valid=valid,errors=[] if valid else [dict(message='频域参数与结果要求未通过完整合同检查')])
        else:
            validation = local_workflow._tool_functions()['validate_simulation_params'](json.dumps(params,allow_nan=False))
            if not initial:
                source_errors = []
                if (scenario!='point_source_2d' or params.get('solver_mode')!='time_domain'
                        or params.get('source_geometry')!='point' or params.get('boundary_condition')!='absorbing_pml'
                        or params.get('medium_type') not in {'homogeneous','homogeneous_water','air'}):
                    source_errors.append(dict(field='source_type',message='此确认入口首批支持均匀介质、吸收层边界的二维时域点源；其他条件请改用已支持的完整频域或初压合同'))
                if params.get('source_normalization')!='jwave_additive_pressure_coefficient':
                    source_errors.append(dict(field='source_normalization',message='请明确采用jwave 0.2.1附加源项压力系数约定；此系数不是换能器标定压力或规定边界声压'))
                amplitude = params.get('source_amplitude_pa')
                if type(amplitude) not in (int,float) or not math.isfinite(amplitude) or amplitude<=0:
                    source_errors.append(dict(field='source_amplitude_pa',message='请提供有限正的附加源项压力系数（Pa）'))
                source_type = params.get('source_type')
                if source_type not in {'tone_burst','continuous_sinusoid'}:
                    source_errors.append(dict(field='source_type',message='请明确连续正弦或有限周期脉冲，不得由代码自行选取波形'))
                if source_type=='tone_burst':
                    cycles = params.get('source_cycles')
                    if type(cycles) is not int or not 1<=cycles<=1000:
                        source_errors.append(dict(field='source_cycles',message='脉冲周期数须为1至1000的整数'))
                    elif params['t_end']<cycles/params['source_frequency']:
                        source_errors.append(dict(field='t_end',message='结束时间小于完整脉冲时长，请延长时间，避免默默截断激励'))
                shape,position = params.get('domain_N'),params.get('source_index')
                pml = params.get('pml_size')
                if (type(shape) is not list or len(shape)!=2 or type(position) is not list or len(position)!=2
                        or type(pml) is not int or pml<=0
                        or any(type(i) is not int or not pml<=i<n-pml for i,n in zip(position,shape))):
                    source_errors.append(dict(field='source_index',message='请给出二维有效网格内、吸收层之外的整数声源位置'))
                validation = dict(valid=validation.get('valid') is True and not source_errors,
                                  errors=validation.get('errors',[])+source_errors)
        if validation.get('valid') is not True:
            for error in validation.get('errors',[])[:12]:
                issues.append(dict(field=error.get('field','physics'),status='invalid',message=error.get('message','参数检查未通过')))
    if not frequency:
        try:
            extracted['skill_query'] = dict(scenario_type=scenario,parameters=local_workflow._retrieval_parameters(params))
        except (ValueError,TypeError,OverflowError):
            extracted['skill_query'] = None
    if not frequency and not initial:
        proposals.append(dict(id='time_field_result',label='输出各网格点在全时段的最大绝对压力；这是附加源项约定下的数值声场，不是换能器标定结果',value=True))
    contract = dict(schema_version=VERSION,task_kind=kind,source_text=query,parameters=copy.deepcopy(params),
        fields=fields,issues=issues,unsupported=unsupported,proposals=proposals,
        result_contract=copy.deepcopy(extracted.get('result_spec') or (dict(quantity='max_absolute_pressure_over_time',
            units='Pa',source_normalization='jwave_additive_pressure_coefficient') if not initial else {})),
        status='unsupported' if unsupported else 'needs_input' if issues else 'ready_for_confirmation',
        _extracted=copy.deepcopy(extracted),_audit={key:audit[key] for key in ('profile_id','model')},
        _profile_id=profile_id,_parameter_extraction_available=available,
        _extraction_origin='explicit_declaration' if explicit else 'model')
    contract['contract_sha256'] = digest(contract)
    return contract


async def prepare(query, profile_id):
    if not isinstance(query,str) or not query.strip() or len(query.encode())>65536:
        raise ValueError('task_input_invalid')
    unsupported_modes = bool(re.search(r'(?:求|计算|推导|find|calculate).{0,40}(?:模态|本征|固有|eigen).{0,20}(?:频率|frequenc)',query,re.I|re.S))
    if unsupported_modes or not re.search(r'声|波|频域|Helmholtz|jwave|pressure|acoustic|wave|point.source|initial_pressure|scenario_type',query,re.I):
        contract = dict(schema_version=VERSION,task_kind='unknown_simulation',source_text=query,
            fields=[],parameters={},issues=[dict(field='task_kind',status='missing',message=
                '目前仅支持二维空矩形刚性房间的模态频率；其他几何的本征问题不会派发为声源驱动仿真' if unsupported_modes else '请说明要计算的声学问题和所需结果')],
            unsupported=['workflow_task_kind'],proposals=[],result_contract={},status='unsupported')
        contract['contract_sha256'] = digest(contract)
        return contract
    # The original explicit declaration is authoritative and needs no extraction
    # model. A model is still configured for the existing generation/review path.
    frequency = None
    start = query.find('{')
    if start>=0:
        try:
            from app.skill_spec import _decode
            declared = _decode(query[start:])
        except ValueError:
            declared = None
        if (isinstance(declared,dict) and {'scenario_type','simulation_params'}<=declared.keys()
                and not set(declared)-{'scenario_type','simulation_params','result_spec'}):
            frequency = dict(declared,skill_query=None)
    initial = local_workflow._explicit_initial_contract(query)
    if frequency is not None or initial is not None:
        extracted = frequency or initial
        if extracted['simulation_params'].get('source_type',extracted['simulation_params'].get('source_kind')) in {'modal_initial_pressure','gaussian_initial_pressure'} and 'result_spec' not in extracted:
            extracted = dict(extracted,skill_query=dict(scenario_type=extracted['scenario_type'],
                parameters=local_workflow._retrieval_parameters(extracted['simulation_params'])))
        profile,_ = model_profiles.resolve(profile_id,'parameter_extraction')
        audit = dict(profile_id=profile.audit_profile_id(),model=profile.model)
        return build_contract(query,extracted,audit,profile_id,False,explicit=True)
    extracted,audit,available = await local_workflow.extract_parameters(query,profile_id,str(uuid.uuid4()))
    return build_contract(query,extracted,audit,profile_id,available)


def public_contract(contract):
    return {key:value for key,value in contract.items() if not key.startswith('_')}
