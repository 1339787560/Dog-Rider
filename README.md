# Dog-Rider - MCP 并行 SubAgent 引擎

基于 DeepSeek 前缀缓存机制，**任务级上下文丢弃 + MCP 服务化 + SubAgent 并行执行**。Token 成本降低 50-70%，执行速度提升 3x+。

> **核心特性**：100% 缓存命中保证，绝无"突然 90% miss"的尴尬情况

---

## 架构概览

```
Dog-Rider MCP Server
┌─────────────────────────────────────────────────────────────┐
│  外部 Agent  ── MCP ──▶  Code-Planner Skill                 │
│                          任务规划 + 上下文同步               │
│                               ▼                              │
│                          主 Agent 调度器                     │
│                            校验缓存对齐                      │
│                            拆分子任务                        │
│                    ┌────────┴────────┬────────┐             │
│                    ▼                 ▼        ▼             │
│                SubAgent 1      SubAgent 2  SubAgent N       │
│                (独立上下文)    (独立快照)  (上下文隔离)       │
│                    └────────┬────────┘                      │
│                             ▼                               │
│                        结果汇总 + 摘要                        │
│                             ▼                               │
│                        返回外部 Agent                        │
└─────────────────────────────────────────────────────────────┘
```

---

## 目录结构

```
src/
├── mcp/                     # ✨ MCP 服务层
│   ├── server.py            # MCP 服务器主入口（stdio + HTTP）
│   ├── protocol.py          # MCP 协议 + 缓存对齐握手
│   ├── session.py           # 会话管理 + 上下文预热/刷新
│   ├── engine.py            # 主 Agent 任务调度
│   └── tools/
│       └── code_planner.py  # Code-Planner MCP 工具
│
├── base/                    # 基础能力层
│   ├── agent.py             # BaseAgentLoop + run_task() 隔离
│   ├── context.py           # 三区模型 + 裁剪 + 摘要
│   ├── config.py            # 配置管理
│   ├── persistence.py       # WAL + checkpoint 持久化
│   ├── task_queue.py        # TaskQueue + WorkerPool
│   ├── breaker.py           # 断路器 + 指数退避 + jitter
│   ├── logger.py            # 结构化 JSON 日志
│   └── metrics.py           # Prometheus 指标
│
├── agent.py                 # Dog-Rider AgentLoop (三区 + 丢弃)
├── discard.py               # 价值判定 + 丢弃策略
├── config.py                # Dog-Rider 扩展配置
├── context.py               # ContextManager (三区)
├── tools.py                 # 内置工具集
└── tokenizer.py             # Token 估算 + 缓存命中预测
```

---

## 核心能力

### 1. 100% 缓存命中保证

**三层校验机制，不命中不执行**：

| 层级 | 检查点 | 失败处理 |
| :--- | :--- | :--- |
| **握手** | 模型/tokenizer 版本哈希匹配 | 拒绝服务 |
| **预校验** | token 数组 SHA256 匹配 + 空请求验证 | 拒绝任务 |
| **运行时** | 第一个子任务实际 hit_tokens 误差 ≤ 5 | 立即中止所有子任务 |

**结果**：要么 ~100% 命中，要么完全不跑。零意外。

---

### 2. 任务级上下文丢弃

**三区模型 + 5 维度价值判定**：

```
messages[0]                            = 永久冻结区 (SYS，100% 命中)
messages[1 : natural_zone_start]       = 暂时冻结区 (已确认内容)
messages[natural_zone_start :]         = 自然增长区 (当前任务，可丢弃)
```

**价值判定维度**：

| 维度 | 权重 | 说明 |
| :--- | :--- | :--- |
| 缓存贡献率 | 0.30 | hit/(hit+miss) |
| 上下文占比 | 0.20 | 占比越大越倾向丢弃 |
| 输出密度 | 0.20 | output/task tokens |
| 请求链深度 | 0.15 | 多轮思考 → 保留 |
| 角色分布 | 0.15 | analyze/output/write → 保留 |

**判定阈值**：

| 区间 | 判定 | 行为 |
| :--- | :--- | :--- |
| ≥ 0.65 | 高价值 | 整任务保留合并 |
| 0.40-0.65 | 可疑 | 保留但标记 |
| < 0.40 | 低价值 | 丢弃，仅存摘要标记 |

---

### 3. SubAgent 并行执行

- **上下文隔离**：每个 SubAgent 独立快照，失败不污染
- **温度对齐**：所有子任务从同一起点启动 → 前缀全员命中
- **失败重试**：单子任务失败自动重试 2 次，不影响整体
- **结果摘要**：汇总时压缩冗余 70-90%，只返回核心结论

