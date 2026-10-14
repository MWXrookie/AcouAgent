# 验收测试指南（TESTING）

> 面向 Owner / 组员 / 验收人员：按层次验证当前系统成果。标注 💰 = 有模型费用，免费 = 不花钱。

## 入口速览

浏览器打开 **`http://192.168.30.200:8001`**（VM 内网 IP，Windows 本机可访问）：

| 页面 | 地址 | 用途 |
|---|---|---|
| 门户（问答） | `/` `/portal` | 自然语言 → Markdown 报告（含热力图+物理解读） |
| 多轮对话 | `/chat` | 连续对话、增量修改参数 |
| 执行看板 | `/dashboard` | 执行记录、代码展开、**结果对比**、图像预览 |
| 纠错缓存 | `/cache` | 经验条目、命中率 |

---

## A. 5 分钟快速体检（免费）

```bash
# 容器状态
docker ps --filter name=dify-mcp --filter name=jwave-executor
# 网关健康
curl http://192.168.30.200:8001/health      # 预期 "ok"
# 各页面
for p in / /portal /dashboard /chat /cache; do
  curl -s -o /dev/null -w "$p -> %{http_code}\n" "http://192.168.30.200:8001$p"
done   # 预期全部 200
```

---

## B. 核心功能验证（💰 每轮约 0.5-1 元）

### B-1 门户问答
在 `/` 输入：
> 仿真 200kHz 超声在水中传播，网格 96x96，dx 0.5mm，t_end 25us

**验收**：报告含「仿真结果报告 + 最大压力 + **压力场热力图（图片）+ 物理解读**」

### B-2 多轮对话（增量修改）
在 `/chat` 连续输入：
```
模拟一个2 MHz点声源…网格128x128…   → 出报告
把频率改成3 MHz                     → 只改频率，其他保留（提示"已合并"）
在距离中心2mm处放一个传感器记录压力波形
```
**验收**：每轮自动合并需求并重新仿真，参数正确保留/修改

### B-3 异常自愈（T-009 迭代循环）
输入**故意欠采样**参数：
> 仿真 10 MHz 超声在水中传播，网格 64x64，dx 0.5mm，t_end 15us

**验收**：报告尾部出现 **"🔁 已自动重试一次"**（审查发现异常 → 自动重试）

### B-4 环形初始压力（P2 修复）
> 环形初始压力场传播：圆环半径2mm，环宽0.5mm，峰值500 Pa，网格256x256，区域1cm，声速1500 m/s

**验收**：最大压力**非零**（此前全零）

### B-5 多会话隔离（P0）
两个不同浏览器打开 `/chat` 各跑一轮，确认互不干扰（各自需求独立）。

---

## C. 脚本化深度测试（💰）

```bash
cd <项目目录>
# 多轮对话稳定性（14 轮，约 5-10 元，15-30 分钟）
python3 scripts/tests/test_multiturn_stability.py
# 50 次全量回归（约 20-50 元，30-60 分钟）⚠️ 跑前确认预算
DIFY_API_KEY=<app-key> python3 scripts/tests/run_50_tests_new.py
# P0 初始压力 4 场景（约 2-4 元）
```
> 脚本网关地址用环境变量 `GW_BASE_URL` 覆盖（默认 localhost:8001，远程设 `http://192.168.30.200:8001`）。

---

## D. 物理正确性验证（免费，executor 内，约 1 分钟）

```bash
docker exec -e JAX_PLATFORMS=cpu -e XLA_PYTHON_CLIENT_PREALLOCATE=false -e HOME=/tmp \
    -i jwave-executor python - < executor/validation_baseline.py
```
**验收**：5/5 PASS（含 3D 球面波和频域衰减，误差 <1%）

---

## E. 工具级（免费，命令行）

```bash
TOKEN=<MCP_AUTH_TOKEN，见 VM .env>
curl -X POST http://192.168.30.200:8001/mcp -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
# 预期 6 个工具：run_jwave_code / run_jwave_code_with_retry / validate_simulation_params
#           / analyze_simulation_result / jwave_environment / list_installed_libraries
```

---

## F. 阶段二验收对照（门禁清单）

| 门禁项 | 验证方式 | 达标线 |
|---|---|---|
| VAL-1 验证基准集 | D 节脚本 | 5/5 PASS，误差 <1% |
| T-007 结果分析（热力图+verdict） | B-1 报告含热力图 | 每次仿真出图 |
| T-008 审查+解释 | B-1 含物理解读 | ≥3 句解读 |
| T-009 迭代循环 | B-3 异常自动重试 | 2 次尝试后终止 |
| 多轮对话 | B-2 / C 稳定性脚本 | 上下文正确率 ≥85% |
| P2 初始压力 | B-4 | 环形/椭圆非全零 |
| P0 会话隔离/进度/对比 | B-5 / 看板勾选对比 | 正常 |

---

## 验收顺序建议

**A（5 分钟）→ B-1/B-2（核心）→ D（物理，免费）→ B-3/B-4/B-5（新能力）→ C（深度，视预算）**

*创建：2026-08-18（阶段二收尾）。阶段三开始前可用本指南做回归基线。*
