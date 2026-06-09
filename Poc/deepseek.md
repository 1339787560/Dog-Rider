```yaml
Doc_Name: DeepSeek API POC
Purpose: DeepSeek API 缓存机制验证与 ContextManager 设计依据
Type: POC
```

## 概要

基于 DeepSeek API 缓存机制的 POC 验证，为 ContextManager→DeepseekContext 的设计提供依据。

1. 缓存机制：DeepSeek 前缀缓存的工作原理
2. POC 验证：五个问题的结论（含实测数据）
3. 设计依据：ContextManager 的三区模型与缓存对齐
4. 结论：优化优先级、费用结构拆解、并发策略
5. 应用场景：Parallel Fork 模式（用户主动开启，context fork 后并发执行）

---

## 缓存机制

### 工作原理

DeepSeek API 的上下文缓存（KV Cache）**默认开启**，无需额外配置。基于 Sliding Window Attention 机制，采用**完整前缀单元匹配**。

**核心规则：**
- 每条缓存前缀是一个独立的完整单元
- 后续请求必须**完整匹配**该单元才能命中
- 部分匹配（共享前缀 A 但后半不同）不会命中该单元
- 系统检测到公共前缀后，会将其作为独立缓存单元落盘

**示例：**
- 请求 1: `A + B`，请求 2: `A + B + C` → 请求 2 完整匹配 `A + B`，命中
- 请求 1: `A + B`，请求 2: `A + C` → 不匹配 `A + B`，但系统检测到公共前缀 `A` 并独立落盘，请求 3: `A + D` 即可命中 `A`

### 缓存落盘的三种触发机制

| 触发机制 | 说明 |
| :--- | :--- |
| 请求结束位置落盘 | 每次请求在"用户输入结束位置"和"模型输出结束位置"各生成一个缓存前缀单元 |
| 公共前缀检测落盘 | 系统检测到多次请求间存在公共前缀时，将其作为独立缓存单元写入硬盘 |
| 固定 token 间隔落盘 | 对长输入/长输出按 token 数量间隔截取缓存前缀单元，防止长前缀因未到达结束位置而无法缓存 |

### 缓存命中计算

API 返回的 `usage` 字段：

```json
{
  "usage": {
    "prompt_cache_hit_tokens": 800,
    "prompt_cache_miss_tokens": 200,
    "completion_tokens": 256,
    "total_tokens": 1256
  }
}
```

- `prompt_cache_hit_tokens`：输入中命中缓存的 token 数
- `prompt_cache_miss_tokens`：输入中未命中缓存的 token 数
- 两者之和 = 总输入 token 数

### 定价（DeepSeek-V4）

| 模型 | Cache Miss | Cache Hit | 折扣倍数 |
| :--- | :--- | :--- | :--- |
| deepseek-v4-flash | $0.14/1M | $0.0028/1M | **50x** |
| deepseek-v4-pro | $0.435/1M | $0.003625/1M | **~120x** |

### 缓存过期

- 属于"尽力而为"模式，不保证 100% 命中
- 缓存构建耗时为秒级
- 不再使用后自动清除，时间一般为**数小时到数天**

---

## POC 验证

### 问题 1：同一 key 下能否创建多份缓存

**结论：可以，但单次请求只命中一个最长连续前缀。**

DeepSeek 的缓存系统基于 Sliding Window Attention，采用 RadixTree（基数树）结构管理缓存单元。系统可以为同一前缀的不同后缀创建独立的缓存单元：

```
缓存单元 1: [system_prompt + user_msg_A]
缓存单元 2: [system_prompt + user_msg_B]
缓存单元 3: [system_prompt]  ← 公共前缀独立落盘
```

单次请求的命中行为：
- 从请求开头逐 token 匹配，找到**最长的连续前缀**命中
- 返回的 `prompt_cache_hit_tokens` 是一个连续区间的长度，不是多个离散区间的累加
- 但系统后台可以同时维护多个独立缓存单元

**缓存份数验证：** 系统可创建的缓存单元数量取决于 GPU 内存，无硬性上限。但单次请求只利用一个最长前缀匹配。因此"9 份缓存"在系统层面是可行的（9 个不同的前缀单元），但单次请求只会命中其中最匹配的 1 个。

**设计启示：** ContextManager 应确保永久冻结区（system prompt + 索引文档）的内容稳定不变，使其成为所有请求共享的最长前缀。不同任务的差异内容放在自然增长区（前缀之后），不影响缓存命中。

**实测验证（10 组不同前缀）：**

