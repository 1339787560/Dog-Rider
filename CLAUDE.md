# Dog-Rider

AI Agent Skill 管理框架。通过标准化 Skill 文件，为 Agent 提供可复用的指令集。

## 启动协议

1. **加载系统提示词** — 按顺序拼接：
   - `src/SYS/SystemPrompt.md` — Agent 基础行为规范
   - `src/SYS/Claude-Core-Toolkit.md` — Claude Code 核心工具集使用指南
   - `CLAUDE.md` — Dog-Rider 框架协议
2. `Glob Skills/*.md` 扫描所有 Skill 文件
3. 对每个文件，`Read` 开头 YAML 块（到 `` ``` `` 结束），提取 `Skill_Name` / `Purpose` / `Execution_Trigger`
4. 建立路由表，后续用户请求匹配 `Execution_Trigger` 时加载对应 Skill 全文

## 目录结构

```
Dog-Rider/
├── src/
│   ├── SYS/           # 系统提示词（永久冻结区内容
│   │   ├── SystemPrompt.md          # Agent 基础行为
│   │   └── Claude-Core-Toolkit.md  # 核心工具集规范
│   ├── base/          # 基础 Agent 层
│   ├── agent.py       # AgentLoop 主循环（三区上下文 + TDD 支持）
│   ├── config.py      # 配置管理（含并行模式开关）
│   └── context.py     # ContextManager 快照/合并
├── Skills/            # 可插拔技能（按需加载）
│   ├── Skill-Writer.md             # Skill 写作规范
│   ├── Doc-Writer.md               # Doc 写作规范
│   └── Code-Planner.md             # 编程任务敲定：需求+实现方案+BDD+测试
├── tests/             # 测试套件（并行测试支持）
├── poc/               # 概念验证代码
└── baseConfig.yaml    # 全局配置（线程数、温度、并行模式等）
```

## 元规范

Skill 写作规范见 `Skills/Skill-Writer.md`。创建新 Skill 时必须遵循此规范。
Doc 写作规范见 `Skills/Doc-Writer.md`。创建新 Doc 时必须遵循此规范。

## 核心装备

启动后默认装备 `src/SYS/Claude-Core-Toolkit.md` — Claude Code 官方最常用工具集（Top 6），覆盖 93% 日常操作场景。

## 核心能力

### 三区上下文模型

- **永久冻结区**：系统提示词
- **暂时冻结区**：已确认内容
- **自然增长区**：当前任务内容

任务结束后可选择性合并或直接丢弃（`merge_on_keep` 控制）。

### 并行模式

`baseConfig.yaml` 配置：
- `discard.merge_mode: "parallel"` — 多线程独立 snapshot，不污染共享上下文
- `discard.isFrozenForParallel: true` — 并行时强制 `temperature=0` 保证确定性
- `test.thread_count` — 并发测试线程数

### TDD 支持

`Code-Planner` Skill 提供完整编程任务流程：
1. 需求拆解（含待确认问题）
2. 实现方案（TodoWrite 格式 + 依赖关系）
3. BDD 场景定义（Given-When-Then，用户视角）
4. 测试方案（单元 + 集成 + 运行方式）

## 核心 Skill 清单

| Skill | 触发条件 | 用途 |
| :--- | :--- | :--- |
| Skill-Writer | 创建新 Skill | Skill 写作规范指导 |
| Doc-Writer | 编写文档 | 结构化技术文档 |
| Code-Planner | 代码实现/开发需求 | 编程任务敲定：需求+方案+BDD+测试 |