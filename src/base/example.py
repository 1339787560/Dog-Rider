"""基础 Agent 使用示例

直接基于 BaseAgentLoop 进行开发，不使用 Dog-Rider 的扩展功能。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.base import BaseConfig, BaseAgentLoop
from src.base.tools import create_default_tools


def main():
    """演示基础 Agent 用法"""
    # 1. 加载配置
    config = BaseConfig.from_env()
    if not config.model.api_key:
        print("Error: DEEPSEEK_API_KEY not set")
        print("Please set it in .env file or environment variable.")
        sys.exit(1)

    # 2. 创建工具集
    tools = create_default_tools()

    # 3. 创建 Agent
    agent = BaseAgentLoop(
        config=config,
        tools=tools,
        system_prompt="You are a helpful assistant. Use tools when needed.",
    )

    # 4. 交互模式
    print(f"Base Agent (minimal)")
    print(f"Model: {config.model.model}")
    print("Commands: /stats /quit")
    print()

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue
        if user_input == "/quit":
            break
        if user_input == "/stats":
            print(f"  Requests: {agent.stats.requests}")
            print(f"  Prompt tokens: {agent.stats.total_prompt_tokens}")
            print(f"  Completion tokens: {agent.stats.total_completion_tokens}")
            continue

        response = agent.run(user_input)
        print(f"\n{response}\n")


if __name__ == "__main__":
    main()