| 前缀 | prompt | hit | miss | 结果 |
| :--- | :--- | :--- | :--- | :--- |
| 1 (TypeScript/CP) | 219 | 128 | 91 | HIT |
| 2 (Python/ML) | 176 | 128 | 48 | HIT |
| 3 (DevOps) | 165 | 128 | 37 | HIT |
| 4 (Frontend) | 165 | 128 | 37 | HIT |
| 5 (Backend) | 162 | 128 | 34 | HIT |
| 6 (Security) | 166 | 128 | 38 | HIT |
| 7 (Mobile) | 156 | 128 | 28 | HIT |
| 8 (Database) | 155 | 128 | 27 | HIT |
| 9 (Game) | 160 | 128 | 32 | HIT |
| 10 (Cloud) | 150 | 128 | 22 | HIT |

- **10/10 前缀全部被缓存**
- **最小缓存块：128 token**（所有命中均为 128，低于此值的前缀不被缓存）
- **触发条件：第 2 次请求触发公共前缀检测**（第 1 次请求结束时落盘，第 2 次命中）

### 问题 2：预热逻辑与 3w token 未命中

**结论：首次请求无缓存，所有 token 均为 cache miss。3w token 来自 system prompt + CLAUDE.md + 上下文的累积。**

分析"介绍一下你自己"产生 3w cache miss 的原因：

1. **首次请求无缓存：** 缓存是请求结束后才落盘的，第一次请求的所有输入 token 都是 cache miss
2. **隐式前缀累积：** 在 Claude Code 环境中，"介绍一下你自己"的请求实际包含：
   - 系统 prompt（工具定义、行为规范）
   - CLAUDE.md 全文
   - RTK.md 全文
   - 之前对话的上下文
   - 用户消息本身
   - 这些加起来可能达到 2-3w token
3. **Sliding Window 的影响：** 如果请求跨越多个 attention window，每个 window 都需要独立计算

**最小化预热阶段 cache miss 的策略：**

| 策略 | 说明 | 预期效果 |
| :--- | :--- | :--- |
| 精简 system prompt | 精简 CLAUDE.md、RTK.md 等固定内容 | 减少首次请求的 token 基数 |
| 分级加载 | 首次请求只包含最小 system prompt，后续按需追加 | 首次 miss 降至最低 |
| 预热请求 | 服务启动时发一个空请求预热缓存 | 后续请求命中 system prompt 前缀 |
| 保持前缀稳定 | system prompt 内容不变化，确保后续请求命中 | 从第二次起 system prompt 部分全命中 |

**ContextManager 对齐方案：**
- 永久冻结区的 system prompt 仅在首次请求时产生 cache miss
- 后续所有请求共享同一前缀，永久冻结区全部命中
- 自然增长区的内容作为前缀之后的追加，不影响永久冻结区的缓存

### 问题 3：回滚操作对缓存命中率的影响

**结论：回滚末尾内容不影响前缀缓存命中，但回滚中间内容会导致后续部分全部 miss。**

缓存是前缀匹配的，因此：

| 回滚位置 | 对缓存的影响 | 说明 |
| :--- | :--- | :--- |
| 回滚末尾内容（自然增长区尾部） | **无影响** | 前缀不变，后续请求仍命中永久冻结区 + 暂时冻结区 |
| 回滚中间内容（如移除某轮对话） | **后续部分全部 miss** | 前缀在断裂点之后全部失效 |
| 回滚永久冻结区内容 | **全部 miss** | 前缀从头改变，所有缓存失效 |

**ContextManager 的回滚策略：**
- **仅允许末尾回滚：** 回滚操作只能移除自然增长区末尾的低价值操作
- **禁止中间删除：** 不允许跳过中间的对话轮次
- **回滚后前缀不变：** 永久冻结区 + 暂时冻结区保持不变，回滚只减少末尾的 cache miss 部分
- **净效果：** 回滚减少了后续请求的 `prompt_cache_miss_tokens`（因为末尾的低价值 token 被移除），同时不损害 `prompt_cache_hit_tokens`

### 问题 4：低价值操作的判定系数

**结论：结合 cache miss 贡献率和上下文占比双维度判定。**

**判定模型：**

```
isLowValue = (missContribution > MISS_THRESHOLD) AND (contextRatio > RATIO_THRESHOLD)
```

其中：
- `missContribution` = 该操作产生的 cache miss token 数 / 该操作的总 token 数
- `contextRatio` = 该操作的 token 数 / 当前上下文总 token 数

**建议阈值：**

| 参数 | 阈值 | 依据 |
| :--- | :--- | :--- |
| MISS_THRESHOLD | 0.9 | 该操作 90%+ 的 token 未命中缓存，说明它几乎不复用前缀 |
| RATIO_THRESHOLD | 0.15 | 该操作占上下文 15%+ 的空间，是显著的上下文消耗 |
| HIT_CEILING | 1000 | 该操作贡献的 cache hit 不超过 1000 token，价值极低 |

**判定逻辑（三条件 OR）：**

