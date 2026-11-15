# jwave 声学仿真知识库

### Medium 类 — 计算域定义

`Medium` 定义声学传播介质的物理属性：声速、密度、衰减系数和 PML 边界厚度。它是 jwave 仿真中最基础的配置对象，几乎所有算子和求解器都需要传入 `Medium` 实例。

**Import**: `from jwave import Medium`

| 方法 | 签名 | 说明 |
|------|------|------|
| `max_sound_speed` | `()` | 计算并返回介质中的最大声速 |
| `min_sound_speed` | `()` | 计算并返回介质中的最小声速 |
| `max_density` | `()` | 计算并返回介质中的最大密度 |
| `min_density` | `()` | 计算并返回介质中的最小密度 |
| `max_attenuation` | `()` | 计算并返回介质中的最大衰减系数 |
| `min_attenuation` | `()` | 计算并返回介质中的最小衰减系数 |
| `int_pml_size` | `()` | 返回 PML 层的整数尺寸 |

**使用示例**:
```python
domain = jw.Domain((128, 128), (0.1e-3, 0.1e-3))
medium = jw.Medium(domain, sound_speed=1500.0, density=1000.0, pml_size=20)
```

### TimeAxis 类 — 计算域定义

`TimeAxis` 定义仿真的时间跨度和步长。通过 `from_medium` 类方法可根据介质和 CFL 条件自动计算合适的时间步长，是时域仿真中连接空间离散化与时间推进的桥梁。

**Import**: `from jwave import TimeAxis`

| 方法 | 签名 | 说明 |
|------|------|------|
| `Nt` | `()` | 返回时间步数 |
| `to_array` | `()` | 将时间轴返回为数组 |
| `from_medium` | `(medium: Medium, cfl: float = 0.3, t_end = None)` | 从 `Medium` 和 CFL 条件构造 `TimeAxis` 对象 |

**使用示例**:
```python
time_axis = jw.TimeAxis.from_medium(medium, cfl=0.3, t_end=40e-6)  # 仿真 40 μs
```

### DistributedTransducer 类 — 激励源

`DistributedTransducer` 模拟分布式超声换能器，支持发射和接收双向操作。与点源 `Sources` 不同，它可以定义具有一定几何形状（如线段、矩形）的换能器，更接近真实超声探头的行为。

**Import**: `from jwave import DistributedTransducer`

| 方法 | 签名 | 说明 |
|------|------|------|
| `set_signal` | `(s)` | 设置换能器的激励信号 |
| `set_mask` | `(m)` | 设置换能器的空间掩码（几何形状） |
| `on_grid` | `(n)` | 返回第 n 个时间步换能器在网格上的场分布 |

### Sources 类 — 激励源

`Sources` 通过点源位置和时域信号定义声波发射源。每个点源有独立的信号时间序列，源的位置在网格点上。适用于点源激励场景，如超声成像中的点散射体仿真。

**Import**: `from jwave import Sources`

| 方法 | 签名 | 说明 |
|------|------|------|
| `to_binary_mask` | `(N)` | 将源位置转换为二值掩码 |
| `on_grid` | `(n)` | 返回第 n 个时间步源在网格上的场分布 |
| `no_sources` | `(domain)` | 返回一个空源（无激励） |

> **⚠️ CRITICAL: Sources API 正确用法（已验证）**

Sources 构造函数接受 **4 个位置参数**，不能使用关键字参数：

```python
# ✅ 正确: 4 个位置参数
sources = Sources(positions, signals, dt, domain)

# ❌ 错误: 使用关键字参数会导致 TypeError
sources = Sources(positions=positions, signals=signals, dt=dt, domain=domain)
```

**positions 必须是整数坐标**:
```python
import jax.numpy as jnp
# ✅ 正确: Python int → jnp 推断为 int32
positions = (jnp.array([64]), jnp.array([64]))

# ❌ 错误: 浮点数坐标 → JAX .at[] 索引在 JIT 中静默失败 → 压力场全零!
positions = (jnp.array([64.0]), jnp.array([64.0]))
```

> 根因: `on_grid(n)` 中 `src.at[self.positions].add(signals)` 要求索引为整数类型。浮点数索引在 JIT 编译中被吞没而不报错，导致 `mass_src_field` 始终为 0。

**signals 必须是 2D jnp array**:
```python
import jax.numpy as jnp
# ✅ 正确: 2D array, shape=(num_sources, Nt)
signal_1d = jnp.sin(2 * jnp.pi * freq * t)
signals = jnp.expand_dims(signal_1d, 0)  # (1, Nt)

# ❌ 错误: list 或 tuple
signals = [signal]   # TypeError
signals = (signal,)  # TypeError
```

**使用示例**:
```python
import jax.numpy as jnp
dt = time_axis.dt
t = time_axis.to_array()
cx, cy = Nx // 2, Ny // 2
positions = (jnp.array([cx]), jnp.array([cy]))          # 整数坐标
signal_1d = jnp.sin(2 * jnp.pi * 5e6 * t)               # 1D
signals = jnp.expand_dims(signal_1d, 0)                  # 2D: (1, Nt)
sources = Sources(positions, signals, dt, domain)        # 4 个位置参数
```

### TimeHarmonicSource 类 — 激励源

`TimeHarmonicSource` 用于频域仿真中的单频（时谐）点源。与 `Sources` 的宽带时域信号不同，它产生单一角频率 $\omega$ 的复值源场，直接用于 Helmholtz 方程求解。

**Import**: `from jwave import TimeHarmonicSource`

| 方法 | 签名 | 说明 |
|------|------|------|
| `on_grid` | `(t = 0.0)` | 返回时谐源在网格上的复值场 |
| `from_point_sources` | `(domain, x, y, value, omega)` | 从点源坐标和频率构造时谐源 |

**使用示例**:
```python
import jax.numpy as jnp
omega = 2 * jnp.pi * 1e6  # 1 MHz 角频率
source = jw.TimeHarmonicSource.from_point_sources(
    domain, x=[64], y=[64], value=[1.0], omega=omega
)
```

### BLISensors 类 — 传感器与输出

