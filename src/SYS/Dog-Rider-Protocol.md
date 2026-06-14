# Dog-Rider Agent Protocol

## 元规范

Skill 写作规范见 `Skills/Skill-Writer.md`。创建新 Skill 时必须遵循此规范。

### 文档撰写规则

- **前置条件**：撰写任何文档前，必须先 `Read` `Skills/Doc-Writer.md` 加载文档写作规范。
- **默认角色**：若用户未指明文档角色或写作风格，一律按照 `Doc-Writer` 的规范执行。

## 上下文模型

### 三区上下文

- **永久冻结区**：系统提示词，不可修改
- **暂时冻结区**：已确认内容，按需合并或丢弃
- **自然增长区**：当前任务内容

任务结束后可选择性合并或直接丢弃（`merge_on_keep` 控制）。

### 并行模式

多线程独立 snapshot，不污染共享上下文。并行时强制确定性输出。

## 核心 Skill 清单

| Skill | 触发条件 | 用途 |
| :--- | :--- | :--- |
| Skill-Writer | 创建新 Skill | Skill 写作规范指导 |
| Doc-Writer | 编写文档 | 结构化技术文档 |
| Code-Planner | 代码实现/开发需求 | 编程任务敲定：需求+方案+BDD+测试 |