1. `missContribution >= 0.9 AND contextRatio >= 0.15` → 高 miss 率 + 高占比 = 低价值
2. `hitContribution <= 1000 AND contextRatio >= 0.15` → 低 hit 贡献 + 高占比 = 低价值
3. `missContribution >= 0.95` → 极高 miss 率（无论占比）= 可疑低价值

**系数计算示例：**

```
操作 A: 2000 token, cache_hit=100, cache_miss=1900
  missContribution = 1900/2000 = 0.95  ✗ 超过 0.9
  contextRatio = 2000/10000 = 0.2     ✗ 超过 0.15
  → 低价值，应回滚

操作 B: 500 token, cache_hit=400, cache_miss=100
  missContribution = 100/500 = 0.2    ✓ 低于 0.9
  → 高价值，保留

操作 C: 3000 token, cache_hit=50, cache_miss=2950
  missContribution = 2950/3000 = 0.98 ✗
  hitContribution = 50                ✗ 低于 1000
  → 低价值，应回滚
```

### 问题 5：API 返回格式与花销统计

**实测数据（deepseek-v4-flash，system prompt ~128 token，user msg ~85 token）：**

| 请求 | prompt_tokens | cached_tokens | cache_hit | cache_miss | completion_tokens | reasoning_tokens |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 第 1 次（首次） | 212 | 0 | 0 | 212 | 30 | 30 |
| 第 2 次（命中） | 214 | 128 | 128 | 86 | 30 | 30 |
| 第 3 次（命中） | 213 | 128 | 128 | 85 | 30 | 30 |

**返回格式中的 usage 字段结构：**

```json
{
  "usage": {
    "prompt_tokens": 214,                    // 总输入 token 数
    "completion_tokens": 30,                 // 总输出 token 数（含推理）
    "total_tokens": 244,                     // prompt_tokens + completion_tokens
    "prompt_tokens_details": {
      "cached_tokens": 128                   // 与 prompt_cache_hit_tokens 相同
    },
    "completion_tokens_details": {
      "reasoning_tokens": 30                 // 推理 token 数（含在 completion_tokens 中）
    },
    "prompt_cache_hit_tokens": 128,          // 输入中命中缓存的 token 数
    "prompt_cache_miss_tokens": 86           // 输入中未命中缓存的 token 数
  }
}
```

**字段关系：**
- `prompt_tokens = prompt_cache_hit_tokens + prompt_cache_miss_tokens`
- `prompt_cache_hit_tokens == prompt_tokens_details.cached_tokens`（同一值的两个位置）
- `completion_tokens >= completion_tokens_details.reasoning_tokens`（推理 token 包含在输出中）
- `total_tokens = prompt_tokens + completion_tokens`

**花销计算公式：**

```
cost = (cache_miss × MISS_PRICE + cache_hit × HIT_PRICE + completion × OUTPUT_PRICE) / 1_000_000
```

**deepseek-v4-flash 定价：**

| 类型 | 单价（/1M tokens） |
| :--- | :--- |
| cache miss（输入未命中） | $0.14 |
| cache hit（输入命中） | $0.0028 |
| output（输出） | $0.28 |

**deepseek-v4-pro 定价：**

| 类型 | 单价（/1M tokens） |
| :--- | :--- |
| cache miss（输入未命中） | $0.435 |
| cache hit（输入命中） | $0.003625 |
| output（输出） | $0.87 |

**花销计算示例（以 flash 为例）：**

```
请求 1（首次，全 miss）:
  cost = (212 × 0.14 + 0 × 0.0028 + 30 × 0.28) / 1_000_000
       = (29.68 + 0 + 8.4) / 1_000_000
       = $0.00003808

请求 2（128 hit, 86 miss）:
  cost = (86 × 0.14 + 128 × 0.0028 + 30 × 0.28) / 1_000_000
       = (12.04 + 0.3584 + 8.4) / 1_000_000
       = $0.00002080

节省率: (0.00003808 - 0.00002080) / 0.00003808 = 45.4%
```

**缓存命中率对花销的影响（flash，200 token 输入 + 30 token 输出）：**

| 命中率 | hit | miss | 输入费用 | 输出费用 | 总费用 | 相对全 miss |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 0% | 0 | 200 | $0.0280 | $0.0084 | $0.0364 | 100% |
| 50% | 100 | 100 | $0.0143 | $0.0084 | $0.0227 | 62.3% |
| 80% | 160 | 40 | $0.0060 | $0.0084 | $0.0144 | 39.6% |
| 95% | 190 | 10 | $0.0019 | $0.0084 | $0.0103 | 28.3% |
| 100% | 200 | 0 | $0.0006 | $0.0084 | $0.0090 | 24.6% |

