# Dog-Rider

AI Agent Skill 管理框架。通过标准化 Skill 文件，为 Agent 提供可复用的指令集。

## 启动协议

1. `Glob Skills/*.md` 扫描所有 Skill 文件
2. 对每个文件，`Read` 开头 YAML 块（到 `` ``` `` 结束），提取 `Skill_Name` / `Purpose` / `Execution_Trigger`
3. 建立路由表，后续用户请求匹配 `Execution_Trigger` 时加载对应 Skill 全文

## 元规范

Skill 写作规范见 `Skills/Skill-Writer.md`。创建新 Skill 时必须遵循此规范。
