"""Offline extraction contract; declared parameters never authenticate a run."""
import copy

CONTRACT = """
【技能库参数声明契约 v2：以本段补充原输出约定】
输出单个严格 JSON 对象。scenario_type 必须显式选自 point_source_2d、point_source_3d、
plane_wave_2d、initial_pressure_2d、interface_2d、attenuation_helmholtz_2d、heterogeneous_2d。
不能确定场景或必要物理条件时保留未知，不猜测以命中技能库；适配器会回退原生成流程。
通用物理字段：sound_speed(m/s)、density(kg/m3)、domain_N(逐轴点数)、
domain_dx(逐轴米)、pml_size(网格点数)、cfl、t_end(秒)、solver_mode
(time_domain/frequency_domain)、medium_type。source_index 和 sensor_index 为逐轴整数坐标；
sensor_indices 为多个逐轴探针坐标，均须位于 PML 内侧物理区域。
连续驱动源使用 source_kind=continuous_sinusoid 和 source_frequency(Hz)。
initial_pressure_2d 使用 source_kind=gaussian_initial_pressure、initial_pressure_pa(Pa)、
initial_sigma_m(米)、initial_velocity=zero；不添加虚构的连续源频率。
高斯初压还需明确 source_geometry(planar_x_uniform_y/radial_gaussian)、
source_index、sensor_indices 和 smooth_initial(布尔)。平面高斯与径向高斯不可混淆。
缺少源位置、宽度、探针或求解条件时不补成固定配方值；完整参数需与生成代码一致。
description 可保留为不超过512个UTF-8字节的简短备注，不进入技能查询参数。
本声明不是执行观测或可信物理证明；禁止输出 trusted_run、verdict、quality_level 等授信字段。
"""

def patch(graph, parameter_ids):
    result=copy.deepcopy(graph)
    index={n["id"]:n for n in result["nodes"]}
    if len(index)!=len(result["nodes"]) or len(parameter_ids)!=len(set(parameter_ids)):
        raise ValueError("explicit_parameter_nodes")
    for node_id in parameter_ids:
        node=index.get(node_id)
        prompts=node.get("data",{}).get("prompt_template") if node else None
        if node is None or node["data"].get("type")!="llm" or type(prompts) is not list:
            raise ValueError("parameter_llm_required")
        users=[p for p in prompts if p.get("role")=="user"]
        if len(users)!=1 or type(users[0].get("text")) is not str:
            raise ValueError("one_parameter_user_prompt")
        text=users[0]["text"]
        marker="【技能库参数声明契约"
        if marker in text:
            if not text.endswith(CONTRACT) or text.count(marker)!=1:
                raise ValueError("parameter_contract_drift")
        else:
            users[0]["text"]=text+CONTRACT
    return result