> 注意：当输入 token 远大于输出 token 时，缓存节省效果显著。当输出 token 占主导时，缓存节省的绝对金额变小。

**统计接口设计：**

```typescript
interface UsageStats {
  prompt_tokens: number;          // 总输入
  cache_hit_tokens: number;       // 命中缓存
  cache_miss_tokens: number;      // 未命中缓存
  completion_tokens: number;      // 总输出
  reasoning_tokens: number;       // 推理 token
  cost_usd: number;               // 计算得出的费用
  cache_hit_rate: number;         // hit / prompt_tokens
}
```

---

## 设计依据：ContextManager 三区模型

### 与缓存机制的对齐

```
请求内容 = [永久冻结区] + [暂时冻结区] + [自然增长区]
              ↑ 缓存始终命中    ↑ 大概率命中      ↑ 大概率 miss
```

| 区域 | 缓存行为 | 压缩策略 |
| :--- | :--- | :--- |
| 永久冻结区 | 始终命中（前缀不变） | 不压缩 |
| 暂时冻结区 | 大概率命中（内容稳定） | 仅在超阈值且占比>80%时联合压缩 |
| 自然增长区 | 大概率 miss（频繁变化） | 接近 90% 时首先压缩，结果 append 入暂时冻结区 |

### 回滚操作的缓存安全

- 回滚仅作用于自然增长区末尾
- 永久冻结区 + 暂时冻结区的前缀不变 → 缓存命中不受影响
- 回滚减少了后续请求的 miss token 数 → 净收益

### 低价值操作的生命周期

```
工具调用执行 → 记录 cache hit/miss → 任务完成时判定
  ├── 高价值 → 保留在自然增长区
  └── 低价值 → 移除，不进入后续上下文
```

---

## 结论

### 优化优先级

基于 5 个 POC 问题的验证结果，DeepSeek API 花销优化的优先级排序：

| 优先级 | 策略 | 原理 | 预期收益 |
| :--- | :--- | :--- | :--- |
| **P0** | 减少输出 token | 输出单价是缓存命中输入的 100~240 倍 | 减少 75% 输出 → 总费用降 50%+ |
| **P1** | 提高输入缓存命中率 | 三区模型保持前缀稳定 | 命中率 0%→80% → 输入费用降 80% |
| **P2** | 回滚低价值操作 | 移除末尾高 miss 内容 | 减少后续请求的 miss token 数 |

**核心认知：** 输出 token 是最贵的资源。caveman 等 prompt 压缩技术减少输出 token 的收益，远大于提高输入缓存命中率的收益。两者组合才能最大化成本效率。

### 费用结构拆解（flash，典型请求）

```
典型请求: 2000 输入 token + 200 输出 token

全 miss:    (2000 × 0.14 + 200 × 0.28) / 1M = $0.000336
80% 命中:   (400 × 0.14 + 1600 × 0.0028 + 200 × 0.28) / 1M = $0.000114
80% 命中 + caveman(-75% 输出):
            (400 × 0.14 + 1600 × 0.0028 + 50 × 0.28) / 1M = $0.000074

总节省: (0.000336 - 0.000074) / 0.000336 = 78%
```

### ContextManager 三区模型的成本收益

| 区域 | 控制手段 | 影响的费用项 |
| :--- | :--- | :--- |
| 永久冻结区 | 保持不变 | 输入 cache hit（始终命中） |
| 暂时冻结区 | 延迟压缩 | 输入 cache hit（大概率命中） |
| 自然增长区 | 及时压缩 + 回滚 | 输入 cache miss → 减少 miss 量 |
| 输出层 | caveman / prompt 压缩 | 输出 token（最贵项） |

### 并发 subagent 缓存策略

**问题：** 高并发调用 subagent 时，如何保持缓存命中？

**结论：在永久冻结区和暂时冻结区充分就绪后，可安全进行并发工作。**

```
请求结构 = [shared prefix] + [task context] + [task instruction]
              ↑ 缓存命中区       ↑ 每个不同       ↑ 每个不同
              (~256 token)       (cache miss)     (cache miss)
```

**实践方案：预热-就绪-并发（Warm-Ready-Concurrent）**

本方案是"低价值丢弃"策略的正向应用：先将高价值内容沉淀到冻结区，再并发执行低价值的探索性工作。

