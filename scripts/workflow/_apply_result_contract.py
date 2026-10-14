"""Apply the auditable field/sensor output contract to the Dify code-generation node.

Usage: python3 scripts/workflow/_apply_result_contract.py graph.json > patched.json
"""

import json
import sys

CODE_GEN = "1783991079665"
MARKER = "## 结果输出契约（必须遵守）"

CONTRACT = r'''

## 结果输出契约（必须遵守）
- `Sources.positions` 是整数网格索引，不是米制坐标。中心点示例：
  `positions = (jnp.array([64], dtype=jnp.int32), jnp.array([64], dtype=jnp.int32))`。
- 不要用 matplotlib、`imshow`、`plot` 或 `savefig` 自己作图。网关统一根据下述数据生成图像，避免初始帧黑图和中文字体告警。
- 稳态/峰值声压场必须取完整时间序列的逐点最大绝对值：
  `field = jnp.max(jnp.abs(p.params), axis=0)[..., 0]`。
  禁止使用 `p.params[0]`、`pressure[0, ...]` 或 t=0 帧作热力图；也不要只用某个任意末帧冒充稳态场。
- 按已有 `__ACOU_FIELD_START__` / `__ACOU_FIELD_END__` 模板输出二维 field JSON。
- 需求包含点传感器时，距离先换算为整数网格偏移，例如 `offset = int(round(distance_m / dx))`；
  从完整压力序列读取 `sensor_pressure = p.params[:, sensor_x, sensor_y, 0]`，并额外输出：
  `__ACOU_SENSOR_START__`
  `{"time": [...], "pressure": [...], "sensor_index": [x, y], "sensor_position_m": [x_m, y_m]}`
  `__ACOU_SENSOR_END__`
  单传感器的 `time` 与 `pressure` 必须是一维等长数组。网关会据此生成真正的时域声压曲线。
'''


TIMING_MARKER = "## 时长与稳态判定补充（v2）"
TIMING_CONTRACT = r'''

## 时长与稳态判定补充（v2）
- 本节纠正旧契约的“稳态/峰值”混称：全时最大绝对声压仅是所运行时间窗口内的峰值分布，不证明稳态。
- 如仅运行指定步数，必须保留该步数，不得擅自延长；报告实际 dt、样本数、结束时间。时间轴浮点取整可能导致样本数与期望相差一步，必须以实际数组为准。
- 有点传感器时，根据实际距离和声速计算几何到达时间 distance_m / sound_speed；结束时间不足时明确提示“主波尚未到达传感器”，不得将到达前微弱数值振荡称为有效响应。
- 要求稳态但时长不足时，输出短时瞬态/窗口峰值并说明需求未满足，另给延长时长的建议；不得将执行成功或非零压力当作稳态达成。
- 延长时间后也须比较后段多个周期的振幅/相位稳定性，未检查收敛不得声称已达到稳态。
- 例如水中 c=1500 m/s、dx=0.25 mm、CFL=0.3 时 dt=0.05 us，30 步约1.5 us；5 mm 到达时间约3.33 us。此例不能输出“稳态已完成”的结论。
'''


