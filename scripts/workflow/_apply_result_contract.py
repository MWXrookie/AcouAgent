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


def main() -> None:
    graph = json.load(open(sys.argv[1], encoding="utf-8"))
    found = False
    for node in graph.get("nodes", []):
        if node.get("id") != CODE_GEN:
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
            message["text"] = text
            found = True
    if not found:
        raise SystemExit(f"code-generation node {CODE_GEN} not found")
    print(json.dumps(graph, ensure_ascii=False))


if __name__ == "__main__":
    main()
