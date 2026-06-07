```yaml
Skill_Name: Doc_System_Framework
Purpose: 定义基于角色分工的文档协作系统架构
Execution_Trigger: 当用户请求操作文档协作系统（创建角色、设计模板、查询渲染）时激活
```

# Doc System Framework - 文档协作系统架构

## 核心目标
- **info-cut**：最大化信息缩减，同一写作角色产出文档格式高度相同，通过阅读角色调整表述

---

## 关键术语定义

| 术语 | 归属 | 含义 |
| :--- | :--- | :--- |
| **表达结构（Format）** | 写作者主权 | 讲故事的骨架，**永远不变** |
| **表达方式（Expression）** | 读者主权 | 语言密度、术语深度、载体选择（代码块/伪代码/散文） |
| **Info-cut** | 读者驱动 | 隐去读者不关心或理解成本高的内容 |

---

## 渲染公式

```
Doc.rendered = Doc.Content × Writer.Format(slots bound to readers) × Reader.Expression × Reader.InfoCut
```

---

## 架构选择

### 方案 B（推荐）：独立渲染 Agent
- 写作者只输出"源 doc"（按自己 Format 的完整结构化内容）
- 渲染 agent 拿源 doc + Reader Profile 跑出最终版
- 写作者只关心自己的 Format；读者只维护自己的 Profile

### 角色 Format Catalog
- 每个角色拥有 **Format Catalog**（而非单一 Format）
- 每个 Format 是 (Purpose × 主要 Target Reader) 的命名实例
- 例：程序员有 `程序设计文档`、`程序实现文档`、`程序自测用例`

---

## 结构性 YAML 方案

每个 doc 的 YAML 头包含 `structure` map：

```yaml
---
name: 程序实现文档
writer: 程序员
version: 1.0
structure:
  - id: overview
    title: 概要
    purpose: 一句话说明这份文档讲什么
    targets: [all]

  - id: data_model
    title: 数据模型
    purpose: 核心数据结构定义
    targets: [程序员, 测试]
    depends_on: []

  - id: api_contracts
    title: 接口契约
    purpose: 前后端/模块间接口定义
    targets: [程序员]
    depends_on: [data_model]

  - id: business_flow
    title: 业务流程
    purpose: 业务逻辑流转说明
    targets: [运营, 策划]
    depends_on: []
---
```

Agent 读取流程：
1. 读 YAML 头 → 拿到 structure map
2. 根据 target_reader 过滤 → 保留 `targets` 包含自己的节
3. 按 `depends_on` 拓扑排序决定读取顺序

---

## Format 可拓展性

- `Formats/` 目录下每增加一个 `.md` 文件（Format 模板），注册表自动膨胀
- 新角色出现 → 在 `Roles/` 下建 Profile，Profile 引用现成的 Formats
- Format 可跨角色复用（如"会议纪要"多角色共用）

---

## Role Profile 格式（已落地）

每个 Role Profile 是一个 `.md` 文件，放在 `Roles/` 目录下，内含两层：

### Expression 层（Reader 视角）

当此角色作为 **Reader** 时，渲染 Agent 按以下偏好裁切源 doc。

| 维度 | 值域 | 含义 |
| :--- | :--- | :--- |
| **语言密度** | dense / balanced / narrative | dense=短句少修饰；balanced=适中；narrative=故事化长文 |
| **术语深度** | expert / professional / layman | expert=直接领域术语；professional=适度解释；layman=通俗化 |
| **载体偏好** | code_block / pseudocode / prose / diagram | 优先级从高到低排列，渲染 Agent 按此顺序选择呈现载体 |
| **info-cut 规则** | skip_rationale / skip_detail / keep_all | skip_rationale=隐去推导只留结论做法；skip_detail=隐去细节只留概要；keep_all=全保留 |
| **skip-sections** | [section_id, ...] | 主动跳过的节，覆盖 targets 过滤；允许角色声明"虽然写给我但我不看" |