`BLISensors`（Band-Limited Interpolant Sensors）支持在离网位置（off-grid）精确采集声压信号。通过带限插值函数，它可以在非网格点位置获得高精度的声压值，适用于需要精确传感器定位的场景（如阵列信号处理）。

**Import**: `from jwave import BLISensors`

| 方法 | 签名 | 说明 |
|------|------|------|
| `__call__` | `(p, u, rho)` | 在传感器位置采集压力场、速度场和密度场数据 |

**核心机制**: `BLISensors` 实现了 `__call__` 方法，接收压力场 `p`、粒子速度场 `u` 和密度场 `rho`，返回传感器位置的信号。底层使用 `bli_function` 进行带限插值，避免离网采样导致的混叠误差。

### Sensors 类 — 传感器与输出

`Sensors` 在网格点位置上直接采样压力值。与 `BLISensors` 不同，它只能放置在离散网格点坐标上，不进行插值。适用于对传感器位置精度要求不高的场景，或网格分辨率足够高的场景。

**Import**: `from jwave import Sensors`

| 方法 | 签名 | 说明 |
|------|------|------|
| `to_binary_mask` | `(N)` | 将传感器位置转换为二值掩码 |
| `__call__` | `(p, u, rho)` | 在传感器位置采集场数据 |

### TimeWavePropagationSettings 类 — 时域仿真

`TimeWavePropagationSettings` 配置时域波传播求解器的参数设置。它控制求解器的数值行为，如时间步进方案、空间离散化选项等。

**Import**: `from jwave import TimeWavePropagationSettings`

### simulate_wave_propagation 函数 — 时域仿真主入口

`simulate_wave_propagation` 是 jwave 时域仿真的核心入口函数，等价于 k-Wave 工具箱的 `kspaceFirstOrderND`。它使用 k-space 伪谱法（PSTD）在时间轴上推进声波传播，通过交错网格分别求解动量守恒和质量守恒方程。

**Import**: `from jwave import simulate_wave_propagation`
**公共 API**: ✓

**OnGrid 版本签名**: `(medium: Medium[OnGrid], time_axis: TimeAxis)`

**FourierSeries 版本签名**: `(medium: Medium[FourierSeries], time_axis: TimeAxis)`

**共用参数说明**:

- `medium`: 声学介质对象。
- `time_axis`: 时间轴对象，定义仿真时间跨度和步长。
- `sources`: 源项。需实现 `sources.on_grid(n)` 方法，返回第 n 个时间步的源场。
- `sensors`: 传感器项。需实现 `sensors(p, u, rho)` 可调用接口，返回记录的场数据。如果未指定，则记录整个压力场。
- `u0`: 初始速度场。若为 `None`，则根据 `p0` 自动设置以确保 $u(t=0)=0$。注意速度场相对于压力场在时间上错开半个步长。
- `p0`: 初始压力场。若为 `None`，设为零。
- `checkpoint`: 是否在每个时间步使用 jax.checkpoint 以节省显存。
- `smooth_initial`: 是否对初始条件进行平滑处理。

**返回值**: 传感器在每个时间步的记录序列 `ys`。**默认未传 `sensors` 时记录的就是压力场 `p`**（源码里 `sensors = lambda p, u, rho: p`），所以 `result[-1]` 已经是最终压力场，**不需要**再调用 `pressure_from_density`；只有当你显式持有 PSTD 内部的密度场 `rho` 时才需要`pressure_from_density(rho, medium)` 做转换。

> 模块内存在两个重载版本（OnGrid 和 FourierSeries），分别对应不同的离散化方式。FourierSeries 版本支持 k-space 算子校正色散误差。

**使用示例**:
```python
result = jw.simulate_wave_propagation(medium, time_axis, sources=sources, sensors=sensors)
```

### momentum_conservation_rhs 函数 — 时域仿真动量守恒

`momentum_conservation_rhs` 实现交错网格上的动量守恒方程右端项计算。它将压力梯度转换为速度场的时间导数，是 PSTD 求解器中两个核心物理方程之一（另一个是质量守恒方程）。

**Import**: `from jwave import momentum_conservation_rhs`
**公共 API**: ✓

**OnGrid 版本签名**: `(p: OnGrid, u: OnGrid, medium: Medium) -> OnGrid`

- `p`: 压力场
- `u`: 速度场
- `medium`: 介质对象
- `c_ref`/`dt`/`params`: 在此版本中**未使用**

**FourierSeries 版本签名**: `(p: FourierSeries, u: FourierSeries, medium: Medium) -> FourierSeries`

- `p`: 压力场
- `u`: 速度场
- `medium`: 介质对象
- `c_ref`: 参考声速，用于计算 k-space 算子
- `dt`: 时间步长，用于计算 k-space 算子
- `params`: 算子参数

> 两个重载版本的关键区别：FourierSeries 版本使用 c_ref 和 dt 计算 k-space 色散校正因子，OnGrid 版本不做此校正。

### mass_conservation_rhs 函数 — 时域仿真质量守恒

`mass_conservation_rhs` 实现质量守恒方程（连续性方程）的右端项计算。它将速度散度转换为密度场的时间导数，同时计入质量源项。与 `momentum_conservation_rhs` 一起构成 PSTD 求解器的完整物理模型。

**Import**: `from jwave import mass_conservation_rhs`
**公共 API**: ✓

**OnGrid 版本签名**: `(p: OnGrid, u: OnGrid, mass_source: object, medium: Medium) -> OnGrid`

- `p`: 压力场
- `u`: 速度场
- `mass_source`: 质量源项
- `medium`: 介质对象
- `c_ref`/`dt`/`params`: 在此版本中**未使用**

**FourierSeries 版本签名**: `(p: FourierSeries, u: FourierSeries, mass_source: object, medium: Medium) -> FourierSeries`

- `p`: 压力场
- `u`: 速度场
- `mass_source`: 质量源项
- `medium`: 介质对象
- `c_ref`: 参考声速，用于 k-space 算子
- `dt`: 时间步长，用于 k-space 算子
- `params`: 算子参数

### pressure_from_density 函数 — 时域仿真

`pressure_from_density` 将仿真输出的声学密度场转换为压力场。在 PSTD 求解器中，主变量之一是密度 $\rho$，此函数通过介质状态方程 $\rho = \rho_0 + p/c_0^2$ 反推出压力。

