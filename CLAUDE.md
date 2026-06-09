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
│   └── SYS/           # 系统提示词（永久冻结区内容
│       ├── SystemPrompt.md          # Agent 基础行为
│       └── Claude-Core-Toolkit.md  # 核心工具集规范
└── Skills/            # 可插拔技能（按需加载）
    ├── Skill-Writer.md             # Skill 写作规范
    └── Doc-Writer.md               # Doc 写作规范
```

## 元规范

Skill 写作规范见 `Skills/Skill-Writer.md`。创建新 Skill 时必须遵循此规范。
Doc 写作规范见 `Skills/Doc-Writer.md`。创建新 Doc 时必须遵循此规范。

## 核心装备

启动后默认装备 `src/SYS/Claude-Core-Toolkit.md` — Claude Code 官方最常用工具集（Top 6），覆盖 93% 日常操作场景。