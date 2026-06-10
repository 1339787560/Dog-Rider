"""基础 Agent 主循环 - 最小实现，无 Dog-Rider 扩展"""
import json
import sys
import time
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
        """调用 LLM API — 自动重试 + 指数退避"""
        body = {
            "model": self.config.model.model,
            "messages": messages,
            "max_tokens": self.config.model.max_tokens,
            "tools": self.tools.get_tool_defs(),
            "tool_choice": "auto",
        }

        url = self.config.model.base_url.rstrip("/") + "/chat/completions"
        data = json.dumps(body).encode()

        last_error = None
        for attempt in range(self.config.max_retries):
            try:
                req = urllib.request.Request(url, data=data, headers={
                    "Authorization": f"Bearer {self.config.model.api_key}",
                    "Content-Type": "application/json",
                })
                with urllib.request.urlopen(req, timeout=self.config.model.timeout) as resp:
                    result = json.loads(resp.read())
            except urllib.error.HTTPError as e:
                err_body = e.read().decode()
                last_error = f"HTTP {e.code}: {err_body[:200]}"
                # 4xx 非 429 不重试
                if e.code < 500 and e.code != 429:
                    print(f"\n[API Error {e.code}] {err_body}", file=sys.stderr)
                    raise
            except Exception as e:
                last_error = str(e)[:200]

            if attempt < self.config.max_retries - 1:
                delay = 2 ** attempt
                print(f"\n  [Retry {attempt+1}/{self.config.max_retries}] {last_error} — waiting {delay}s",
                      file=sys.stderr)
                time.sleep(delay)
                continue

            print(f"\n[API Error] All {self.config.max_retries} retries failed: {last_error}",
                  file=sys.stderr)
            raise RuntimeError(f"API call failed after {self.config.max_retries} retries: {last_error}")

        # 更新统计
        usage = result.get("usage", {})
        self.stats.total_prompt_tokens += usage.get("prompt_tokens", 0)
        self.stats.total_completion_tokens += usage.get("completion_tokens", 0)
        self.stats.requests += 1

        return result

    def run(self, user_input: str) -> str:
        """执行一个用户请求 — 健壮循环，不会因单次错误中断。

        防护：
        - max_turns 上限防止无限循环
        - 每步 try/except 防止单点崩溃
        - 工具参数解析失败 → 注入错误消息，继续
        - API 响应结构异常 → 注入错误消息，继续
        """
        self.context.append({"role": "user", "content": user_input})

        for turn in range(self.config.max_turns):
            # ── API 调用 ──
            try:
                result = self._call_api(self.context.messages)
            except Exception as e:
                # 注入错误消息让 LLM 感知，继续循环
                self.context.append({
                    "role": "system",
                    "content": f"[ERROR] API call failed after {self.config.max_retries} retries: {e}. "
                               f"Please continue with what you know, or try a different approach.",
                })
                continue

            # ── 解析响应 ──
            try:
                choice = result["choices"][0]
                message = choice["message"]
                finish_reason = choice.get("finish_reason")
            except (KeyError, IndexError, TypeError) as e:
                self.context.append({
                    "role": "system",
                    "content": f"[ERROR] Unexpected API response structure: {e}. "
                               f"Please continue with what you know.",
                })
                continue

            # ── 工具调用轮次 ──
            if finish_reason == "tool_calls" and message.get("tool_calls"):
                self.context.append(message)
                for tc in message["tool_calls"]:
                    fn_name = tc["function"]["name"]
                    fn_id = tc.get("id", f"call_{turn}")

                    # 解析参数 — 失败不崩溃
                    try:
                        args = json.loads(tc["function"]["arguments"])
                    except (json.JSONDecodeError, KeyError, TypeError) as e:
                        tool_output = f"(error: failed to parse arguments for '{fn_name}': {e})"
                        self.context.append({
                            "role": "tool",
                            "tool_call_id": fn_id,
                            "content": tool_output,
                        })
                        continue

                    # 执行工具
                    try:
                        tool_output = self.tools.execute(fn_name, **args)
                    except Exception as e:
                        tool_output = f"(error executing '{fn_name}': {e})"

                    self.context.append({
                        "role": "tool",
                        "tool_call_id": fn_id,
                        "content": tool_output,
                    })
                continue

            # ── 最终回复 ──
            content = message.get("content", "")
            self.context.append(message)
            return content

        # 达到 max_turns 上限 — 注入错误，给 LLM 最后回应机会
        self.context.append({
            "role": "system",
            "content": f"[ERROR] Reached max_turns={self.config.max_turns}. "
                       f"Please provide your final answer now based on what you have so far.",
        })
        try:
            result = self._call_api(self.context.messages)
            content = result["choices"][0]["message"].get("content", "")
            self.context.append(result["choices"][0]["message"])
            return content
        except Exception:
            return f"(Agent loop stopped: reached max_turns={self.config.max_turns})"

    def reset(self):
        """重置对话，保留 system prompt"""
        self.context.clear_natural()
