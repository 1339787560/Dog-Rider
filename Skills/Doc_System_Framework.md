```yaml
name: Doc_System_Framework
purpose: 定义基于角色分工的文档协作系统架构
execution_trigger: 当讨论 doc 书写规范、角色分工、info-cut 机制时激活
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

## 待落地

1. **Role Profile 格式**（Expression + Format Catalog 引用）
2. **Format 模板格式**（slot 定义 + targets + depends_on）
3. **渲染算法**（输入：源 doc × Reader Profile → 输出：InfoCut 后的裁切 doc）