```
Phase 1 — 就绪 (Sequential, 单线程)
  ├── 构建永久冻结区: system prompt + 项目文档 + 技能索引
  ├── 构建暂时冻结区: 任务相关的上下文（已读取的文件、已确认的设计）
  ├── 预热请求: 发 1 个请求验证缓存就绪
  └── 回滚低价值内容: 移除未贡献缓存命中的临时操作

Phase 2 — 并发 (Parallel, 多线程)
  ├── 每个 subagent 请求 = 冻结区(prefix) + task context + task instruction
  ├── 冻结区部分 → 全部命中缓存
  ├── task context 部分 → cache miss（不可避免，但占比小）
  └── 收集结果 → 判定高/低价值 → 高价值 append 入暂时冻结区

Phase 3 — 收敛 (Sequential, 单线程)
  ├── 合并所有 subagent 结果
  ├── 低价值操作回滚（不进入下一轮冻结区）
  ├── 高价值内容沉淀入暂时冻结区
  └── 为下一轮并发更新 shared prefix
```

**与低价值丢弃的关系：**

| 阶段 | 低价值丢弃的作用 |
| :--- | :--- |
| Phase 1 就绪 | 回滚探索性操作，确保冻结区只含高价值内容 |
| Phase 2 并发 | 每个 subagent 的 task context 是一次性的，天然低价值 |
| Phase 3 收敛 | 丢弃 subagent 的低价值输出，只保留高价值结论沉淀 |

**关键约束：**
- 冻结区必须 ≥128 token 才能触发缓存
- 预热请求必须在并发请求之前完成（缓存构建是秒级）
- 并发 subagent 的 shared prefix 必须完全一致（一字不差）
- task context 的差异不影响 shared prefix 的缓存命中

**实测数据（5 并发）：**

| 策略 | hit_rate | cost | 节省 |
| :--- | :--- | :--- | :--- |
| 无预热 | 0% | $0.000258 | — |
| 1 次预热 | 95.5% | $0.000082 | 68% |
| 分波 (Wave1 自然构建) | 94~97% | $0.000083 | 68% |

### 应用场景：Parallel Fork 模式

**场景描述：** 用户在对话中逐步构建上下文（阅读代码、确认设计），当判断上下文充足后，主动开启 parallel 模式。此后用户的每次任务可被拆分为多个并行 agent，每个 agent 从当前 context fork 一份后独立执行。

**交互流程：**

```
用户: 读一下 leveldefine_xzmp.ts
Agent: [读取文件，分析结构，回复摘要]     ← Sequential，构建上下文
用户: 再看看 cmmonthcard_xzmp.ts
Agent: [读取文件，分析结构，回复摘要]     ← Sequential，继续构建
用户: 对比这两个模块的数据存储差异
Agent: [分析对比，回复结论]               ← Sequential，上下文已丰富

用户: /parallel on                        ← 用户判断上下文充足，开启并行模式

用户: 同时做三件事：
  1. 为 leveldefine 写接口文档
  2. 为 cmmonthcard 写接口文档
  3. 对比两个模块的降级策略差异

Agent: [Fork context × 3, 并发执行]
  ├── Agent-1: context_fork + "写 leveldefine 接口文档"
  ├── Agent-2: context_fork + "写 cmmonthcard 接口文档"
  └── Agent-3: context_fork + "对比降级策略"
  [收集结果，合并回复]
```

**Context Fork 机制：**

```
主 context (用户对话累积)
  ├── 永久冻结区: system prompt + 项目文档       ← fork 时复制
  ├── 暂时冻结区: 已读取的文件 + 已确认的设计    ← fork 时复制
  └── 自然增长区: 对话历史 + 工具调用结果        ← fork 时复制

Fork-1 (Agent-1 的独立 context)
  ├── [继承] 永久冻结区 (不变)
  ├── [继承] 暂时冻结区 (不变)
  ├── [继承] 自然增长区 (作为只读参考)
  └── [新增] Agent-1 的任务上下文 + 工具调用结果

Fork-2, Fork-3 ... 同理
```

**缓存对齐：**

| 阶段 | 操作 | 缓存效果 |
| :--- | :--- | :--- |
| Sequential 积累 | 用户对话 + 工具调用 | 永久冻结区 + 暂时冻结区逐步构建，缓存命中率逐步提升 |
| /parallel on | 用户显式开启 | 此时 shared prefix 已充分就绪，缓存已热 |
| Fork 并发 | N 个 agent 从 context fork | 每个 fork 共享同一 prefix → 全部命中缓存 |
| 结果收敛 | 合并 N 个 agent 的输出 | 高价值内容 append 入暂时冻结区，低价值丢弃 |

**与低价值丢弃的关系：**

- Sequential 阶段：用户通过对话逐步筛选高价值上下文，低价值的中间探索被自然淘汰
- Parallel 阶段：每个 fork 的 task context 是一次性的（低价值），冻结区是高价值的
- 收敛阶段：agent 输出中的低价值内容（调试日志、中间推理）被丢弃，高价值结论沉淀入暂时冻结区

**设计约束：**