**Import**: `from jwave import pressure_from_density`
**公共 API**: ✓
**签名**: `(rho: Field, medium: Medium) -> Field`

**参数**:

- `rho`: 密度场（仿真原始输出）
- `medium`: 介质对象
- `params`: 算子参数，**未使用**

**返回值**: Field: 压力场。

### laplacian_with_pml 函数 — 频域仿真带 PML 拉普拉斯算子

`laplacian_with_pml` 计算带有完美匹配层（PML）吸收边界的拉普拉斯算子。它是频域 Helmholtz 求解器的核心组件，PML 确保在有限计算域中模拟无界声传播而不产生边界反射。支持四种离散化方式的重载。

**Import**: `from jwave import laplacian_with_pml`
**公共 API**: ✓

四个重载版本分别对应不同场类型：

| 版本 | 签名 | 适用场景 |
|------|------|----------|
| Continuous | `(u: Continuous, medium: Medium) -> Continuous` | 连续域理论分析 |
| OnGrid | `(u: OnGrid, medium: Medium) -> OnGrid` | 标准有限差分网格 |
| FiniteDifferences | `(u: FiniteDifferences, medium: Medium) -> FiniteDifferences` | 显式有限差分格式 |
| FourierSeries | `(u: FourierSeries, medium: Medium) -> FourierSeries` | 谱方法（推荐，精度最高） |

**共用参数**:

- `u`: 复值声场
- `medium`: 介质对象（含 PML 参数）
- `omega`: 角频率
- `params`: 算子参数

**选择建议**: 一般仿真推荐使用 **FourierSeries** 版本，精度最高且支持 k-space 校正。OnGrid 版本用于简单场景，FiniteDifferences 用于需要显式控制差分模板的场景。

### wavevector 函数 — 频域仿真波矢算子

`wavevector` 计算 Helmholtz 方程中的波矢项 $k^2 = (\omega/c_0 + i\alpha_0)^2$。它是 Helmholtz 算子 $(\nabla^2 + k^2)u = s$ 中除拉普拉斯项外的另一核心组成部分。

**Import**: `from jwave import wavevector`
**公共 API**: ✓
**签名**: `(u: Field, medium: Medium) -> Field`

**参数**:

- `u`: 复值场
- `medium`: 介质对象，包含 $\alpha_0$（衰减）和 $c_0$（声速）
- `omega`: 角频率
- `params`: 算子参数

**返回值**: Field: 施加波矢算子后的场。

### helmholtz 函数 — 频域仿真 Helmholtz 算子

`helmholtz` 计算完整的 Helmholtz 算子 $\mathcal{H}[u] = \nabla^2 u + k^2 u$（含 PML）。它是 `laplacian_with_pml` 与 `wavevector` 的组合，是频域声学仿真的核心 PDE 算子。

**Import**: `from jwave import helmholtz`
**公共 API**: ✓

**Field 版本签名**: `(u: Field, medium: Medium) -> Field`
- `params`: **未使用**

**OnGrid 版本签名**: `(u: OnGrid, medium: Medium) -> OnGrid`
- `params`: 算子参数

**参数**:
- `u`: 复值声场
- `medium`: 介质对象
- `omega`: 角频率

### angular_spectrum 函数 — 频域仿真角谱法

`angular_spectrum` 实现角谱法（Angular Spectrum Method），用于将平面上的声压场传播到另一深度位置。等价于 k-Wave 工具箱的 `angularSpectrumCW`，适用于分层介质中的前向声传播问题（如超声换能器的声场投射）。

**Import**: `from jwave import angular_spectrum`
**公共 API**: ✓
**签名**: `(pressure: FourierSeries) -> FourierSeries`

**参数**:

- `pressure`: 输入平面上的复值声压场 $[Pa]$
- `z_pos`: 投影平面的相对 z 位置
- `f0`: 输入平面的频率
- `medium`: 介质对象（定义声速、密度和吸收）
- `padding`: 用于角谱计算的网格扩展量，默认 0
- `angular_restriction`: 是否使用角度限制方法（Zeng & McGough, 2008），默认 True
- `unpad_output`: 是否将输出裁剪回输入尺寸，默认 True

### born_series 函数 — 频域仿真收敛 Born 级数求解器

`born_series` 使用收敛 Born 级数（CBS）方法求解非均匀介质中的 Helmholtz 方程。CBS 是一种迭代方法，通过 Neumann 级数展开求解散射问题，适用于中等对比度的非均匀介质。

**Import**: `from jwave import born_series`
**公共 API**: ✓
**签名**: `(medium: Medium, src: FourierSeries) -> FourierSeries`

**参数**:

- `medium`: 声学介质。注意：`density` 项被忽略（原论文未包含密度项）；`attenuation` 也被忽略（尚未实现损耗介质）。
- `src`: 复值源场
- `omega`: 角频率
- `k0`: 波数。若为 None, 按 $k_0 = 0.5(\max(k^2) + \min(k^2))$ 自动计算（Osnabrugge et al, 2016）
- `max_iter`: 最大迭代次数
- `tol`: 相对收敛容差
- `alpha`: PML 振幅参数
- `remove_pml`: 是否从解中移除 PML 区域，默认 True
- `print_info`: 是否打印收敛信息，默认 False

**返回值**: FourierSeries: 复值解场。

**适用场景**: 适用于非均匀介质的散射问题，对比度不太高时收敛快速。对比度极高时建议改用 `helmholtz_solver` 的 GMRES 方法。

### born_iteration 函数 — 频域仿真 Born 级数单步迭代

`born_iteration` 实现收敛 Born 级数方法的一步迭代 $u_{k+1} = u_k - \epsilon G V u_k$，其中 $G$ 是格林算子，$V$ 是散射势。

**Import**: `from jwave import born_iteration`
**公共 API**: ✓
**签名**: `(field: Field, k_sq: Field, src: Field) -> FourierSeries`

**参数**:

- `field`: 当前场 $u_k$
- `k_sq`: 非均匀波数平方 $k^2(x)$
- `src`: 复值源场 $s$
- `k0`: 波数（用于格林算子）
- `epsilon`: 预条件器的吸收参数

