"""基础 Agent 主循环 - 最小实现，无 Dog-Rider 扩展"""
import json
import sys
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import BaseConfig
from .context import BaseContext
from .tools import BaseToolRegistry


@dataclass
class UsageStats:
    """API 调用统计"""
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    requests: int = 0


class BaseAgentLoop:
    """基础 Agent 主循环 - 纯工具调用 + 聊天

    可直接继承此类进行二次开发，或直接实例化使用：

    ```python
    from src.base import BaseAgentLoop, BaseConfig, create_default_tools

    config = BaseConfig.from_env()
    tools = create_default_tools()
    agent = BaseAgentLoop(config, tools, "You are a helpful assistant.")

    response = agent.run("Hello!")
    print(response)
    ```
    """

    def __init__(
        self,
        config: BaseConfig,
        tools: BaseToolRegistry,
        system_prompt: str = "You are a helpful assistant.",
    ):
        self.config = config
        self.tools = tools
        self.context = BaseContext()
        self.context.init_with_system(system_prompt)
        self.stats = UsageStats()

    def _call_api(self, messages: List[dict]) -> dict:
        """调用 LLM API"""
        body = {
            "model": self.config.model.model,
            "messages": messages,
            "max_tokens": self.config.model.max_tokens,
            "tools": self.tools.get_tool_defs(),
            "tool_choice": "auto",
        }

        url = self.config.model.base_url.rstrip("/") + "/chat/completions"
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, headers={
            "Authorization": f"Bearer {self.config.model.api_key}",
            "Content-Type": "application/json",
        })

        try:
            with urllib.request.urlopen(req, timeout=self.config.model.timeout) as resp:
                result = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            err_body = e.read().decode()
            print(f"\n[API Error {e.code}] {err_body}", file=sys.stderr)
            raise

        # 更新统计
        usage = result.get("usage", {})
        self.stats.total_prompt_tokens += usage.get("prompt_tokens", 0)
        self.stats.total_completion_tokens += usage.get("completion_tokens", 0)
        self.stats.requests += 1

        return result

    def run(self, user_input: str) -> str:
        """执行一个用户请求

        Returns:
            Agent 的最终回复内容
        """
        self.context.append({"role": "user", "content": user_input})

        while True:
            result = self._call_api(self.context.messages)
            choice = result["choices"][0]
            message = choice["message"]
            finish_reason = choice.get("finish_reason")

            if finish_reason == "tool_calls" and message.get("tool_calls"):
                self.context.append(message)
                for tc in message["tool_calls"]:
                    fn_name = tc["function"]["name"]
                    args = json.loads(tc["function"]["arguments"])
                    tool_output = self.tools.execute(fn_name, **args)
                    self.context.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": tool_output,
                    })
                continue

            # 最终回复
            content = message.get("content", "")
            self.context.append(message)
            return content

    def reset(self):
        """重置对话，保留 system prompt"""
        self.context.clear_natural()