| 约束 | 说明 |
| :--- | :--- |
| 用户显式开启 | 不自动判断，避免误触发（上下文不足时并行效果差） |
| fork 是快照 | fork 后主 context 冻结，各 agent 独立演化，不互相干扰 |
| prefix 必须一致 | 所有 fork 共享同一 frozen zones，一字不差 |
| 收敛是 append-only | agent 结果只追加到暂时冻结区末尾，不修改已有内容 |
| /parallel off | 用户可随时关闭，回到 sequential 模式 |

**预期收益（基于实测）：**

```
Sequential 模式: 3 个任务串行执行
  总 cost = 3 × single_request_cost

Parallel Fork 模式: 3 个任务并行执行
  总 cost = warmup_cost + 3 × (prefix_hit_cost + task_miss_cost)
  节省 ≈ 68% (基于 5 并发实测)
```

---

## 分词器逆向

### Tokenizer 获取

DeepSeek V3/V4 使用 **Tiktoken-based BPE** 分词器，直接从 HuggingFace 获取：

```python
# 下载
# https://huggingface.co/deepseek-ai/DeepSeek-V3/resolve/main/tokenizer.json
# 文件大小: ~7.8MB, 缓存至 Poc/.cache/tokenizer.json

from tokenizers import Tokenizer
tok = Tokenizer.from_file("Poc/.cache/tokenizer.json")
```

| 属性 | 值 |
| :--- | :--- |
| Vocab 大小 | 128,815 |
| 分词算法 | BPE (Byte-Pair Encoding) |
| 基础词表 | Tiktoken (cl100k_base 兼容) |
| 缓存粒度 | 128 token |
| Chat template 开销 | base 4 token + 每条消息 2 token (n>2) |

### Chat Template 开销公式

本地 token 计数 vs API `prompt_tokens` 的关系：

```
API prompt_tokens = sum(local_content_tokens) + overhead

overhead = 4                       (n_messages ≤ 2)
overhead = 4 + 2 × (n_messages - 2)  (n_messages > 2)
```

**实测验证：**

| 消息结构 | local | api | overhead | 公式 |
| :--- | :--- | :--- | :--- | :--- |
| 1 user | 1 | 5 | 4 | 4 |
| sys + user | 2 | 6 | 4 | 4 |
| sys + user + asst + user | 4 | 12 | 8 | 4 + 2×2 |
| sys + ... + user (6 msgs) | 6 | 18 | 12 | 4 + 2×4 |
| 10 user | 10 | 23 | 13 | ~4 + 2×5 |

**关键结论：**
- 本地 token 计数精确匹配 API 返回的 `prompt_cache_hit_tokens`（不含 template 开销）
- Template 开销仅影响 `prompt_tokens` 总数，不影响缓存命中计算
- 三区模型的 token 分析基于本地计数即可，无需 API 往返

### 128 Token 缓存边界验证

| system prompt | 第 1 次 miss | 第 2 次 hit | 缓存？ |
| :--- | :--- | :--- | :--- |
| 68 token | 73 | 0 | ❌ |
| 90 token | 95 | 0 | ❌ |
| 128 token | 133 | **128** | ✅ |
| 132 token | 9 | **128** | ✅ |
| 173 token | 50 | **128** | ✅ |
| 258 token | 135 | **256** | ✅ |
| 256 token | 5 | **256** | ✅ |

**结论：**
- 缓存命中值总是 128 的整数倍
- `< 128 token` 的前缀不会被缓存
- 永久冻结区必须 ≥ 128 token 才能触发缓存
- 本地 `count(text)` = API `prompt_cache_hit_tokens`（对前缀部分）

### ContextAnalyzer 类

`Poc/tokenizer_poc.py` 提供三个核心类：

```python
from tokenizer_poc import DeepSeekTokenizer, ContextAnalyzer, ValueJudge, TemplateEstimator

# 1. 分词器
tok = DeepSeekTokenizer()
tok.count("hello world")          # → 2
tok.common_prefix_len(a, b)       # → 公共前缀 token 数
tok.cache_hit_tokens(200)         # → 128 (128 对齐)
tok.estimate_cache_hit(prefix, full_input)  # → (hit, miss)

# 2. 三区分析器
analyzer = ContextAnalyzer(tok)
analyzer.set_zone("permanent", system_prompt)
analyzer.set_zone("temporary", read_files_summary)
analyzer.set_zone("natural", current_task)
print(analyzer.report())          # → 完整分析报告

# 3. 低价值判定
judge = ValueJudge(tok)
is_low, reason = judge.judge(op_tokens=200, cache_hit=10, cache_miss=190, context_total=500)
# → (True, "极高 miss 率 (95.0%)")

# 4. Template 开销估算
api_estimate = TemplateEstimator.estimate(messages, content_tokens)
content_estimate = TemplateEstimator.content_tokens_from_api(api_prompt_tokens, n_messages)
```

### 用法