**返回值**: FourierSeries: 更新后的场 $u_{k+1}$。

### scattering_potential 函数 — 频域仿真散射势

`scattering_potential` 计算 CBS 方法中的散射势 $V(x) = k^2(x) - k_0^2$，表示非均匀介质波数相对于背景波数的偏差。

**Import**: `from jwave import scattering_potential`
**公共 API**: ✓
**签名**: `(field: Field, k_sq: Field) -> Field`

**参数**:

- `field`: 当前场 $u$
- `k_sq`: 非均匀波数平方 $k^2(x)$
- `k0`: 背景波数
- `epsilon`: 吸收参数

**返回值**: FourierSeries: 散射势场。

### homogeneous_helmholtz_green 函数 — 频域仿真格林算子

`homogeneous_helmholtz_green` 实现均匀 Helmholtz 方程的格林算子 $\mathcal{G} = (-\nabla^2 - k_0^2 - i\epsilon)^{-1}$。在 CBS 方法中作为预条件器使用，通过傅里叶域对角化高效计算。

**Import**: `from jwave import homogeneous_helmholtz_green`
**公共 API**: ✓
**签名**: `(field: FourierSeries)`

**参数**:

- `field`: 输入场 $u$
- `k0`: 波数
- `epsilon`: 吸收参数

**返回值**: FourierSeries: 格林算子作用后的场。

### rayleigh_integral 函数 — 频域仿真 Rayleigh 积分

`rayleigh_integral` 通过 Rayleigh 积分公式将平面上的声压场计算到空间中任意点的声压值。适用于计算换能器在空间中的辐射声场分布。

**Import**: `from jwave import rayleigh_integral`
**公共 API**: ✓
**签名**: `(pressure: FourierSeries)`

**参数**:

- `pressure`: 平面上对应于 $u$ 的声压场
- `r`: 距离声压平面的距离，必须为 3D 数组
- `f0`: 源频率
- `sound_speed`: 均匀介质的声速，默认 1500 m/s

**返回值**: complex64: 位置 `r` 处的 Rayleigh 积分值。

### helmholtz_solver 函数 — 频域仿真通用线性求解器

`helmholtz_solver` 是频域 Helmholtz 方程的通用迭代求解器。基于 GMRES 或 BiCGSTAB 等 Krylov 子空间方法求解大规模稀疏线性系统 $\mathcal{H}u = s$，适用于任意非均匀介质。

**Import**: `from jwave import helmholtz_solver`
**公共 API**: ✓
**签名**: `(medium: Medium, omega: object, source: OnGrid)`

**参数**:

- `medium`: 声学介质
- `omega`: 角频率
- `source`: 源场
- `guess`: 迭代求解器的初始猜测解，默认 None
- `method`: 求解方法，默认 `'gmres'`（也可选 `'bicgstab'`）
- `checkpoint`: 是否使用梯度检查点节省显存，默认 True
- `params`: 算子参数，默认 None

**方法选择**: `gmres` 适用于一般非对称系统，收敛稳定；`bicgstab` 在某些场景下收敛更快但可能不稳定。建议先用 `gmres`，若收敛慢再试 `bicgstab`。

### complex_pml 函数 — 边界条件

`complex_pml` 为连续域（Continuous）复值场计算 PML 吸收边界。PML 通过在边界区域引入复数坐标拉伸，使向外传播的波在 PML 内指数衰减而不反射。

**Import**: `from jwave import complex_pml`

### complex_pml_on_grid 函数 — 边界条件

`complex_pml_on_grid` 为离散网格上的频域仿真计算 PML 吸收层。返回一个与网格同形的数组，在 PML 区域为非零值，内部区域为零。频域 Helmholtz 求解器自动调用此函数。

**Import**: `from jwave import complex_pml_on_grid`
**签名**: `(medium: Medium, omega: float, exponent = 4.0, alpha_max = 2.0, shift = 0.0) -> jnp.ndarray`

**参数**:
- `medium`: 介质对象（从中提取 PML 尺寸）
- `omega`: 角频率
- `exponent`: PML 衰减的幂次，默认 4.0
- `alpha_max`: 最大衰减系数，默认 2.0
- `shift`: PML 坐标偏移，默认 0.0

### td_pml_on_grid 函数 — 边界条件

`td_pml_on_grid` 为时域仿真计算 PML 吸收层。与频域 PML 不同，时域 PML 需要根据时间步长调整衰减参数，确保在宽频范围内有效吸收。

**Import**: `from jwave import td_pml_on_grid`
**签名**: `(medium: Medium, dt: float, exponent = 4.0, alpha_max = 2.0, c0 = 1.0, dx = 1.0, coord_shift = 0.0) -> jnp.ndarray`

**参数**:
- `medium`: 介质对象
- `dt`: 时间步长（用于调整频率响应）
- `exponent`: PML 衰减幂次，默认 4.0
- `alpha_max`: 最大衰减系数，默认 2.0
- `c0`: 参考声速，默认 1.0
- `dx`: 网格间距，默认 1.0

**PML 参数选择建议**: `pml_size` 一般取 10-20 个网格点。`exponent` 越大 PML 衰减越陡峭，但数值反射可能增加，4.0 是经验最优值。对于宽频时域仿真，适当增大 `pml_size` 比调整 `alpha_max` 更有效。

### tone_burst 函数 — 信号处理

`tone_burst` 生成脉冲式正弦波激励信号（Tone Burst）。输出是一个由 `num_cycles` 个正弦周期组成的脉冲，包络为高斯窗或 Hann 窗，是超声仿真中最常用的激励信号形式。

**Import**: `from jwave.signal_processing import tone_burst`
**签名**: `(sample_freq: float, signal_freq: float, num_cycles: float) -> Array`

**参数**:

- `sample_freq`: 采样频率 [Hz]。**注意应传 `1/dt`，不是 `dt`**（例如 `tone_burst(1/time_axis.dt, 5e6, 3)`）
- `signal_freq`: 信号中心频率 [Hz]
- `num_cycles`: 脉冲中的正弦周期数。周期越多，带宽越窄；周期越少，带宽越宽

**返回值**: jnp.ndarray: Tone burst 信号数组。

