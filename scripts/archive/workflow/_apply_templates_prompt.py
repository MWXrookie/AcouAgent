"""A/B 优化 + 3D 修复：代码生成 prompt 两处幂等更新。

1) 追加"代码骨架速查" section（去冗余独有写法）
2) 修复内嵌场输出模板：3D 场取中心 z 切片输出 2D 字段
   （否则 3D 全场 JSON 超 40 万字符 → Dify 报错；analyze 也只认 2D 场）

用法: python3 _apply_templates_prompt.py <graph.json> > new_graph.json
"""

import json
import sys

CODE_GEN = "1783991079665"
SKELETON_MARKER = "## 代码骨架速查"

# 新场输出模板（3D 兼容：全时 max 后取中心 z 切片 → 2D）
NEW_FIELD_TEMPLATE = """参考实现（在仿真代码末尾追加；网格过大时自动降采样，保证 JSON 不超过 40 万字符）：
import json as __json
import jax.numpy as __jnp
__field = __jnp.max(__jnp.abs(p.params), axis=0)[..., 0]  # 全时最大 |p| 场 (Nx, Ny[, Nz])
if __field.ndim > 2:                                      # 3D 场取中心 z 切片 → 2D
    __field = __field[..., __field.shape[-1] // 2]
__maxp = float(__jnp.max(__field))
__step = 1
while __field.size / (__step * __step) > 40000:
    __step += 1
__field_out = __field[::__step, ::__step] if __step > 1 else __field
print("__ACOU_FIELD_START__")
print(__json.dumps({"shape": list(__field_out.shape), "kind": "field",
                    "downsample": __step, "max_pressure": __maxp,
                    "data": __field_out.tolist()}))
print("__ACOU_FIELD_END__")
print(f"最大压力: {__maxp:.6f}")"""

SKELETON_SECTION = """

## 代码骨架速查（完整模板见知识库文档，此处只给独有骨架；import/域构造同前）
- 2D 点源骨架：sources = jw.Sources((jnp.array([c]), jnp.array([c])),
    jnp.expand_dims(jw.signal_processing.tone_burst(1.0/time_axis.dt, f0, 3), 0), time_axis.dt, domain)
  p = simulate_wave_propagation(medium, time_axis, sources=sources, settings=S).params
  maxp = float(jnp.max(jnp.abs(p)))        # p.shape=(Nt,Nx,Ny,1)，取点 p[t,x,y,0]
- p0 高斯骨架：p0 = FourierSeries(A*jnp.exp(-((XX-cx*dx)**2+(YY-cy*dx)**2)/(2*sigma**2)), domain)
- 环形骨架：r=jnp.sqrt((XX-cx*dx)**2+(YY-cy*dx)**2);
  p0 = FourierSeries(A*jnp.exp(-((r-R0)**2)/(2*sigma**2)), domain)   # R0 < N/2-pml_size
- 异质介质骨架：c_map=jnp.where(r<R_cyst, c_cyst, c_bg);
  medium = jw.Medium(domain, sound_speed=c_map, density=1000.0, pml_size=20)
- 传感器：不要用 Sensors 类（位置约定有歧义），直接取全场 p[t_idx, x_idx, y_idx, 0]
- 峰值测量：cfl=0.1 + 窄时间窗 + 原始信号最大值（raw max），不要用 analytic_signal
"""


def _replace_field_template(text: str) -> str:
    """把 prompt 中旧场输出模板段替换为 3D 兼容版（幂等：已含中心切片则跳过）。"""
    old_marker = "参考实现（在仿真代码末尾追加；网格过大时自动降采样"
    new_marker = "if __field.ndim > 2:"
    if old_marker not in text:
        return text
    if new_marker in text:
        return text  # 已修复
    # 找到旧模板段起点与终点（到下一个空行后的 ## 或结尾）
    start = text.index(old_marker)
    # 段结束：找该段后第一个 "\n\n" 后跟非空 或 结尾
    end = len(text)
    for kw in ["\n## ", "\n\n## "]:
        j = text.find(kw, start)
        if j > 0:
            end = min(end, j)
            break
    # 向后找到模板代码的 print 结束
    tail_pos = text.find('print(f"最大压力: {__maxp:.6f}")', start)
    if tail_pos > 0:
        end = min(end, tail_pos + len('print(f"最大压力: {__maxp:.6f}")'))
    return text[:start] + NEW_FIELD_TEMPLATE + text[end:]


def main():
    g = json.load(open(sys.argv[1], encoding="utf-8"))
    for n in g.get("nodes", []):
        if n.get("id") == CODE_GEN:
            for m in n["data"].get("prompt_template", []):
                if m.get("role") == "user":
                    m["text"] = _replace_field_template(m["text"])
                    if SKELETON_MARKER not in m["text"]:
                        m["text"] = m["text"].rstrip() + SKELETON_SECTION
    print(json.dumps(g, ensure_ascii=False))


if __name__ == "__main__":
    main()
