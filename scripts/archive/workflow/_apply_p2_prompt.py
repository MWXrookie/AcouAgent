"""P2 修复：工作流代码生成 prompt 追加 p0 初始压力示例（幂等）。

用法: python3 _apply_p2_prompt.py <graph.json> > new_graph.json
"""

import json
import sys

P0_SECTION = """

## p0 初始压力（需求含"初始压力/高斯脉冲/环形/椭圆压力"时必须用 p0 参数！）
- simulate_wave_propagation 接受的关键字是 p0（不存在 initial_pressure= 之类的参数！）
- 构造：p0 = FourierSeries(p0_grid, domain)，p0_grid 形状 (Nx, Ny)
- 调用：p = simulate_wave_propagation(medium, time_axis, p0=p0)（不要再传 sources）

网格坐标（必须用 meshgrid, indexing='ij'）：
x = jnp.linspace(0, Nx*dx, Nx); y = jnp.linspace(0, Ny*dx, Ny)
XX, YY = jnp.meshgrid(x, y, indexing='ij')

高斯初始压力：
p0_grid = A * jnp.exp(-((XX-cx)**2 + (YY-cy)**2) / (2*sigma**2))
p0 = FourierSeries(p0_grid, domain)

环形初始压力（R0 = 环半径，须小于域半宽）：
r = jnp.sqrt((XX-cx)**2 + (YY-cy)**2)
p0_grid = A * jnp.exp(-((r-R0)**2) / (2*sigma**2))
p0 = FourierSeries(p0_grid, domain)

椭圆初始压力（a、b = 长短半轴）：
p0_grid = A * jnp.exp(-(((XX-cx)/a)**2 + ((YY-cy)/b)**2) / 2)
p0 = FourierSeries(p0_grid, domain)
"""

CODE_GEN = "1783991079665"


def main():
    g = json.load(open(sys.argv[1], encoding="utf-8"))
    marker = "## p0 初始压力"
    for n in g.get("nodes", []):
        if n.get("id") == CODE_GEN:
            for m in n["data"].get("prompt_template", []):
                if m.get("role") == "user" and marker not in m["text"]:
                    m["text"] = m["text"].rstrip() + P0_SECTION
    print(json.dumps(g, ensure_ascii=False))


if __name__ == "__main__":
    main()
