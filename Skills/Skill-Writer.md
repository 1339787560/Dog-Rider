```yaml
Skill_Name: Skill-Writer
Purpose: 定义 Skill 文件的标准结构、格式约束和编写流程
Execution_Trigger: 用户请求创建新 Skill 或 Agent 指令集时激活
```

## 概要

本规范定义 Skill 文件的唯一合法结构。创建新 Skill 前必须阅读本文件。

核心结构：

1. YAML Header — 元数据声明
2. 概要 — 一段话 + 有序列表，说明本 Skill 做什么、分几步
3. 详细规范 — 每个步骤一个 `###`，展开为可执行指令
4. Harness — 需要严格控制输出时，提供结构化 schema
5. 附录（可选） — 术语表、模板、示例

---

## 详细规范

### 详细规范 1：YAML Header

文件第一个内容块，必须用 ` ```yaml ` 包裹，包含且仅包含三个字段：

| 字段 | 类型 | 约束 |
| :--- | :--- | :--- |
| `Skill_Name` | string | 与文件名一致（不含 `.md`） |
| `Purpose` | string | 一句话，不超过 30 字 |
| `Execution_Trigger` | string | 描述用户意图，Agent 用于路由匹配 |

#### 反模式

- 不要在 Header 中添加额外字段（如 `Version`、`Author`）
- 不要在 ` ```yaml ` 前放任何内容（包括空行）

### 详细规范 2：概要

概要是 Skill 的"目录 + 说明书"，必须包含两个固定槽位：

**槽位 A — 范围声明（1-2 句话）：**
说明本 Skill 解决什么问题、在什么场景下使用。

**槽位 B — 步骤清单（有序列表）：**
列出本 Skill 的核心步骤，每个步骤一行，不超过 20 字。步骤标题必须与后续 `###` 标题完全一致。

#### 写作规则

- 槽位 A 和槽位 B 之间用空行分隔
- 步骤清单项数控制在 2-6 项
- 超过 6 项时拆分为多个 Skill

### 详细规范 3：详细规范

每个详细规范是独立的可执行指令块，必须满足：

**结构要求：**
- 标题格式：`### 详细规范 N：{标题}`，N 从 1 开始递增
- 标题必须与概要步骤清单中的文字完全一致
- 每个详细规范内可以使用 H4（`####`）划分子规则

**内容要求：**
- 每条规则用列表项表达，开头用动词（禁止/必须/可以/建议）
- 规则之间如果有因果关系，用箭头（→）标注
- 需要示例时用代码块，用 `#### 示例` 作为标题

**反模式：**
- 不要用散文段落代替规则列表
- 不要在详细规范中重复概要已说过的内容

### 详细规范 4：使用 Harness

当 Skill 要求 Agent 输出结构化内容时，必须提供 Harness schema 而非自然语言描述。

**什么是 Harness：**
Harness 是对输出格式的显式约束——用 JSON Schema、YAML 模板、表格骨架或代码块模板，规定输出必须填入的字段和结构。Agent 遵循 schema 生成内容，比遵循散文指令准确得多。

**何时使用：**
- 输出需要被下游程序解析（JSON / YAML / CSV）
- 输出需要跨多个 Skill 保持一致格式
- 输出字段有固定枚举或类型约束
- plain text 描述会导致 Agent 自由发挥、格式漂移

**写法规则：**
- 在详细规范中用 `#### Harness` 标题放置 schema
- schema 必须包含所有必填字段，用 `{占位符}` 标注待填项
- 可选字段用 `// 可选` 注释标注
- 如果字段有枚举值，用注释列出所有合法值

#### 示例

以下为一个 commit message 的 Harness，比"请写好 commit message"更精确：

```yaml
# Harness: Commit Message
header:
  type: {feat|fix|refactor|docs|chore}
  scope: {模块名}       # 可选
  description: {不超过 50 字}
body:
  - {what and why, 不写 how}  # 可选，多行
footer:
  - "Closes #{issue_number}"  # 可选
```

### 详细规范 5：附录

可选部分，用于放置不便放在正文中的补充内容：

- **术语表** — 发明的术语必须在此注册
- **模板** — 可复制的代码块模板
- **示例** — 完整的填写示例

---

## 附录

### Skill 模板

创建新 Skill 时以此为起点，删除所有 `{占位符}` 并填入实际内容：

````markdown
```yaml
Skill_Name: {名称}
Purpose: {一句话描述，不超过 30 字}
Execution_Trigger: {用户意图描述}
```

## 概要

{范围声明：本 Skill 用于什么场景，解决什么问题。}

1. {步骤一标题}
2. {步骤二标题}
3. {步骤三标题}

---

## 详细规范

### 详细规范 1：{步骤一标题}

- {规则一}
- {规则二}

### 详细规范 2：{步骤二标题}

- {规则一}
- {规则二}

### 详细规范 3：{步骤三标题}

- {规则一}
- {规则二}

#### Harness（如需严格控制输出格式）

```yaml
{schema 定义：字段名、类型、枚举值}
```
````

### 填写示例

以下为一个已填写完成的 Skill 片段，展示实际写法：

````markdown
```yaml
Skill_Name: Git_Commit_Convention
Purpose: 定义 commit message 的标准格式
Execution_Trigger: 用户请求创建 commit message 规范时激活
```

## 概要

本规范用于统一 commit message 格式，确保 git log 可读、可检索。

1. Header 格式
2. Body 格式
3. 禁止事项

---

## 详细规范

### 详细规范 1：Header 格式

- 格式：`{type}({scope}): {description}`
- type 必须为以下之一：feat / fix / refactor / docs / chore
- scope 为可选的模块名
- description 不超过 50 字，英文小写，不加句号

#### 示例

```
feat(auth): add JWT refresh token rotation
fix(api): handle null response in user endpoint
```

### 详细规范 2：Body 格式

- Body 与 Header 之间空一行
- 说明 what 和 why，不写 how
- 每行不超过 72 字符

### 详细规范 3：禁止事项

- 禁止使用 `fix bug`、`update code` 等无意义描述
- 禁止在 description 末尾加句号

#### Harness

```yaml
# Harness: Commit Message
header:
  type: {feat|fix|refactor|docs|chore}
  scope: {模块名}         # 可选
  description: {不超过 50 字}
body:
  - {what and why}        # 可选，多行
footer:
  - "Closes #{issue}"     # 可选
```
````
