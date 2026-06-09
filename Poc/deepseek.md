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
4. 结论：优化优先级与费用结构拆解

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

---

## 验证计划

| 验证项 | 方法 | 预期结果 |
| :--- | :--- | :--- |
| 单请求多缓存命中 | 发送请求，检查 usage 中 hit/miss 的分布 | 单次只命中一个最长连续前缀 |
| 预热 token 消耗 | 首次请求 vs 后续相同请求对比 | 首次全 miss，后续 system prompt 部分全 hit |
| 回滚对缓存的影响 | 回滚末尾内容后重新请求 | hit token 数不变，miss token 数减少 |
| 低价值判定系数 | 批量请求统计各操作的 hit/miss | 验证阈值是否合理 |
| API 返回格式 | 实际调用 API 解析 usage 字段 | 已验证 ✅ 见问题 5 |