**参数选择建议**: `num_cycles=3` 是常用值，平衡了时域分辨率和频域带宽。对于需要宽带激励的场景（如高分辨率成像），可降至 1-2；对于窄带场景（如稳态分析），可增至 5-10。

**使用示例**:
```python
dt = time_axis.dt
signal = jw.signal_processing.tone_burst(1/dt, 5e6, 3)  # 5 MHz, 3周期脉冲
```

### analytic_signal 函数 — 信号处理

`analytic_signal` 通过 Hilbert 变换从实信号计算解析信号 $s_a(t) = s(t) + i\mathcal{H}[s(t)]$。解析信号的模值即为信号的包络，广泛用于超声信号的包络检波和到达时间提取。

**Import**: `from jwave.signal_processing import analytic_signal`
**签名**: `(x: Array, axis: int = -1) -> Array`

**参数**:

- `x`: 输入实信号
- `axis`: 沿哪个轴计算 Hilbert 变换，默认 -1（最后一维，通常是时间轴）

**返回值**: jnp.ndarray: 复值解析信号。

**使用示例**:
```python
import jax.numpy as jnp
analytic = jw.signal_processing.analytic_signal(recorded_signals, axis=-1)
envelope = jnp.abs(analytic)  # 包络检波
```

### fourier_downsample 函数 — 信号处理

`fourier_downsample` 在频域对信号进行降采样。与时域直接抽取不同，频域降采样先对信号做 FFT，截断高频分量后再做 IFFT，可以避免时域降采样导致的频谱混叠。

**Import**: `from jwave.signal_processing import fourier_downsample`
**签名**: `(x: Array, subsample: int = 2, discard_last: bool = True) -> Array`

**参数**:

- `x`: 待降采样信号
- `subsample`: 降采样因子，默认 2
- `discard_last`: 是否不对最后一维降采样（保留时间轴分辨率），默认 True

**返回值**: jnp.ndarray: 降采样后的信号。

**使用场景**: 仿真输出采样率通常远高于信号带宽所需，使用此函数可在保留信号特征的条件下减少数据量。

### fourier_upsample 函数 — 信号处理

`fourier_upsample` 在频域对信号进行升采样。通过频域补零然后 IFFT 实现，等效于时域的 sinc 插值，可平滑地增加信号的采样点数。

**Import**: `from jwave.signal_processing import fourier_upsample`
**签名**: `(x: Array, upsample: int = 2, discard_last: bool = True) -> Array`

**参数**:

- `x`: 待升采样信号
- `upsample`: 升采样因子，默认 2
- `discard_last`: 是否不对最后一维升采样，默认 True

### apply_ramp 函数 — 信号处理

`apply_ramp` 对激励信号施加平滑的启动斜坡（Ramp），避免信号从零突然跳变导致的数值伪影。使用指数斜坡 $s(t) \cdot (1 - e^{-t/\sigma})$ 实现平滑启动。

**Import**: `from jwave.signal_processing import apply_ramp`
**签名**: `(signal: Array, dt: float, center_freq: float, warmup_cycles: float = 3) -> Array`

**参数**:

- `signal`: 输入信号 $s(t)$
- `dt`: 时间步长
- `center_freq`: 中心频率 $f_0$
- `warmup_cycles`: 暖启动周期数 $\sigma$，默认 3（即信号在 3 个周期内从 0 平滑过渡到满幅）

### smooth 函数 — 信号处理

`smooth` 在频域对 n 维信号进行平滑处理。通过将信号的傅里叶变换乘以 Blackman 窗实现低通滤波，有效抑制高频噪声和吉布斯现象。

**Import**: `from jwave.signal_processing import smooth`
**签名**: `(x: Array, exponent: float = 1.0) -> Array`

**参数**:

- `x`: 输入信号
- `exponent`: 平滑指数，越大平滑越强，默认 1.0

### blackman 函数 — 信号处理

`blackman` 生成长度为 N 的 Blackman 窗函数。Blackman 窗具有优秀的旁瓣抑制能力（-58 dB），在频谱分析和信号平滑中被广泛使用。

**Import**: `from jwave.signal_processing import blackman`
**签名**: `(N: int) -> Array`

### gaussian_window 函数 — 信号处理

`gaussian_window` 返回高斯窗调制信号 $s(t) \cdot \exp(-(t-\mu)^2 / (2\sigma^2))$。用于信号的时域选通或生成高斯包络脉冲。

**Import**: `from jwave.signal_processing import gaussian_window`
**签名**: `(signal: Array, time: Array, mu: float, sigma: float) -> Array`

**参数**:

- `signal`: 原始信号 $s(t)$
- `time`: 时间数组 $t$
- `mu`: 高斯窗中心位置 $\mu$
- `sigma`: 高斯窗宽度 $\sigma$

### smoothing_filter 函数 — 信号处理

`smoothing_filter` 返回一个基于 Blackman 窗的平滑滤波器工厂函数。接收示例信号以确定滤波器的尺寸和形状，返回一个可调用的平滑函数。

**Import**: `from jwave.signal_processing import smoothing_filter`
**签名**: `(sample_input: jnp.ndarray) -> Callable`

### show_field 函数 — 可视化

`show_field` 绘制二维实值声场。色图范围从 `-vmax` 到 `vmax`，默认使用 divergent colormap（红蓝）显示正负压力值。是仿真结果快速检视最常用的工具。

**Import**: `from jwave.utils import show_field`
**签名**: `(x: Field, title: str = '', figsize: Tuple[int, int] = (8, 6), vmax=None, aspect: str = 'auto')`

**参数**:

- `x`: 待绘制的场
- `title`: 图标题，默认空
- `figsize`: 图像尺寸，默认 (8, 6)
- `vmax`: 色图最大值，默认 None（自动取场的最大绝对值）
- `aspect`: 图像纵横比，默认 "auto"

**使用示例**:
```python
jw.utils.show_field(result[-1], 'Final pressure field')
```

### display_complex_field 函数 — 可视化

`display_complex_field` 同时显示复值场的实部和模值（绝对值）。生成两个并排子图：左图为实部（Re），右图为模值（|u|）。适用于频域仿真结果（Helmholtz 解为复值场）的可视化。