#### Expression 补充规则
- 遇到 `depends_on` 引用的节，只读引用声明（id + purpose），不读完整内容
- 遇到代码块，按载体偏好裁切：code_block=保留签名+核心逻辑；pseudocode=转伪代码；prose=转散文描述

### Format Catalog 层（Writer 视角）

当此角色作为 **Writer** 时，从以下 Format 中选择模板。

| Format Name | Purpose | Primary Reader | 文件 |
| :--- | :--- | :--- | :--- |
| (示例) 程序设计文档 | 架构决策与模块划分 | 程序员, 架构师 | Formats/program_design.md |
| (示例) 程序实现文档 | 数据模型 + 接口契约 + 实现细节 | 程序员 | Formats/program_impl.md |

### 设计决策记录

| 决策点 | 选择 | 理由 |
| :--- | :--- | :--- |
| Expression 和 Catalog 同文件还是分文件？ | 同文件 | 角色是原子单位，拆文件导致跨文件引用碎片化；同文件内用 H2 分层，Agent 按 reader/writer 选择性加载 |
| Expression 用 YAML 还是表格？ | 表格 | 维度是有限枚举，表格比 YAML map 更易扫描，与 Framework 术语表风格一致 |
| info-cut 规则放在 Expression 层还是独立机制？ | Expression 层 | info-cut 是"读者不关心什么"，纯读者视角裁切，归入 Expression 语义正交 |
| skip-sections 和 targets 过滤是否重复？ | 不重复，互补 | targets 是源 doc 声明"写给谁"，skip-sections 是角色主动声明"不看什么"，后者覆盖前者 |
| Format Catalog 用引用表而非内联模板？ | 引用表 | 模板跨角色复用时内联导致重复和维护灾难 |

---

## Harness / Guideline 分界原则

系统中的声明分为两类，分界线是 **"是否影响渲染 Agent 的结构决策"**：

| 类型 | 定义 | 机器可执行 | 示例 |
| :--- | :--- | :--- | :--- |
| **Harness** | 不需要"理解"就能确定性执行的声明 | ✅ | targets → 过滤；depends_on → 排序；required → 缺失报错 |
| **Guideline** | 需要语义理解才能执行的自然语言指引 | ❌ | "格式：代码块"、"禁止：技术细节" |

分界规则：
- **YAML 头 = Harness** — 渲染 Agent 直接解析，做路由、过滤、排序、校验
- **Markdown 体 = Guideline** — 写给 Writer 的填写指引，渲染 Agent 不做内容合规硬校验
- 质量兜底交给写作阶段的 Grill-me 协议，而非运行时 harness 硬校验

---

## Format 模板格式（已落地）

每个 Format 模板是一个 `.md` 文件，放在 `Formats/` 目录下，由 YAML 头（Harness）+ Markdown 体（Guideline）组成。

### YAML 头（Harness）

```yaml
---
name: 程序实现文档
writer: 程序员
version: 1.0
structure:
  - id: overview
    title: 概要
    purpose: 一句话说明这份文档讲什么
    targets: [all]
    depends_on: []
    required: true

  - id: data_model
    title: 数据模型
    purpose: 核心数据结构定义
    targets: [程序员, 测试]
    depends_on: []
    required: true

  - id: api_contracts
    title: 接口契约
    purpose: 前后端/模块间接口定义
    targets: [程序员]
    depends_on: [data_model]
    required: true

  - id: error_handling
    title: 异常处理
    purpose: 错误码与恢复策略
    targets: [程序员, 运维]
    depends_on: [api_contracts]
    required: false

  - id: appendix
    title: 附录
    purpose: 外部依赖索引与参考资料
    targets: [all]
    depends_on: []
    required: false
---
```

新增字段说明：
- **required** — Writer 必填/可省标识；渲染 Agent 遇到缺失 required slot 可报错或留空占位

