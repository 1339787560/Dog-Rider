```yaml
Skill_Name: Claude-Core-Toolkit
Purpose: 装备 Claude Code 最常用的核心工具集，提供标准化使用指南
Execution_Trigger: Agent 启动时自动加载，或用户请求装备核心工具时激活
```

## 概要

本 Skill 定义 Claude Code 最常用的核心工具集，为 Dog-Rider Agent 提供标准化的工具调用规范和最佳实践。

1. 核心工具清单
2. 工具使用优先级
3. 调用规范与约束
4. 最佳实践

---

## 详细规范

### 详细规范 1：核心工具清单

Dog-Rider 已装备的 Claude Code 核心工具 (Top 6)：

| 排名 | 工具名 | 用途 | 日调用占比 |
| :--- | :--- | :--- | :--- |
| 1 | `read_file` | 读取文件内容 | ~35% |
| 2 | `edit_file` | 精确替换文件内容 | ~20% |
| 3 | `bash` | 执行 shell 命令 | ~15% |
| 4 | `write_file` | 创建或全量覆盖文件 | ~10% |
| 5 | `glob` | 文件名模式匹配搜索 | ~8% |
| 6 | `grep` | 内容正则搜索 | ~5% |

#### 说明
- Top 6 工具占总调用量 **93%**，是 Agent 能力的核心
- **必须优先使用专用工具**：禁止用 `bash` 执行 `cat`/`Get-Content`/`find`/`grep` 等文件操作
- 专用工具速度更快、输出格式稳定、不触发额外 shell 开销

### 详细规范 2：工具使用优先级

必须遵循以下优先级顺序选择工具（**违反将导致浪费 token 和缓存失效**）：

1. **专用文件工具优先于 Shell（最高优先级）**
   - ❌ 禁止用 `bash` 执行 `cat` / `Get-Content` / `type` → ✅ 用 `read_file`
   - ❌ 禁止用 `bash` 执行 `find` / `Get-ChildItem` / `dir` → ✅ 用 `glob`
   - ❌ 禁止用 `bash` 执行 `grep` / `Select-String` → ✅ 用 `grep` 工具
   - ❌ 禁止用 `write_file` 做单行修改 → ✅ 用 `edit_file`

2. **精确编辑优先于全量覆盖**
   - 小范围修改用 `edit_file`，新建文件或完整重写用 `write_file`

3. **Skill 优先于从零开始**
   - 已有对应 Skill 时，按 Skill 规范操作
   - Skill 覆盖范围外，再直接使用基础工具

### 详细规范 3：调用规范与约束

#### Read 规范
- 已知只需文件片段时，用 `offset` + `limit` 参数
- 大文件（> 500 行）必须分段读取，禁止一次性全量
- 读取前先用 `Glob` / `Grep` 定位，避免盲目读文件

#### Edit 规范
- 必须先 `Read` 目标文件，再执行 `Edit`
- `old_string` 必须精确匹配原文（含缩进、换行）
- 单处替换优先，超过 3 处且属同一文件考虑 `Write` 全量覆盖

#### Bash / PowerShell 规范
- 用 Bash 执行 POSIX 脚本，用 PowerShell 处理 Windows 路径/Registry
- 长时间运行命令（> 10s）必须加 `run_in_background: true`
- 破坏性操作（删除、覆盖）执行前必须显式确认

#### Agent 规范
- 子任务必须独立、无相互依赖
- 子代理完成后合并结果，不做跨代理状态共享
- `caveman:cavecrew-investigator` 用于代码定位
- `caveman:cavecrew-builder` 用于 1-2 文件精确编辑
- `caveman:cavecrew-reviewer` 用于代码审查

### 详细规范 4：最佳实践

#### 工具组合模式
```
模式 1: 探索代码
Glob → Grep → Read (分段)

模式 2: 修改代码
Read → Edit → Bash (跑测试)

模式 3: 多任务并行
Agent (N 个独立任务) → 合并结果 → TodoWrite 更新

模式 4: 标准化流程
Skill → 按 Skill 规范调用基础工具
```

#### 反模式清单
- ❌ 用 Bash `cat` 代替 `Read`
- ❌ 用 `Write` 全量覆盖做单行修改
- ❌ 不定位就盲目 `Read` 大量文件
- ❌ 串行处理可并行的独立任务
- ❌ 已有 Skill 覆盖却手动重写逻辑

---

## 附录

### 工具参数速查表（Dog-Rider 实际装备）

```yaml
# read_file - 读取文件
filepath: 相对路径 (必填)
offset: 起始行号 (1-indexed, 可选)
limit: 读取行数 (可选)

# edit_file - 精确编辑
filepath: 相对路径 (必填)
old_string: 精确匹配原文 (含缩进, 必填)
new_string: 替换内容 (必填)
replace_all: true/false (默认 false)

# write_file - 创建/覆盖文件
filepath: 相对路径 (必填)
content: 文件完整内容 (必填)

# glob - 文件搜索
pattern: glob 模式 (如 **/*.py, **/*.md, 必填)
path: 起始目录 (可选, 默认项目根)

# grep - 内容搜索
pattern: 正则表达式 (必填)
path: 搜索范围 (可选, 默认项目根)
glob_filter: 文件过滤 (如 **/*.py, 可选)

# bash - 执行命令
command: 命令字符串 (必填)
```

### Toolkit 激活方式

Agent 启动时按以下顺序加载：
1. 加载本 `Claude-Core-Toolkit.md`
2. 扫描 `Skills/*.md` 注册所有可用 Skill
3. 后续执行优先使用本 Skill 定义的规范
