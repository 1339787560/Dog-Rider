# Dog-Rider - 任务级丢弃策略 Agent

基于 DeepSeek API 前缀缓存机制，实现任务级别的上下文丢弃策略。

## 架构

```
src/
├── config.py          # 配置: 阈值、权重、模型参数
├── tokenizer.py       # DeepSeekTokenizer + TemplateEstimator
├── context.py         # ContextManager (三区模型)
├── discard.py         # ValueJudge + TaskValueJudge + SubRequest
├── tools.py           # bash + read_file + write_file 工具
├── agent.py           # AgentLoop + per-request tracking + discard 执行
├── scenarios.py       # 4 个预设测试场景
└── main.py            # CLI 入口
```

## 核心概念

### 三区模型

```
messages[0]                            = 永久冻结区 (system prompt)
messages[1 : natural_zone_start]       = 暂时冻结区 (已确认内容)
messages[natural_zone_start :]         = 自然增长区 (当前任务)
```

### 任务级价值判定 (5 维度)

| 维度 | 权重 | 说明 |
| :--- | :--- | :--- |
| 缓存贡献率 | 0.30 | hit/(hit+miss), > 50% → 满分 |
| 上下文占比 | 0.20 | task/context, > 30% → 0 分 (反向) |
| 输出密度 | 0.20 | output/task, > 30% → 满分 |
| 请求链深度 | 0.15 | request_count, ≥ 3 → 满分 |
| 角色分布 | 0.15 | 是否有 analyze/output/write |

### 判定阈值

| 区间 | 判定 | 行为 |
| :--- | :--- | :--- |
| score ≥ 0.65 | 高价值 | 整任务保留 |
| 0.40 ≤ score < 0.65 | 可疑 | 保留但标记 |
| score < 0.40 | 低价值 | 丢弃，提取高价值内容 + 生成摘要 |

## 使用

### 交互模式

```bash
python -m src.main
```

### 场景模式

```bash
python -m src.main --scenario explore   # 探索任务 (应丢弃)
python -m src.main --scenario analyze   # 深度分析 (应保留)
python -m src.main --scenario write     # 写入任务 (应保留)
python -m src.main --scenario mixed     # 混合任务 (可疑)
python -m src.main --all-scenarios      # 运行所有场景
```

### 配置

```bash
# 禁用自动丢弃 (仅打印判定结果)
python -m src.main --no-auto-discard

# 详细输出
python -m src.main --verbose

# 切换模型
python -m src.main --model deepseek-v4-pro
```

## 预设场景

| 场景 | 说明 | 预期 |
| :--- | :--- | :--- |
| explore | 大量 grep/read，无输出/写入 | ✗ DISCARD |
| analyze | 多轮思考分析 | ✓ KEEP |
| write | 代码生成与修改 | ✓ KEEP |
| mixed | 先探索再分析 | 可疑 (观察) |

## 验证

```bash
# 离线验证判定逻辑 (无需 API)
python -c "
from src.config import load_env_config
from src.discard import SubRequest, TaskValueJudge
config = load_env_config()
judge = TaskValueJudge(config)

# 测试 1: 纯探索 -> 应丢弃
r1 = [SubRequest('exploration', 150, 0, 150, 10, 'ls')] * 3
v1 = judge.assess(r1, context_total=800, task_description='explore')
print('Explore: is_low_value=', v1.is_low_value, '(expected True)')

# 测试 2: 深度分析 -> 应保留
r2 = [SubRequest('output', 100, 80, 20, 100, 'final reply')]
v2 = judge.assess(r2, context_total=800, task_description='analyze')
print('Analyze: is_low_value=', v2.is_low_value, '(expected False)')
"
```

## 依赖

```bash
pip install tokenizers
```

- tokenizer.json: `Poc/.cache/tokenizer.json` (从 HuggingFace 下载)
- DEEPSEEK_API_KEY: 环境变量或 `.env` 文件