### Markdown 体（Guideline）

每个 slot 一个 H3，定义填写约束，写给 Writer 看：

```markdown
### slot: overview
- 格式：单段散文，≤2 句
- 禁止：技术细节、代码块
- 示例：「本文档定义用户模块的数据库表结构、REST 接口契约及异常处理策略。」

### slot: data_model
- 格式：代码块（SQL DDL / TypeScript interface / JSON Schema 三选一）
- 必须包含：字段名、类型、约束
- 禁止：业务逻辑描述

### slot: api_contracts
- 格式：每个接口一个 H4，内含签名 + 请求/响应示例
- depends_on 引用：data_model 中的类型定义，用 `[[data_model#UserDTO]]` 链接

### slot: error_handling
- 格式：表格（错误码 | 触发条件 | 恢复策略）
- 可选 slot，无异常场景可省略

### slot: appendix
- 格式：引用块 `>` 列表
- 仅放外部链接，不放正文内容
```

### slot 引用语法

- `[[id#anchor]]` — 跨 slot 链接，指向依赖 slot 的具体锚点
- 渲染 Agent 据此做跨 slot 引用追踪，但不校验链接是否有效

### `targets: [all]` 语义

- `all` 为保留关键字，等同于不过滤
- 渲染 Agent 遇到 `all` → 所有角色均保留此节

### 设计决策记录

| 决策点 | 选择 | 理由 |
| :--- | :--- | :--- |
| YAML 头 + Markdown 体双区？ | 是 | YAML 头机器可读做路由，Markdown 体人可读做填写指引，分离关注点 |
| Harness / Guideline 分界？ | "是否影响结构决策" | 影路由→harness；影响质量→guideline；避免过度结构化导致维护膨胀 |
| 新增 required 字段？ | 是 | Writer 知必填/可省；渲染 Agent 知缺失是报错还是允许空 |
| slot 引用语法 `[[id#anchor]]`？ | 是 | 与 depends_on 配合，Writer 可链接依赖 slot 的锚点，渲染 Agent 做引用追踪 |
| slot 格式约束放模板而非 Role Profile？ | 模板 | 格式约束是"这个位置填什么"（模板主权），不是"读者想怎么看"（角色主权） |
| targets `[all]` 硬编码？ | 保留关键字 | 比枚举所有角色简洁，等同于不过滤 |

---

## 渲染算法（已落地）

渲染公式：`Doc.rendered = Doc.Content × Writer.Format × Reader.Expression × Reader.InfoCut`

落地为 4 个阶段，阶段顺序不可逆——先裁结构再裁内容，因为结构裁切决定"读不读"，内容裁切决定"怎么读"。

### 阶段 1：结构裁切（Harness）

```
输入: 源 doc YAML 头 + Reader.targets + Reader.skip-sections
输出: 过滤后的 section 列表 + 拓扑排序后的读取顺序
```

- 保留 `targets` 包含 Reader 角色的节
- `targets: [all]` → 保留（等同不过滤）
- 删除 Reader `skip-sections` 中声明的节（覆盖 targets 保留）
- 按 `depends_on` 对保留的节做拓扑排序，确定读取顺序

### 阶段 2：内容加载（Harness）

```
输入: 阶段1输出的 section 列表 + depends_on 声明
输出: 按依赖顺序加载的 section 内容
```

- 保留节 → 加载完整内容
- 被依赖但不在 Reader targets 中的节 → 只加载引用声明（id + purpose），不加载完整内容
- 无依赖关系的节按阶段1排序顺序加载

### 阶段 3：信息裁切（Guideline）

```
输入: 阶段2加载的完整内容 + Reader Expression.info-cut 规则
输出: 裁切后的内容
```

按 Reader 的 info-cut 规则删减内容：
- **skip_rationale** — 隐去推导段落，只保留"做了什么"和"怎么做"
- **skip_detail** — 隐去细节，只保留概要和结论
- **keep_all** — 全保留，不做删减
- 代码块裁切：保留签名+核心逻辑，删除注释和示例调用（与 Expression 载体偏好独立，此处只删不减）

### 阶段 4：表达转换（Guideline）

```
输入: 阶段3裁切后内容 + Reader Expression 偏好（语言密度、术语深度、载体偏好）
输出: 最终 rendered doc
```

- **语言密度** — dense=压缩为短句；balanced=保持原文节奏；narrative=展开为叙述体
- **术语深度** — expert=保留术语原文；professional=术语后附简释；layman=替换为通俗表述
- **载体偏好** — 按偏好优先级转换呈现载体：code_block→保留代码；pseudocode→转伪代码；prose→转散文描述；diagram→生成结构图描述

### 设计决策记录

| 决策点 | 选择 | 理由 |
| :--- | :--- | :--- |
| 阶段1-2 vs 阶段3-4 归属？ | 1-2=Harness，3-4=Guideline | 符合 Harness/Guideline 分界原则：结构决策确定性执行，内容裁切需语义理解 |
| 阶段3和4合并还是分开？ | 分开 | info-cut 是"删什么"，expression 是"改怎么说"，语义正交且符合单职原则 |
| 被依赖但不在 targets 的节怎么处理？ | 只读 id+purpose | 与 Role Profile Expression 补充规则一致，避免加载读者不关心的完整内容 |
| 阶段顺序可逆吗？ | 不可逆 | 结构裁切决定"读不读"是前提，内容裁切决定"怎么读"是后续，逻辑依赖链不可颠倒 |

---

## 执行步骤（已落地）

### 操作1：创建/修改 Role Profile

#### 步骤1：角色定位确认
- **新增角色** → 确认角色名称、与已有角色的关系（是否继承父角色）
- **修改角色** → 读取现有 Profile，确认修改范围（Expression 层 / Format Catalog 层 / 两者）

#### 步骤2：Expression 层填写
- 逐一确认 5 个维度（语言密度、术语深度、载体偏好、info-cut 规则、skip-sections）
- 每个维度给出值域选项，用户选择
- 执行维度组合冲突检测规则（见下方）

#### 步骤3：Format Catalog 层填写
- 从 `Formats/` 目录列出可选模板
- 确认该角色作为 Writer 时使用的模板及 Primary Reader
- 若所需模板不存在 → 建议先执行"操作2：设计模板"

#### 步骤4：产出与确认
- 生成/更新 `Roles/<角色名>.md`
- 用户确认后写入文件

#### Expression 维度组合冲突检测规则

| 规则 | 条件 | 冲突 | 建议 |
| :--- | :--- | :--- | :--- |
| 术语-载体排斥 | 术语深度=layman 且 载体偏好首选项=code_block | 通俗化术语与代码块载体语义冲突 | 降级载体为 pseudocode 或 prose |
| 密度-裁切矛盾 | 语言密度=narrative 且 info-cut=skip_detail | 叙述体要求展开但裁切要求压缩 | 建议调整为 balanced + skip_detail 或 narrative + keep_all |
| 密度-术语矛盾 | 语言密度=dense 且 术语深度=layman | 短句密度与通俗化解释需求矛盾 | 建议调整为 dense + professional 或 balanced + layman |
| 裁切冗余 | info-cut=keep_all 且 skip-sections 有内容 | keep_all 与主动跳过矛盾 | 建议移除 skip-sections 或降级 info-cut 为 skip_detail |

冲突检测为 Harness 层逻辑：Agent 发现冲突时必须向用户报告并建议调整，不得静默忽略。

### 操作2：创建/修改 Format 模板

#### 步骤1：模板定位确认
- **新增模板** → 确认 name、writer 归属、Purpose
- **修改模板** → 读取现有模板，确认修改范围（YAML 头 / Markdown 体）

#### 步骤2：structure map 设计（YAML 头）
- 逐一定义 slot：id / title / purpose / targets / depends_on / required
- 画出 depends_on 依赖关系，检测循环依赖
- 用户确认 structure map

#### 步骤3：slot Guideline 编写（Markdown 体）
- 逐 slot 编写填写约束：格式 / 必须包含 / 禁止 / 示例
- 用户确认 Guideline

#### 步骤4：产出与联动
- 生成/更新 `Formats/<模板名>.md`
- 检查 `Roles/` 中的 Format Catalog 是否需要更新引用
- 用户确认后写入文件

### 操作3：查询渲染流程

#### 步骤1：场景锁定
- 确认 Reader 角色 + 源 doc
- 读取对应的 Role Profile + Format 模板 YAML 头

#### 步骤2：逐阶段推演
- **阶段1** — 列出 targets 过滤结果 + skip-sections 覆盖结果 + 拓扑排序
- **阶段2** — 列出完整加载 vs 仅引用声明（id+purpose）的节
- **阶段3** — 按 info-cut 规则标注哪些内容会被裁切
- **阶段4** — 按 Expression 偏好描述最终表达形式

#### 步骤3：产出
- 输出渲染推演报告（裁切决策的解释，非最终 rendered doc）

---

## 协作协议（已落地）

### Skill 边界定义

| Skill | 职责范围 | 核心操作 |
| :--- | :--- | :--- |
| **Doc_Architect_Skill** | 写作层 — 用模板填内容 | 帮用户写具体文档 |
| **Doc_System_Framework** | 系统层 — 构建协作系统 | 创建角色、设计模板、查询渲染 |

### 交接方式

**文件系统交接，不通过上下文传递**
- Framework 产出文件到 `Roles/` 和 `Formats/`
- Architect 读取这些文件作为输入
- 无上下文传递、无内存共享、无回调引用

### 交接触发

| 方向 | 触发条件 | 交接动作 |
| :--- | :--- | :--- |
| **Framework → Architect** | 用户完成模板创建/修改后 | Agent 主动提示用户"可以基于此模板撰写文档" |
| **Architect → Framework** | Architect 发现所需模板不存在时 | Agent 主动建议用户"当前没有合适的模板，是否要先创建？" |
| **并行不交** | 用户纯写文档 / 用户纯操作系统 | 无交接，各自独立运行 |

### 跨界约束

- 跨界操作必须征得用户同意，Agent 不得自动切换 Skill
- Architect 发现模板缺失 → 建议用户切换到 Framework，不原地降级继续
- Framework 产出模板后 → 提示用户可切回 Architect 继续写作

---

## 待落地

1. ~~**Role Profile 格式**~~（已落地）
2. ~~**Format 模板格式**~~（已落地）
3. ~~**渲染算法**~~（已落地）
4. ~~**执行步骤**~~（已落地）
5. ~~**协作协议**~~（已落地）

### Expression 维度扩展路线

当前采用 **方案 A：固定维度枚举**，5 个维度（语言密度、术语深度、载体偏好、info-cut 规则、skip-sections）。

后续可考虑的扩展方向：
- **维度增量注册机制** — 新维度需在 Framework 层声明值域后才允许角色使用，避免自由维度导致的渲染不确定性
- ~~**维度组合冲突检测**~~ — 已落地为 Harness 层规则表，后续随维度扩展同步更新规则表
- **角色继承** — 子角色（如"前端程序员"）继承父角色（"程序员"）的 Expression，只覆盖差异维度
- **场景化 Expression 覆盖** — 同一角色在不同场景（如"快速排查 bug"vs"学习新模块"）使用不同的 Expression 偏好

### Format Catalog 扩展路线

- **Format 版本演进** — 当模板结构变化时，Catalog 引用需支持版本锁定或兼容性声明
- **多角色共创 Format** — 当一个 Format 的 slots 由多个角色共同填写时，需定义 slot 的 writer归属