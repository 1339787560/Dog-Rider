"""
tiny_agent.py — DeepSeek API 实验用最小 Agent
支持: chat / agent-loop(工具调用) / bash 执行 / 费用统计

用法:
  python Poc/tiny_agent.py              # 交互模式
  python Poc/tiny_agent.py "你的问题"   # 单次模式
"""

import json
import os
import subprocess
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# Windows GBK 编码兼容
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── 配置 ──────────────────────────────────────────────

def load_env():
    env = {}
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
    return env

_env = load_env()
API_KEY  = os.environ.get("DEEPSEEK_API_KEY",  _env.get("DEEPSEEK_API_KEY", ""))
BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", _env.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
MODEL    = os.environ.get("DEEPSEEK_MODEL",    _env.get("DEEPSEEK_MODEL_FLASH", "deepseek-v4-flash"))

PRICING = {
    "deepseek-v4-flash": {"miss": 0.14, "hit": 0.0028, "out": 0.28},
    "deepseek-v4-pro":   {"miss": 0.435, "hit": 0.003625, "out": 0.87},
}

# ── 费用统计 ──────────────────────────────────────────

class Stats:
    def __init__(self):
        self.total_prompt   = 0
        self.total_hit      = 0
        self.total_miss     = 0
        self.total_output   = 0
        self.total_reasoning = 0
        self.total_cost     = 0.0
        self.requests       = 0

    def record(self, usage: dict):
        p = usage.get("prompt_tokens", 0)
        h = usage.get("prompt_cache_hit_tokens", 0)
        m = usage.get("prompt_cache_miss_tokens", p)
        o = usage.get("completion_tokens", 0)
        r = usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0)

        self.total_prompt += p
        self.total_hit    += h
        self.total_miss   += m
        self.total_output += o
        self.total_reasoning += r
        self.requests     += 1

        price = PRICING.get(MODEL, PRICING["deepseek-v4-flash"])
        cost = (m * price["miss"] + h * price["hit"] + o * price["out"]) / 1_000_000
        self.total_cost += cost

    def summary(self) -> str:
        hit_rate = self.total_hit / self.total_prompt * 100 if self.total_prompt else 0
        return (
            f"\n── Stats ──────────────────────────────\n"
            f"  requests:   {self.requests}\n"
            f"  prompt:     {self.total_prompt} (hit {self.total_hit}, miss {self.total_miss}, {hit_rate:.1f}% hit)\n"
            f"  output:     {self.total_output} (reasoning {self.total_reasoning})\n"
            f"  cost:       ${self.total_cost:.6f}\n"
            f"──────────────────────────────────────"
        )

stats = Stats()

# ── API 调用 ──────────────────────────────────────────

def chat(messages: list, tools: list = None, tool_choice: str = "auto") -> dict:
    body = {"model": MODEL, "messages": messages, "max_tokens": 4096}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice

    data = json.dumps(body).encode()
    # 去掉 /anthropic 后缀，使用 OpenAI 兼容端点
    url = BASE_URL.rstrip("/")
    if url.endswith("/anthropic"):
        url = url[: -len("/anthropic")]
    url += "/chat/completions"

    req = Request(url, data=data, headers={
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    })

    try:
        with urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
    except HTTPError as e:
        err_body = e.read().decode()
        print(f"\n[API Error {e.code}] {err_body}")
        sys.exit(1)

    usage = result.get("usage", {})
    stats.record(usage)

    hit = usage.get("prompt_cache_hit_tokens", 0)
    miss = usage.get("prompt_cache_miss_tokens", 0)
    out = usage.get("completion_tokens", 0)
    print(f"  [{miss} miss + {hit} hit → {out} out]", end="")

    return result

# ── 工具定义 ──────────────────────────────────────────

BASH_TOOL = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Execute a shell command and return stdout+stderr.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute."
                }
            },
            "required": ["command"]
        }
    }
}

def execute_bash(command: str) -> str:
    print(f"\n  $ {command}")
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30,
            cwd=os.path.join(os.path.dirname(__file__), "..")
        )
        output = result.stdout + result.stderr
        if not output.strip():
            output = "(no output)"
        # 截断过长输出
        if len(output) > 4000:
            output = output[:2000] + f"\n... [{len(output)} chars truncated] ...\n" + output[-1000:]
        return output
    except subprocess.TimeoutExpired:
        return "(command timed out after 30s)"
    except Exception as e:
        return f"(error: {e})"

TOOLS_MAP = {"bash": execute_bash}

# ── Agent Loop ────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are a helpful assistant with access to bash. "
    "Use the bash tool to run commands when needed. "
    "Be concise."
)

def agent_loop(user_input: str, messages: list):
    messages.append({"role": "user", "content": user_input})

    while True:
        print("  ", end="", flush=True)
        result = chat(messages, tools=[BASH_TOOL])
        choice = result["choices"][0]
        message = choice["message"]
        finish = choice["finish_reason"]

        if finish == "tool_calls" and message.get("tool_calls"):
            messages.append(message)
            for tc in message["tool_calls"]:
                fn_name = tc["function"]["name"]
                args = json.loads(tc["function"]["arguments"])
                tool_output = TOOLS_MAP.get(fn_name, lambda _: "unknown tool")(args.get("command", ""))
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_output,
                })
            continue

        # 最终回复
        content = message.get("content", "")
        if message.get("reasoning_content"):
            print(f"\n  [reasoning] {message['reasoning_content'][:200]}...")
        print(f"\n{content}")
        messages.append(message)
        break

# ── 入口 ──────────────────────────────────────────────

def main():
    if not API_KEY:
        print("Error: DEEPSEEK_API_KEY not set. Check .env file.")
        sys.exit(1)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    print(f"tiny-agent | model={MODEL} | /stats /clear /quit")

    # 单次模式
    if len(sys.argv) > 1:
        user_input = " ".join(sys.argv[1:])
        agent_loop(user_input, messages)
        print(stats.summary())
        return

    # 交互模式
    while True:
        try:
            user_input = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(stats.summary())
            break

        if not user_input:
            continue
        if user_input == "/quit":
            print(stats.summary())
            break
        if user_input == "/stats":
            print(stats.summary())
            continue
        if user_input == "/clear":
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            stats.__init__()
            print("(cleared)")
            continue

        agent_loop(user_input, messages)

if __name__ == "__main__":
    main()
