"""P1 衰减扩展：工作流代码生成 prompt 追加 介质衰减/吸收 示例（幂等）。

在代码生成节点（1783991079665）追加衰减示例：
- 时域 simulate_wave_propagation 忽略 attenuation（重要约束）
- 频域 helmholtz_solver 才是衰减的正解（Medium.attenuation, dB/幂律 y=2）
- 完整可执行示例 + 理论公式（Im(k)=ω²·db2neper(α,2)，振幅 ∝ exp(-Im(k)·r)）

用法: python3 _apply_atten_prompt.py <graph.json> > new_graph.json
"""

import json
import sys

ATTEN_SECTION = """

## 介质衰减/吸收（需求含"衰减/吸收/attenuation/吸收系数"时用！）
- ⚠️ jwave 0.2.1 的**时域** simulate_wave_propagation **忽略** Medium.attenuation，
  衰减只在**频域** helmholtz_solver（wavevector 算子）中生效。含衰减需求必须走频域！
- Medium 加衰减参数：medium = jw.Medium(domain, sound_speed=c, density=rho,
  attenuation=alpha_db, pml_size=20)（alpha_db 单位 dB，幂律 y=2，k-Wave 约定；
  0 = 无吸收）
- 频域求解完整示例（均匀吸收介质 + 点源）：
  from jwave.acoustics import helmholtz_solver, db2neper
  omega = 2 * jnp.pi * f0                      # f0 为需求频率
  src = jw.FourierSeries(jnp.zeros((N, N), dtype=jnp.complex64).at[c, c].set(1.0), domain)
  u = helmholtz_solver(medium, omega, src, method="gmres")
  p = jnp.asarray(u.on_grid).reshape(N, N)     # 复压力场，取模 jnp.abs(p)
- 理论参考：Im(k) = omega^2 * db2neper(alpha_db, 2.0)（neper/m），
  振幅沿传播距离 r 按 exp(-Im(k)*r) 衰减，2D 再加圆柱扩散 1/sqrt(r)
- 注意：helmholtz_solver 的 src 必须是 FourierSeries（不能传裸数组）；
  物理区半径 = N/2 - pml_size，探针/源不要放进 PML
"""

CODE_GEN = "1783991079665"


def main():
    g = json.load(open(sys.argv[1], encoding="utf-8"))
    marker = "## 介质衰减"
    for n in g.get("nodes", []):
        if n.get("id") == CODE_GEN:
            for m in n["data"].get("prompt_template", []):
                if m.get("role") == "user" and marker not in m["text"]:
                    m["text"] = m["text"].rstrip() + ATTEN_SECTION
    print(json.dumps(g, ensure_ascii=False))


if __name__ == "__main__":
    main()