```bash
python Poc/tokenizer_poc.py --local     # 纯本地分析 (不需要 API key)
python Poc/tokenizer_poc.py --verify    # 验证本地 vs API 计数
python Poc/tokenizer_poc.py             # 全量分析
```

---

## 任务级价值判定

### 设计目标

从"单次操作判定"扩展到"任务级判定"——任务是由多个 API 请求组成的合集（agent loop），在任务自然结束（finish_reason ≠ tool_calls）后判定整条任务的价值，决定保留或丢弃。

### 任务生命周期

```
Task 开始 (用户发送消息)
  │
  ├── Request 1: chat 调用        → hit/miss 记录
  ├── Request 2: tool 调用        → hit/miss 记录
  ├── Request 3: chat (分析结果)  → hit/miss 记录
  ├── ...
  └── Request N: finish_reason ≠ tool_calls  → 任务自然结束
                                                    │
                                          TaskValueJudge.assess()
                                                    │
                                    ┌───────────────┴───────────────┐
                                    ↓                               ↓
                                KEEP                             DISCARD
                          整任务保留在                     提取高价值子请求
                          自然增长区                       + 生成任务摘要
                                                                ↓
                                                         压缩后 append
                                                         到共享 context
```

### 子请求角色分类

每个子请求按语义角色标记：

| 角色 | 标记 | 默认价值 | 示例 |
| :--- | :--- | :--- | :--- |
| exploration | 探索 | 低 | grep, find, ls, glob |
| read | 读取 | 中 | read file, cat |
| analyze | 分析 | 高 | chat 无 tool call |
| output | 输出 | 高 | 最终回复、结论 |
| write | 写入 | 高 | write/edit file |

### 5 维度评分模型

```
任务价值 score = Σ(维度得分 × 权重)

维度:
  1. 缓存贡献率  (W=0.30): hit/(hit+miss),  >50% → 满分
  2. 上下文占比  (W=0.20): task/context,     >30% → 0分 (反向)
  3. 输出密度    (W=0.20): output/task,      >30% → 满分
  4. 请求链深度  (W=0.15): request_count,    ≥3   → 满分
  5. 角色分布    (W=0.15): 是否有 analyze/output/write
```

### 判定阈值

| 区间 | 判定 | 行为 |
| :--- | :--- | :--- |
| score ≥ 0.65 | 高价值 | 整任务保留在自然增长区 |
| 0.35 ≤ score < 0.65 | 可疑 | 保留但标记，观察后续 |
| score < 0.35 | 低价值 | 丢弃，提取高价值子请求 + 生成摘要 |

### 高价值子请求提取

低价值任务中可能包含高价值信息。丢弃时按优先级提取：

```
提取优先级:
  1. ROLE_OUTPUT  → 任务结论，必须保留
  2. ROLE_ANALYZE → 分析过程，包含推理
  3. ROLE_WRITE   → 代码产出，有持久价值
  4. 高缓存命中   → 复用价值高

限制: 提取总量 ≤ MAX_EXTRACT_TOKENS (默认 500)
```

### 任务摘要

丢弃后生成压缩摘要替代原任务内容，append 到共享 context：

```
[DISCARDED TASK] 探索任务: grep 搜索 + 读文件
  requests=4 tokens=850 hit_rate=1.2%
  roles: analyze=1, exploration=1, read=2
```

### 实测演示

```bash
python Poc/tokenizer_poc.py --task
```

| 场景 | requests | tokens | hit_rate | score | 判定 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 探索 grep + 读文件 | 4 | 850 | 1.2% | 0.34 | ✗ DISCARD |
| 深度分析 + 写文档 | 4 | 520 | 48.1% | 0.77 | ✓ KEEP |
| 快速查询 | 1 | 30 | 66.7% | 0.83 | ✓ KEEP |
| 纯探索 (grep/ls) | 4 | 650 | 0.0% | 0.16 | ✗ DISCARD |
| 读 → 分析 → 写代码 | 4 | 360 | 58.3% | 0.80 | ✓ KEEP |

### 配置可调

`TaskValueJudge.Config` 类中所有参数可通过 yaml/json 覆盖：

```python
class Config:
    SCORE_THRESHOLD_LOW  = 0.35   # 低于此值 → 丢弃
    SCORE_THRESHOLD_HIGH = 0.65   # 高于此值 → 保留
    W_CACHE_CONTRIB  = 0.30       # 缓存贡献权重
    W_CONTEXT_RATIO  = 0.20       # 上下文占比权重
    W_OUTPUT_DENSITY = 0.20       # 输出密度权重
    W_CHAIN_DEPTH    = 0.15       # 请求链深度权重
    W_ROLE_MIX       = 0.15       # 角色分布权重
    MAX_EXTRACT_TOKENS = 500      # 提取上限
```