**Import**: `from jwave.utils import display_complex_field`
**签名**: `(field, figsize=(15, 8), max_intensity=None) -> Tuple[Figure, np.ndarray]`

**参数**:

- `field`: 复值场
- `figsize`: 图像尺寸，默认 (15, 8)
- `max_intensity`: 最大显示强度，默认 None（自动取最大绝对值）

**使用示例**:
```python
jw.utils.display_complex_field(field)
```

### plot_comparison 函数 — 可视化

`plot_comparison` 并排比较两个二维场，并显示它们的差异。生成三列子图：场1、场2、差异（场1 - 场2）。用于验证求解器精度或比较不同参数下的仿真结果。

**Import**: `from jwave.utils import plot_comparison`
**签名**: `(field1, field2, title='', names=('',''), cmap='seismic', vmin=None, vmax=None) -> Figure`

**参数**:

- `field1`: 第一个场
- `field2`: 第二个场
- `title`: 图标题
- `names`: 两个场的名称标签，默认 `('','')`
- `cmap`: 色图，默认 'seismic'
- `vmin`/`vmax`: 色图范围

### load_image_to_numpy 函数 — 可视化

`load_image_to_numpy` 从文件路径加载图像并转换为 numpy 数组。用于将外部图像（如 CT 扫描、组织切片）导入为仿真的初始条件（如声速分布或密度分布）。

**Import**: `from jwave.utils import load_image_to_numpy`
**签名**: `(filepath: str, padding: int = 0, image_size: Tuple[int, int] = None) -> np.ndarray`

**参数**:

- `filepath`: 图像文件路径
- `padding`: 图像边缘的填充量，默认 0
- `image_size`: 输出图像尺寸（不含填充），默认 None（保持原尺寸）

### 仿真结果输出与 matplotlib 产物

代码生成不仅要完成求解，还必须把结果交付给用户。`__JWAVE_SELFCHECK__`
行只是工作流解析用的机器输出，不能替代用户结果。

**数值结果**：根据需求用 `print` 输出实际结果，例如模态分析的
`(nx, ny)`、频率表，时域仿真的 `max(abs(pressure))`、时间步数和采样间隔。
不要只打印调试信息或自检 JSON。

**二维场图像**：执行器可能没有图形界面，使用非交互后端并保存文件：

```python
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

output_dir = Path("outputs")
output_dir.mkdir(parents=True, exist_ok=True)
plt.figure()
plt.imshow(field_2d)
plt.colorbar(label="Pressure")
plt.tight_layout()
image_path = output_dir / "pressure_field.png"
plt.savefig(image_path, dpi=150)
plt.close()
print(f"压力场图片: {image_path}")
```

必须调用 `savefig` 并打印保存路径；仅调用 `plt.show()` 在无头执行器中不会产生
可交付图片。时域结果通常选择最终时刻或峰值时刻的二维切片，四维数组应先明确
选择时间索引和最后一个通道，例如 `field[-1, ..., 0]`。

### get_smallest_prime_factors 函数 — 可视化

`get_smallest_prime_factors` 返回给定数的最小质因数集合。用于选择 FFT 友好的网格尺寸——当网格尺寸的最小质因数较小时，FFT 效率最高。

**Import**: `from jwave.utils import get_smallest_prime_factors`
**签名**: `(n: int) -> Set[int]`

### numbers_with_smallest_primes 函数 — 可视化

`numbers_with_smallest_primes` 在给定范围内打印最小质因数不超过 `max_prime` 的所有数及其质因数。用于为仿真选择 FFT 最优的网格尺寸（推荐尺寸的最小质因数 ≤ 7）。

**Import**: `from jwave.utils import numbers_with_smallest_primes`
**签名**: `(min_range: int, max_range: int, max_prime: int = 7) -> None`

### kspace_op 函数 — 工具

`kspace_op` 返回 k-space 算子的数值数组。k-space 算子 $sinc(c_{ref} \cdot k \cdot \Delta t / 2)$ 是 PSTD 方法的核心，用于校正有限差分在粗网格上的色散误差，使时域仿真在 Nyquist 极限下依然精确。

**Import**: `from jwave.acoustics.spectral import kspace_op`
**签名**: `(domain: Domain, c_ref: float, dt: float) -> ndarray`

**参数**:

- `domain`: 计算域对象
- `c_ref`: 参考声速（通常取介质最大声速）
- `dt`: 时间步长

**物理意义**: k-space 算子补偿了有限时间步长导致的相位误差。当 $c_{ref} k \Delta t / 2$ 较小时，sinc 函数接近 1；在 Nyquist 频率附近，sinc 校正变得显著。

### db2neper 函数 — 工具

`db2neper` 将声学吸收系数的单位从 dB 转换为奈培（Nepers）。声学中常用 dB/(MHz^y·cm) 表示衰减，但 PDE 方程中衰减项以奈培为单位，此函数完成两种单位制之间的换算。

**Import**: `from jwave import db2neper`
**公共 API**: ✓
**签名**: `(alpha: jnp.ndarray, y: jnp.ndarray)`

**参数**:

- `alpha`: 以 dB 为单位的吸收系数
- `y`: 吸收系数的频率幂次指数

**返回值**: jnp.ndarray: 以奈培为单位的吸收系数。

**换算公式**: $\alpha_{Np} = \alpha_{dB} / (20 \log_{10} e) \approx \alpha_{dB} / 8.686$

### circ_mask 函数 — 工具

`circ_mask` 生成二维圆形二值掩码。在指定形状的网格上创建一个圆形区域（内部为 1，外部为 0）。用于定义圆形换能器、圆形散射体的初始几何形状。

**Import**: `from jwave.geometry import circ_mask`
**签名**: `(N: Tuple[int, int], radius: float, centre) -> np.ndarray`

**参数**:

- `N`: 网格形状 (x_size, y_size)
- `radius`: 圆的半径（网格单位）
- `centre`: 圆心坐标 (x, y)

### sphere_mask 函数 — 工具

`sphere_mask` 生成三维球形二值掩码。用于 3D 仿真中定义球形换能器、散射体或初始压力分布区域。

