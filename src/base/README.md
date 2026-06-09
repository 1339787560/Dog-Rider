# Base Agent Framework

最小化的 Agent 基础框架，可直接用于二次开发，不依赖 Dog-Rider 的扩展功能。

## 架构分层

```
┌─────────────────────────────────────────┐
│  Dog-Rider 扩展 (src/*.py)              │
│  - 三区上下文模型                        │
│  - 任务级价值判定与丢弃策略              │
│  - 缓存命中率追踪与优化                  │
│  - Claude Core Toolkit (6 tools)        │
├─────────────────────────────────────────┤
│  Base Framework (src/base/*.py)         │
│  - BaseConfig: 基础配置                  │
│  - BaseContext: 简单消息列表             │
│  - BaseToolRegistry: 可扩展工具系统      │
│  - BaseAgentLoop: 纯工具调用循环         │
│  - 默认工具集: bash/read/write (3)      │
└─────────────────────────────────────────┘
```

## 快速开始

### 基础 Agent (3 tools)

```python
from src.base import BaseConfig, BaseAgentLoop
from src.base.tools import create_default_tools

# 1. 加载配置
config = BaseConfig.from_env()

# 2. 创建工具集 (bash/read_file/write_file)
tools = create_default_tools()

# 3. 创建 Agent
agent = BaseAgentLoop(
    config=config,
    tools=tools,
    system_prompt="You are a helpful assistant.",
)

# 4. 执行任务
response = agent.run("List all .py files in src directory.")
print(response)
```

### 扩展工具集

```python
from src.base.tools import BaseToolRegistry, bash_command, read_file

# 创建自定义工具集
tools = BaseToolRegistry()
tools.register(
    name="my_tool",
    description="My custom tool.",
    parameters={
        "type": "object",
        "properties": {
            "arg1": {"type": "string", "description": "Argument 1."}
        },
        "required": ["arg1"]
    },
    handler=my_function,
)
```

### 使用完整 Dog-Rider Agent

```python
from src.agent import AgentLoop
from src.config import load_env_config

config = load_env_config()
agent = AgentLoop(config)

# 自动加载:
# - 三区上下文模型
# - 任务级丢弃策略
# - 6 tools (bash/read/write/edit/glob/grep)
# - 完整系统提示词 (SYS/*.md + CLAUDE.md)

response = agent.run_task("Hello!")
```

## 运行示例

```bash
# 基础 Agent
python -m src.base.example

# Dog-Rider Agent (完整版)
python -m src.main
```

## 扩展指南

### 继承 BaseAgentLoop

```python
from src.base.agent import BaseAgentLoop

class MyAgent(BaseAgentLoop):
    def __init__(self, config, tools, system_prompt):
        super().__init__(config, tools, system_prompt)
        # 你的扩展

    def run(self, user_input: str) -> str:
        # 自定义逻辑
        return super().run(user_input)
```

### 扩展 BaseToolRegistry

```python
from src.base.tools import BaseToolRegistry

def my_tool(arg1: str) -> str:
    return f"result: {arg1}"

registry = BaseToolRegistry()
registry.register("my_tool", "Description", {...}, my_tool)
```
