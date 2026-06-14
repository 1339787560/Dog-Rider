# Dog-Rider

AI Agent Skill 管理框架。通过标准化 Skill 文件，为 Agent 提供可复用的指令集。

## 目录结构

```
Dog-Rider/
├── src/
│   ├── SYS/           # 系统提示词（永久冻结区内容）
│   │   ├── SystemPrompt.md          # Agent 基础行为
│   │   ├── Claude-Core-Toolkit.md  # 核心工具集规范
│   │   └── Dog-Rider-Protocol.md   # Dog-Rider 框架协议
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

## 核心 Skill 清单

| Skill | 触发条件 | 用途 |
| :--- | :--- | :--- |
| Skill-Writer | 创建新 Skill | Skill 写作规范指导 |
| Doc-Writer | 编写文档 | 结构化技术文档 |
| Code-Planner | 代码实现/开发需求 | 编程任务敲定：需求+方案+BDD+测试 |

## 元规范

- 创建新 Skill 时必须遵循 `Skills/Skill-Writer.md` 规范
- 撰写文档前必须先 `Read` `Skills/Doc-Writer.md` 加载文档写作规范