**Import**: `from jwave.geometry import sphere_mask`
**签名**: `(N: Tuple[int, int, int], radius: float, centre) -> np.ndarray`

**参数**:

- `N`: 网格形状 (x_size, y_size, z_size)
- `radius`: 球的半径（网格单位）
- `centre`: 球心坐标 (x, y, z)

### points_on_circle 函数 — 工具

`points_on_circle` 在圆周上生成均匀分布的点。用于定义环形传感器阵列的阵元位置，或圆形换能器的离散化采样点。

**Import**: `from jwave.geometry import points_on_circle`
**签名**: `(n: int, radius: float, centre: Tuple[float, float], cast_int: bool = True, angle: float = 0.0, max_angle: float = 2 * np.pi) -> Tuple[List[float], List[float]]`

**参数**:

- `n`: 点的数量
- `radius`: 圆的半径
- `centre`: 圆心坐标 (x, y)
- `cast_int`: 是否取整（网格点坐标需要整数），默认 True
- `angle`: 起始角度（弧度），默认 0
- `max_angle`: 终止角度（弧度），默认 $2\pi$（完整圆）

### fibonacci_sphere 函数 — 工具

`fibonacci_sphere` 使用 Fibonacci 球面分布算法在球面上生成均匀分布的点。相比经纬度等距采样，Fibonacci 分布更均匀，无极点聚集效应。用于 3D 超声阵列的阵元排布设计。

**Import**: `from jwave.geometry import fibonacci_sphere`
**签名**: `(n: int, radius: float, centre, cast_int: bool = True) -> Tuple[np.ndarray, np.ndarray, np.ndarray]`

**参数**:

- `n`: 点的数量
- `radius`: 球的半径
- `centre`: 球心坐标 (x, y, z)
- `cast_int`: 是否取整，默认 True

### bli_function 函数 — 工具

`bli_function` 计算带限插值（Band-Limited Interpolation）函数值。这是 `BLISensors` 的底层实现，用于在离网位置精确重建带限信号，避免直接线性插值导致的混叠误差。

**Import**: `from jwave.geometry import bli_function`
**签名**: `(x0: jnp.ndarray, x: jnp.ndarray, n: int, include_imag: bool = False) -> jnp.ndarray`

**参数**:

- `x0`: 传感器沿轴的位置
- `x`: 网格位置
- `n`: 网格尺寸
- `include_imag`: 是否包含虚部分量

### get_line_transducer 函数 — 工具

`get_line_transducer` 构造二维线段换能器。返回一个 `DistributedTransducer` 对象，其空间掩码为指定位置和角度的线段，用于模拟线阵换能器的声场发射。

**Import**: `from jwave.geometry import get_line_transducer`
**签名**: `(domain, position, width, angle = 0) -> DistributedTransducer`

**参数**:

- `domain`: 计算域
- `position`: 换能器中心位置
- `width`: 换能器长度（网格单位）
- `angle`: 换能器倾斜角度（弧度），默认 0

### three_circles 函数 — 工具

`three_circles` 生成三圆数字体模（Phantom）。返回一个包含三个不同声学参数圆形区域的二维数组，用于测试和验证成像/仿真算法的标准测试图案。

**Import**: `from jwave.phantoms import three_circles`
**签名**: `(N: tuple) -> jnp.ndarray`

**参数**:

- `N`: 体模尺寸，必须为长度 2 的元组

### set_logging_level 函数 — 工具

`set_logging_level` 设置 jwave 的日志输出级别，同时作用于 logger 及其所有 handler。用于在调试时开启详细日志（DEBUG），或在生产运行时减少输出（WARNING/ERROR）。

**Import**: `from jwave.logger import set_logging_level`
**签名**: `(level: int) -> None`

**参数**:

- `level`: 日志级别，取值为 `logging.DEBUG`、`logging.INFO`、`logging.WARNING`、`logging.ERROR` 或 `logging.CRITICAL`

### save_video 函数 — 工具

`save_video` 将时间序列的场数据导出为视频文件。用于可视化波传播的动态过程，支持自定义帧率、色图范围和编码格式。

**Import**: `from jwave.extras.export import save_video`

### 典型工作流 — 时域仿真全流程

完整的时域 PSTD 仿真流程：定义计算域和介质 → 设置时间轴 → 定义激励源 → 设置传感器 → 运行仿真 → 可视化结果。

```python
import jwave as jw
from jaxdf import FourierSeries
import jax.numpy as jnp

# 1. 定义计算域和介质
domain = jw.Domain((128, 128), (0.1e-3, 0.1e-3))  # 128x128, 0.1mm 网格
medium = jw.Medium(domain, sound_speed=1500.0, density=1000.0, pml_size=20)

# 2. 设置时间轴 (CFL=0.3, 仿真 40 μs)
time_axis = jw.TimeAxis.from_medium(medium, cfl=0.3, t_end=40e-6)

# 3. 定义激励源 (5 MHz, 3 周期 tone burst) — 注意整数坐标和位置参数
t = time_axis.to_array()
dt = time_axis.dt
signal_1d = jw.signal_processing.tone_burst(1/dt, 5e6, 3)
signals = jnp.expand_dims(signal_1d, 0)                    # 必须是 2D array
positions = (jnp.array([64]), jnp.array([64]))            # 必须是整数坐标
sources = jw.Sources(positions, signals, dt, domain)          # 位置参数，不能关键字

# 4. 设置传感器
sensors = jw.Sensors(positions=[(32, 64), (96, 64)])

# 5. 运行仿真
result = jw.simulate_wave_propagation(medium, time_axis, sources=sources, sensors=sensors)

# 6. 可视化最终压力场
jw.utils.show_field(result[-1], 'Final pressure field')
```

### 典型工作流 — 频域仿真全流程

完整的频域 Helmholtz 求解流程：定义介质 → 设置时谐源 → 选择求解器 → 求解 → 可视化复值场。