### 与三区模型的集成

```
自然增长区末尾 = 当前任务 (N 个请求)

TaskValueJudge.assess() → DISCARD
  ↓
1. 提取高价值子请求 → 保留在自然增长区 (压缩后)
2. 生成任务摘要 → 追加到自然增长区
3. 原始请求链 → 从上下文中移除
4. 永久冻结区 + 暂时冻结区 → 不变，缓存命中不受影响

TaskValueJudge.assess() → KEEP
  ↓
1. 整任务保留在自然增长区
2. 后续可被 ContextManager 压缩到暂时冻结区
```

---

## 验证记录

所有验证均通过 tiny-agent (`python Poc/tiny_agent.py`) 或直接调用 DeepSeek API 完成。

### 已验证项

| 验证项 | 结论 | 实测数据 |
| :--- | :--- | :--- |
| 缓存命中稳定性 | ✅ 同一 system prompt 连续 5 次请求，hit 稳定 256 token | 5 次均为 hit=256, miss=56, 82.1% |
| 多前缀缓存数量 | ✅ 10 组不同前缀全部被缓存，最小块 128 token | 10/10 HIT，每个 hit=128 |
| 并发缓存命中 | ✅ 1 次预热后 5 并发请求 hit_rate=95.5% | 无预热 0% → 预热后 95.5%，节省 68% |
| Warm-Ready-Concurrent | ✅ 就绪→预热→并发→收敛的四阶段模式 | 低价值丢弃的正向应用 |
| Parallel Fork 模式 | 设计方案，待实现 | 用户主动开启，context fork 后并发执行 |
| 首次请求全 miss | ✅ 首次无缓存，system prompt 全量计费 | 1890 miss, 0 hit, $0.000293 |
| 缓存后费用降低 | ✅ 第二次起 system prompt 命中缓存 | 1792 hit, 98 miss, $0.000047（降低 6.2x） |
| 费用计算公式 | ✅ `cost = (miss×0.14 + hit×0.0028 + out×0.28) / 1M` | 手动计算与脚本输出一致 |
| flash vs pro 对比 | ✅ 命中率相同时，pro 费用约为 flash 的 3x | flash $0.000026 vs pro $0.000080（第3次） |
| caveman 输出压缩 | ✅ 输出 token 减少 57.6%，总费用降低 54.5% | 正常 500 out/$0.000144 vs caveman 212 out/$0.000066 |
| 30k token 来源 | ✅ Claude Code 完整 system prompt ~1890 token，30k 来自会话累积 | system prompt 7384 chars ≈ 1890 token |

### 验证细节

**缓存命中稳定性（5 次连续请求）：**
```
第1次: prompt=312 hit=256 miss=56 out=141 cost=$0.000048
第2次: prompt=312 hit=256 miss=56 out=85  cost=$0.000032
第3次: prompt=312 hit=256 miss=56 out=40  cost=$0.000020
第4次: prompt=312 hit=256 miss=56 out=71  cost=$0.000028
第5次: prompt=312 hit=256 miss=56 out=68  cost=$0.000028
```

**caveman 输出压缩效果：**
```
正常模式: output=500 tokens, cost=$0.000144
caveman:  output=212 tokens, cost=$0.000066
输出节省: 288 tokens (57.6%)
费用节省: $0.000079 (54.5%)
```

**flash vs pro 费用对比（210 token system prompt）：**
```
flash 第1次: miss=210 hit=0   out=50 cost=$0.000043
flash 第3次: miss=82  hit=128 out=50 cost=$0.000026  (节省 40.5%)

pro   第1次: miss=210 hit=0   out=50 cost=$0.000135
pro   第3次: miss=82  hit=128 out=50 cost=$0.000080  (节省 40.9%)
```

**并发 subagent 缓存命中（5 并发，shared prefix ~256 token）：**

```
模式 1 无预热:
  5 个并发请求全部 MISS → hit_rate=0.0%, cost=$0.000258

模式 2 预热后并发:
  1 次预热 + 5 个并发请求 → hit_rate=95.5%, cost=$0.000082 (节省 68%)

模式 3 分波并发 (2 波):
  Wave 1 (3个): hit_rate=94.3%, cost=$0.000051
  Wave 2 (2个): hit_rate=97.2%, cost=$0.000032
  总 cost=$0.000083
```

| 模式 | 预热 | hit_rate | cost | 相对无预热 |
| :--- | :--- | :--- | :--- | :--- |
| 无预热并发 | 无 | 0.0% | $0.000258 | 100% |
| 预热后并发 | 1 次 | 95.5% | $0.000082 | 31.8% |
| 分波并发 | 0 次（Wave1 自然构建） | 94~97% | $0.000083 | 32.2% |