SENSOR_METADATA_MARKER = "## 可选传感器物理元数据（v1）"
SENSOR_METADATA_SNIPPET = "# Only for a single continuous sinusoid starting at t=0 in uniform medium.\n# These variables must be the ones used to construct Medium, Sources and read p.\nsensor_payload[\"timing_metadata\"] = {\n    \"version\": 1, \"time_unit\": \"s\", \"medium\": \"uniform\",\n    \"sound_speed_m_s\": float(actual_uniform_sound_speed),\n    \"source_frequency_hz\": float(actual_source_frequency_hz),\n    \"source_position_m\": [float(i) * float(d) for i, d in zip(actual_source_index, actual_grid_spacing_m)],\n    \"sensor_position_m\": [float(i) * float(d) for i, d in zip(actual_sensor_index, actual_grid_spacing_m)],\n    \"source_start_s\": 0, \"source_kind\": \"continuous_sinusoid\",\n}\n"
SENSOR_METADATA_CONTRACT = """
\n## 可选传感器物理元数据（v1）
- 只有实际模型是均匀介质、零时刻启动单个连续正弦源时，才在已有sensor_payload加入timing_metadata。其他模型省略，诊断unknown；不得为凑字段伪造模型条件。
- 下面actual_*必须绑定实际创建Medium/Sources、读取p.params的配置变量，不能从需求文字复制；坐标单位米，时间数组单位秒。
- actual_source_index/actual_sensor_index/actual_grid_spacing_m长度必须相同且为2或3；同一网格坐标原点。实际网格偏移取整后再计算位置，禁止用期望距离代替实际距离。
- 先生成原time/pressure/sensor_index，再插入下段，最后沿用原标记和json.dumps打印；不得单独打印元数据或改变场数据/步数。
- 字段仅供未认证时长诊断，不证明参数绑定、来源真实或稳态。
""" + "\n\x60\x60\x60python\n" + SENSOR_METADATA_SNIPPET + "\x60\x60\x60\n"



SENSOR_TIME_ORIGIN_MARKER = "## jwave 压力输出实际时刻（v3）"
SENSOR_TIME_ORIGIN_SNIPPET = """# jwave 0.2.1 Fourier solver emits p AFTER each full pressure update.
# Keep the actual output count; do not alter dt or requested simulation steps.
actual_dt_s = float(time_axis.dt)
sensor_payload["time"] = [(i + 1) * actual_dt_s for i in range(len(sensor_pressure))]
# Only if the actual uniform/single-continuous-source model qualifies for metadata:
if isinstance(sensor_payload.get("timing_metadata"), dict):
    sensor_payload["timing_metadata"]["version"] = 2
    sensor_payload["timing_metadata"]["sample_start_s"] = actual_dt_s
    sensor_payload["timing_metadata"]["sample_origin"] = "jwave_0_2_1_after_update"
"""
SENSOR_TIME_ORIGIN_CONTRACT = """
\n## jwave 压力输出实际时刻（v3）
- jwave 0.2.1 Fourier求解器在每次完整压力更新之后返回传感器样本，p.params[k]对应(k+1)*dt；不要直接把从0开始的TimeAxis.to_array()贴到该输出上。
- 本节修正旧时间示例：保留实际压力样本数与用户步数，用实际time_axis.dt构造时间数组。其他求解器/自行加入初始p0的输出须先核对各自语义，不能盲目加dt。
- 如果符合前述均匀介质、单连续正弦源元数据条件，改用version=2，增加sample_start_s=actual_dt_s及sample_origin=jwave_0_2_1_after_update；其余原有字段保持实际变量取值。
- 不符合该模型条件时只输出正确time/pressure，不伪造元数据；没有认证仍是unknown/insufficient/unverified，不证明稳态或物理正确。
""" + "\n" + chr(96)*3 + "python\n" + SENSOR_TIME_ORIGIN_SNIPPET + chr(96)*3+"\n"


def main() -> None:
    graph = json.load(open(sys.argv[1], encoding="utf-8"))
    found = False
    for node in graph.get("nodes", []):
        if node.get("id") not in {CODE_GEN,"1786095555003"}:
            continue
        for message in node.get("data", {}).get("prompt_template", []):
            if message.get("role") != "user":
                continue
            text = message.get("text", "")
            text = text.replace("pressure = p.params[0]", "pressure = p.params")
            if MARKER not in text:
                text = text.rstrip() + CONTRACT
            if TIMING_MARKER not in text:
                text = text.rstrip() + TIMING_CONTRACT
            if SENSOR_METADATA_MARKER not in text:
                text = text.rstrip() + SENSOR_METADATA_CONTRACT
            if SENSOR_TIME_ORIGIN_MARKER not in text:
                text = text.rstrip() + SENSOR_TIME_ORIGIN_CONTRACT
            message["text"] = text
            found = found or node.get("id")==CODE_GEN
    if not found:
        raise SystemExit(f"code-generation node {CODE_GEN} not found")
    print(json.dumps(graph, ensure_ascii=False))


if __name__ == "__main__":
    main()