```python
import jwave as jw
from jaxdf import FourierSeries
import jax.numpy as jnp

# 1. 定义介质
domain = jw.Domain((128, 128), (0.1e-3, 0.1e-3))
medium = jw.Medium(domain, sound_speed=1500.0, density=1000.0, pml_size=20)

# 2. 定义时谐源 (1 MHz) — helmholtz_solver 需要 OnGrid 源
omega = 2 * jnp.pi * 1e6
src_array = jnp.zeros(domain.N, dtype=jnp.complex64).at[64, 64].set(1.0)
source = FourierSeries(src_array, domain)

# 3. 求解 Helmholtz 方程 (GMRES 方法)
field = jw.helmholtz_solver(medium, omega, source, method='gmres')

# 4. 可视化复值场 (实部 + 模值)
jw.utils.display_complex_field(field)
```

### 典型工作流 — 传感器信号采集与处理

在仿真完成后对传感器记录的时域信号进行处理：提取解析信号 → 包络检波 → 降采样。

```python
import jwave as jw
import jax.numpy as jnp

# ... (仿真代码，sensors 已在 simulate_wave_propagation 中配置) ...

# 获取传感器记录的时域信号
recorded_signals = result  # simulate_wave_propagation 返回传感器信号

# 解析信号提取 (Hilbert 变换)
analytic = jw.signal_processing.analytic_signal(recorded_signals, axis=-1)
envelope = jnp.abs(analytic)  # 提取包络

# 降采样以便后续分析或存储
downsampled = jw.signal_processing.fourier_downsample(recorded_signals, subsample=4)
```

### ⚠️ LLM 常编造的禁止 API（黑名单）

以下是 LLM 经常编造但**不存在**的 jwave API，代码生成时必须避免：

| 错误写法 | 正确写法 | 说明 |
|---------|---------|------|
| `p0.shape` | `p0.params.shape` | shape 在 .params 子对象上 |
| `p0.data` | `p0.params` | data 属性不存在 |
| `time_axis.t` | `time_axis.to_array()` | .t 属性不存在 |
| `time_axis.time` | `time_axis.to_array()` | .time 属性不存在 |
| `FourierSeries.from_array(data, domain)` | `FourierSeries(data, domain)` | 直接构造，无 from_array |
| `Sources(domain=..., source=...)` | `Sources(positions, signals, dt, domain)` | 位置参数，不能关键字 |
| `Sources.sources(source, domain)` | `Sources.on_grid(source_pressure)` | 用 on_grid 静态方法 |
| `signals=[signal]` (list) | `signals = jnp.expand_dims(signal_1d, 0)` | 必须是 2D jnp array |
| `positions=[(64,64)]` (list of tuples) | `positions = (jnp.array([64]), jnp.array([64]))` | 必须是 tuple of 1D int arrays |
| `positions = (jnp.array([64.0]), ...)` (float) | `positions = (jnp.array([64]), ...)` (int) | **浮点数导致压力场全零！** |

### 快速参考 — 公共 API 列表

jwave 所有公共 API 的快速索引，按类型（类/函数）列出，含标准 import 路径。

**类（7个）**:

| 名称 | Import |
|------|--------|
| `BLISensors` | `from jwave import BLISensors` |
| `DistributedTransducer` | `from jwave import DistributedTransducer` |
| `Medium` | `from jwave import Medium` |
| `Sensors` | `from jwave import Sensors` |
| `Sources` | `from jwave import Sources` |
| `TimeAxis` | `from jwave import TimeAxis` |
| `TimeHarmonicSource` | `from jwave import TimeHarmonicSource` |
| `TimeWavePropagationSettings` | `from jwave import TimeWavePropagationSettings` |

**函数（19个）**:

| 名称 | Import |
|------|--------|
| `angular_spectrum` | `from jwave import angular_spectrum` |
| `born_iteration` | `from jwave import born_iteration` |
| `born_series` | `from jwave import born_series` |
| `db2neper` | `from jwave import db2neper` |
| `helmholtz` | `from jwave import helmholtz` |
| `helmholtz_solver` | `from jwave import helmholtz_solver` |
| `homogeneous_helmholtz_green` | `from jwave import homogeneous_helmholtz_green` |
| `laplacian_with_pml` | `from jwave import laplacian_with_pml` |
| `mass_conservation_rhs` | `from jwave import mass_conservation_rhs` |
| `momentum_conservation_rhs` | `from jwave import momentum_conservation_rhs` |
| `pressure_from_density` | `from jwave import pressure_from_density` |
| `rayleigh_integral` | `from jwave import rayleigh_integral` |
| `scale_source_helmholtz` | `from jwave import scale_source_helmholtz` |
| `scattering_potential` | `from jwave import scattering_potential` |
| `simulate_wave_propagation` | `from jwave import simulate_wave_propagation` |
| `wavevector` | `from jwave import wavevector` |

### 快速参考 — 按模块索引

按 jwave 源码模块组织的完整函数索引，方便按功能查找。

- **`geometry`** — `BLISensors`, `DistributedTransducer`, `Medium`, `Sensors`, `Sources`, `TimeAxis`, `TimeHarmonicSource`, `bli_function`, `circ_mask`, `fibonacci_sphere`, `get_line_transducer`, `points_on_circle`, `sphere_mask`, `unit_fibonacci_sphere`
- **`acoustics.time_varying`** — `TimeWavePropagationSettings`, `mass_conservation_rhs`, `momentum_conservation_rhs`, `pressure_from_density`, `simulate_wave_propagation`
- **`acoustics.time_harmonic`** — `angular_spectrum`, `born_iteration`, `born_series`, `helmholtz_solver`, `homogeneous_helmholtz_green`, `rayleigh_integral`, `scattering_potential`
- **`acoustics.operators`** — `helmholtz`, `laplacian_with_pml`, `scale_source_helmholtz`, `wavevector`
- **`acoustics.pml`** — `complex_pml`, `complex_pml_on_grid`, `td_pml_on_grid`
- **`acoustics.spectral`** — `kspace_op`
- **`acoustics.conversion`** — `db2neper`
- **`signal_processing`** — `analytic_signal`, `apply_ramp`, `blackman`, `fourier_downsample`, `fourier_upsample`, `gaussian_window`, `smooth`, `smoothing_filter`, `tone_burst`
- **`utils`** — `display_complex_field`, `plot_comparison`, `show_field`, `load_image_to_numpy`
- **`extras.export`** — `save_video`
- **`phantoms`** — `three_circles`
- **`logger`** — `set_logging_level`
