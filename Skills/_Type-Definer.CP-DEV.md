```yaml
Doc_Name: Type-Definer.CP-DEV
Purpose: CP-DEV 角色的文档类型特化覆盖
Reader: Agent（执行 Doc-Writer 时，当角色为 CP-DEV 时读取）
```

## 概要

本文件是 `_Type-Definer.md` 的角色子类，覆盖 CP-DEV（游戏礼包服务工程师）视角下的文档类型偏好。

1. 程序接口文档：由表及里——配置→流程→请求→回调→通信→数据
2. 程序设计文档：TS 脚本层的模块设计偏好
3. 程序详解文档：从 TS 源码逆向的流程解析偏好

---

## 内容

### 类型覆盖

以下为各类型的 override 声明。每个类型必须包含 `extends` 字段，其余 key 仅写与基类不同的部分。

```yaml
程序接口文档:
  extends: 程序接口文档
  定位: 面向 clientDev、gamesvrDev、其他 CP 模块开发者，提供模块对外接口的完整协议参考
  必须包含:
    - 模块标识（MODULE_NAME、APP_CODE、GAME_ID）
    - CP 服务回调（OnPayResult、OnScriptReload 等生命周期函数的签名与行为）
    - 客户端请求接口（按 `req` 字段路由的协议表，含请求参数与响应结构）
    - 模块间通信接口（OnInternalCall 的入站/出站协议，含 req 名与数据体）
    - 客户端通知消息（notify 消息名、触发时机、携带数据）
    - 数据存储结构（Redis key 格式、MySQL 表名与字段名、过期策略）
    - 配置文件字段解析（jsonc 配置中每个字段的业务含义，结合代码中的使用位置说明其实际效果；数组类型仅解析一个元素的结构）
    - 脚本运转流程（文本描述：客户端请求到达后的完整处理链路，包括各 CP 服务回调的触发时机与处理逻辑、客户端行为（充值、登录、结算等）的响应流程）
  不包含:
    - C++ 服务器内部实现
    - 前端 UI 交互细节
    - 协程调度机制
  格式约束:
    - 回调函数签名用 ```typescript 代码块
    - 协议路由用表格（req 值 → 处理逻辑 → 响应结构）
    - 数据结构用 interface/type 定义 + 字段说明表格
    - Redis key 用字符串模板标注 ${变量} 占位符
    - 每个回调/接口独立 `###`，侧边可快速定位
    - 配置字段用表格（字段 / 类型 / 示例值 / 业务含义），数组类型标注"此处以第 N 个元素为例"
    - 配置字段的业务含义必须结合代码中的读取位置（标注函数名或变量名），禁止脱离代码空释义
    - 脚本运转流程用分段文本描述，按"请求入口 → 分支判断 → 业务处理 → 存储写入 → 通知下发"的顺序组织
  术语深度:
    - CP 服务器术语（async_internal_call、modsvr.context 等）可直接使用
    - 项目特有协议名（如 playerLevelChange）首次出现时附一句话说明
    - Redis/MySQL 命名规约需遵循 L0_Index 中的规范
  章节骨架:
    - 模块概述（标识、职责、配置结构）
      - 配置文件字段解析（jsonc 配置字段与代码中的实际效果）
    - 脚本运转流程（客户端请求与行为的完整处理链路）
    - 客户端请求接口（按 req 路由）
      - CP 服务回调（生命周期函数，作为客户端行为的后处理环节）
    - 模块间通信接口（内部调用协议）
    - 数据结构（持久化模型、枚举、配置接口）
    - 工具类（MySQL / Redis 存储抽象）

程序设计文档:
  extends: 程序设计文档
  定位: 面向 CP-DEV 团队成员，接收策划需求，产出 TypeScript 脚本层的模块设计方案
  必须包含:
    - 需求摘要（策划需求中与 CP 服务相关的要点）
    - TS 模块结构（namespace 划分：Business / CommonFuncs / interf / TestTool）
    - 协议设计（客户端 req 路由、模块间 internal call 协议、客户端 notify 消息）
    - 数据模型（UserData 类型、Redis 缓存策略、MySQL 持久化方案）
    - 回调接入点（需要实现哪些 CP 服务回调：OnPayResult / OnClientRequest 等）
  格式约束:
    - 模块结构用 namespace 列表 + 职责说明
    - 协议设计用表格（方向 / req 名 / 数据体 / 说明）
    - 数据模型用 TypeScript interface 代码块
    - 降级/恢复等状态机用 Mermaid stateDiagram

程序详解文档:
  extends: 程序详解文档
  定位: 从已有 TS 脚本源码出发，逆向产出模块的流程解析文档，供 clientDev / gamesvrDev 理解协议行为
  必须包含:
    - 模块入口（OnClientRequest / OnPayResult / OnInternalCall 的分发逻辑）
    - 核心业务流程（充值→经验计算→等级变更→通知客户端 的完整链路）
    - 配置项作用（jsonc 配置中每个关键字段的业务含义）
    - 跨模块依赖（通过 async_internal_call 与哪些模块交互、协议是什么）
  格式约束:
    - 流程用有序列表或 Mermaid sequenceDiagram，标注每步涉及的函数名
    - 代码片段标注 `文件路径:行号`，引用关键逻辑而非逐行翻译
    - 配置项用表格（字段 / 类型 / 当前值 / 业务含义）
    - 先概述模块职责（1-2 句），再展开流程
  术语深度:
    - CP 服务回调名（OnPayResult 等）可直接使用
    - 业务术语（通宝、降级、一次性奖励）首次出现时用一句话定义
    - 跨模块协议名标注来源模块（如 convert 模块的 queryMigrationVipData）
```
