"""P1 3D 扩展：工作流代码生成 prompt 追加 3D 仿真示例（幂等）。

在 T-008/T-009 工作流代码生成节点（1783991079665）追加 3D 示例：
- 3D Domain 构造（dx 标量或列表）
- 3D meshgrid + 高斯球 p0
- 3D 点源 Sources（三坐标数组）
- 3D 内存红线（N ≤ 72）

用法: python3 _apply_3d_prompt.py <graph.json> > new_graph.json
"""

import json
import sys

D3_SECTION = """

## 3D 仿真（需求含"三维/3D/立体/球面波/空间分布"时用！）
- 3D 域：jw.Domain((N, N, N), (dx, dx, dx))，dx 可用标量或列表 (dx, dx, dx)
- 3D 网格坐标（必须 meshgrid, indexing='ij'）：
  x = jnp.linspace(0, N*dx, N); y = jnp.linspace(0, N*dx, N); z = jnp.linspace(0, N*dx, N)
  XX, YY, ZZ = jnp.meshgrid(x, y, z, indexing='ij')
- 3D 高斯球初始压力：
  p0_grid = A * jnp.exp(-((XX-cx)**2 + (YY-cy)**2 + (ZZ-cz)**2) / (2*sigma**2))
  p0 = FourierSeries(p0_grid, domain)
  p = simulate_wave_propagation(medium, time_axis, p0=p0)
- 3D 点源（3 组整数坐标数组，各长度=源数）：
  sources = jw.Sources((jnp.array([cx]), jnp.array([cy]), jnp.array([cz])), signals, dt, domain)
  p = simulate_wave_propagation(medium, time_axis, sources=sources)
- 全场压力 p = simulate_wave_propagation(...).params，形状 (Nt, Nx, Ny, Nz, 1)，
  取点用 p[t_idx, x_idx, y_idx, z_idx, 0]
- ⚠️ 3D 内存红线：全场 float32 = N³ × Nt × 4B。executor 上限 4GB：
  建议 N ≤ 72（72³ × 1000 步 ≈ 1.1GB），N=96 已接近上限易 OOM；
  t_end 对应 Nt = t_end / (cfl*dx/c)，别让 N³×Nt 超过 ~2e9 字节
- 探针/初始场不要放进 PML：物理区半径 = N/2 - pml_size，环形/球壳半径须小于此值
"""

CODE_GEN = "1783991079665"


def main():
    g = json.load(open(sys.argv[1], encoding="utf-8"))
    marker = "## 3D 仿真"
    for n in g.get("nodes", []):
        if n.get("id") == CODE_GEN:
            for m in n["data"].get("prompt_template", []):
                if m.get("role") == "user" and marker not in m["text"]:
                    m["text"] = m["text"].rstrip() + D3_SECTION
    print(json.dumps(g, ensure_ascii=False))


if __name__ == "__main__":
    main()