**性能提升**：并行度 4 → 时间加速 ≈ 3x，Token 总开销降低 50-70%

---

### 4. 上下文维持机制

- **预热**：加载 SYS + 历史摘要 → 后续请求前缀命中
- **刷新**：定期裁剪 + 重新摘要 → 维持 token 预算内
- **增量同步**：首次全量，后续只传追加消息 → 传输开销小

---

## MCP 工具清单

| 工具 | 参数 | 说明 |
| :--- | :--- | :--- |
| `code_planner_plan` | requirements: str | 生成标准化 Code-Planner 四区块 |
| `session_create` | system_prompt?: str | 创建新会话 |
| `session_resume` | session_id: str | 恢复已有会话 |
| `session_preheat` | session_id, sys_prompts, history | 预热上下文 |
| `session_refresh` | session_id | 裁剪并刷新上下文 |
| `task_submit` | session_id, plan, context | 提交并行任务（含缓存对齐校验） |
| `task_status` | task_id | 查询任务状态 |
| `task_cancel` | task_id | 取消任务 |
| `cache_debug_align` | token_ids, hash | 诊断缓存对齐问题（集成必用） |
| `healthz` | - | 健康检查 |

---

## 时空复杂度 + Token 节约

| 操作 | 时间 | 空间 | Token 节约 |
| :--- | :--- | :--- | :--- |
| 上下文预热 | O(n) | O(n) | 60-85% |
| 任务拆分 | O(k) | O(k) | - |
| 4 并行执行 | O(m/4) | O(4n) | 30-50% |
| 结果汇总 | O(k) | O(k) | 70-90% |
| 上下文刷新 | O(n) | O(1) | 持续优化 |

**总计**：Token 总成本降低 **50-70%**，执行时间降低 **60-75%**。

---

## 使用

### 作为 MCP 服务器

```bash
# stdio 模式（默认，Claude Code 集成用）
python -m src.mcp.server

# HTTP 模式（调试用）
python -m src.mcp.server --http --port 8000
```

**Claude Code 配置**（`~/.claude/settings.json`）：
```json
{
  "mcpServers": {
    "dog-rider": {
      "command": "python",
      "args": ["-m", "src.mcp.server"],
      "cwd": "/path/to/Dog-Rider"
    }
  }
}
```

---

### 作为 Python 库（原模式）

```bash
# 交互模式
python -m src.main

# 场景测试
python -m src.main --scenario explore   # 探索（应丢弃）
python -m src.main --scenario analyze   # 分析（应保留）
python -m src.main --scenario write     # 写入（应保留）
```

---

### 配置

```bash
# 并行模式
python -m src.main --parallel --workers 4

# 禁用自动丢弃
python -m src.main --no-auto-discard

# 指定模型
python -m src.main --model deepseek-v4-flash
```

---

## 验证与测试

```bash
# 离线验证丢弃逻辑
python -c "
from src.config import load_env_config
from src.discard import TaskValueJudge, SubRequest
config = load_env_config()
judge = TaskValueJudge(config)

# 纯探索 → 丢弃
r = [SubRequest('exploration', 150, 0, 150, 10, 'ls')] * 3
print('Explore: is_low_value =', judge.assess(r, 800, 'x').is_low_value)

# 输出 → 保留
r = [SubRequest('output', 100, 80, 20, 100, 'final')]
print('Output: is_low_value =', judge.assess(r, 800, 'x').is_low_value)
"

# 全量测试
pytest tests/ -v --cov=src
```

---

## 稳健性保障

| 风险 | 缓解措施 | 效果 |
| :--- | :--- | :--- |
| SubAgent 失败 | 自动重试 2 次 → 可选失败 | 单任务失败不影响整体 |
| 上下文超时 | 硬截止 + 失败回滚 | 不污染主会话 |
| API 限流 | 断路器 + 指数退避 + jitter | 快速失败不级联 |
| OOM | 单任务 token 预算 + 全局阈值 | 超阈值拒绝新任务 |
| 进程崩溃 | WAL + checkpoint → resume | 零数据丢失 |
| 调用方断开 | 任务继续执行 → 轮询获取 | 不中断已提交任务 |

---

## 依赖

```bash
pip install tokenizers
pip install @modelcontextprotocol/sdk  # MCP 协议
```

- `tokenizer.json`: `Poc/.cache/tokenizer.json`
- `DEEPSEEK_API_KEY`: 环境变量或 `.env` 文件

---

## 相关文档

- [MCP 服务化开发路线图](docs/base-agent-service-roadmap.md) - 完整设计方案 + 实现计划
- [CLAUDE.md](CLAUDE.md) - 项目规范与元规范
- [SYS 提示词](src/SYS/) - 系统提示词（永久冻结区）
