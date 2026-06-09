"""Dog-Rider Agent - 基于 BaseAgentLoop 的扩展实现

扩展功能：
- 三区上下文模型（永久冻结/暂时冻结/自然增长）
- 任务级价值判定与丢弃策略
- 缓存命中率追踪与优化
- Claude Core Toolkit 完整工具集
"""
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .base.agent import BaseAgentLoop, UsageStats as BaseUsageStats
from .config import Config, PRICING
from .context import ContextManager
from .discard import SubRequest, TaskValueJudge, TaskVerdict
from .tokenizer import get_tokenizer
from .tools import ALL_TOOLS, get_tool_handler, detect_role_from_tool_name


@dataclass
class DogRiderUsageStats(BaseUsageStats):
    """扩展统计 - 增加缓存和丢弃相关指标"""
    total_hit: int = 0
    total_miss: int = 0
    total_reasoning: int = 0
    total_cost: float = 0.0
    discarded_tasks: int = 0
    tokens_saved: int = 0


def load_system_prompt() -> str:
    """加载系统提示词：SYS/SystemPrompt.md + SYS/Claude-Core-Toolkit.md + CLAUDE.md"""
    base_path = Path(__file__).parent
    project_root = base_path.parent

    files = [
        base_path / "SYS" / "SystemPrompt.md",        # Agent 行为基础规范
        base_path / "SYS" / "Claude-Core-Toolkit.md",  # 核心工具集使用指南
        project_root / "CLAUDE.md",                    # Dog-Rider 框架协议
    ]

    parts = []
    for f in files:
        if f.exists():
            # strip 末尾空白 + 统一换行 → token 级别完全一致
            content = f.read_text(encoding="utf-8").rstrip()
            parts.append(content)
        else:
            print(f"Warning: {f.name} not found", file=sys.stderr)

    return "\n\n---\n\n".join(parts).rstrip()


class AgentLoop(BaseAgentLoop):
    """Dog-Rider Agent - 集成任务级丢弃策略和缓存优化

    继承自 BaseAgentLoop，扩展：
    - 三区上下文管理
    - 任务级价值判定与自动丢弃
    - 缓存命中率可视化
    - 成本统计
    """

    SYSTEM_PROMPT = load_system_prompt()

    def __init__(self, config: Config):
        # 绕过父类初始化，完全自定义
        self.config = config
        self.tok = get_tokenizer()
        self.context = ContextManager(config)
        self.context.init_with_system(self.SYSTEM_PROMPT)
        self.stats = DogRiderUsageStats()
        self.task_judge = TaskValueJudge(config)
        self.current_task_requests: List[SubRequest] = []
        self.last_verdict: Optional[TaskVerdict] = None

    def _call_api(self, messages: List[dict]) -> dict:
        """调用 DeepSeek API - 扩展缓存统计"""
        body = {
            "model": self.config.model.model,
            "messages": messages,
            "max_tokens": self.config.model.max_tokens,
            "tools": ALL_TOOLS,
            "tool_choice": "auto",
        }

        url = self.config.model.base_url.rstrip("/") + "/chat/completions"
        data = json.dumps(body, sort_keys=True).encode()  # sort_keys → 确定性序列化 → 缓存命中稳定

        # 重用父类的 API 调用逻辑，但扩展统计
        import urllib.request
        import urllib.error
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

        # 扩展统计：缓存命中 + 成本
        usage = result.get("usage", {})
        p = usage.get("prompt_tokens", 0)
        h = usage.get("prompt_cache_hit_tokens", 0)
        m = usage.get("prompt_cache_miss_tokens", p)
        o = usage.get("completion_tokens", 0)
        r = usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0)

        self.stats.total_prompt_tokens += p
        self.stats.total_completion_tokens += o
        self.stats.total_hit += h
        self.stats.total_miss += m
        self.stats.total_reasoning += r
        self.stats.requests += 1

        price = PRICING.get(self.config.model.model, PRICING["deepseek-v4-flash"])
        cost = (m * price.miss + h * price.hit + o * price.output) / 1_000_000
        self.stats.total_cost += cost

        return result

    def _record_subrequest(self, role: str, usage: dict, label: str = ""):
        """记录子请求"""
        p = usage.get("prompt_tokens", 0)
        h = usage.get("prompt_cache_hit_tokens", 0)
        m = usage.get("prompt_cache_miss_tokens", p)
        o = usage.get("completion_tokens", 0)

        req = SubRequest(
            role=role,
            tokens=p,
            cache_hit=h,
            cache_miss=m,
            output_tokens=o,
            label=label,
        )
        self.current_task_requests.append(req)

    def run_task(self, user_input: str) -> str:
        """执行一个任务 (用户输入 → agent loop 自然结束)

        结束后自动触发价值判定和丢弃。
        """
        self.current_task_requests = []
        self.context.append({"role": "user", "content": user_input})

        start_natural = len(self.context.messages)

        turn = 0
        while True:
            turn += 1
            print(f"  [T{turn}] Calling API...", end="", flush=True)
            result = self._call_api(self.context.messages)
            choice = result["choices"][0]
            message = choice["message"]
            finish = choice["finish_reason"]

            usage = result.get("usage", {})
            h = usage.get("prompt_cache_hit_tokens", 0)
            m = usage.get("prompt_cache_miss_tokens", usage.get("prompt_tokens", 0))
            hit_rate = h / (h + m) * 100 if (h + m) > 0 else 0

            # 命中可视化
            hit_bar = "█" * min(int(hit_rate // 10), 10)
            miss_bar = "░" * (10 - len(hit_bar))
            print(f" hit={h:4d} miss={m:4d} [{hit_bar}{miss_bar}] {hit_rate:5.1f}%")

            if finish == "tool_calls" and message.get("tool_calls"):
                # 工具调用轮次
                tool_names = [tc["function"]["name"] for tc in message["tool_calls"]]
                role = detect_role_from_tool_name(tool_names[0]) if tool_names else "analyze"
                self._record_subrequest(role, usage, f"tool: {', '.join(tool_names)}")

                self.context.append(message)

                # 执行所有工具调用
                for tc in message["tool_calls"]:
                    fn_name = tc["function"]["name"]
                    args = json.loads(tc["function"]["arguments"])
                    handler = get_tool_handler(fn_name)

                    print(f"\n  $ {fn_name}({', '.join(f'{k}={repr(v)[:40]}' for k, v in args.items())})", end="", flush=True)

                    if handler:
                        tool_output = handler(**args)
                    else:
                        tool_output = f"(unknown tool: {fn_name})"

                    self.context.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": tool_output,
                    })
                continue

            # 最终回复
            content = message.get("content", "")
            self._record_subrequest("output", usage, "final reply")
            self.context.append(message)

            # 任务结束，触发价值判定
            self._handle_task_end(user_input, start_natural)

            if self.config.verbose:
                print(f"\n  [usage] hit={usage.get('prompt_cache_hit_tokens', 0)} "
                      f"miss={usage.get('prompt_cache_miss_tokens', 0)} "
                      f"out={usage.get('completion_tokens', 0)}")

            return content

    def _handle_task_end(self, task_description: str, start_natural: int):
        """任务结束处理：价值判定 + 丢弃执行 + 命中分析"""
        analysis = self.context.analyze()
        context_total = analysis.total_tokens

        # 提前判定价值（用于在趋势表中显示）
        verdict = self.task_judge.assess(
            self.current_task_requests,
            context_total,
            task_description,
        )
        self.last_verdict = verdict
        will_discard = verdict.is_low_value and self.config.discard.auto_discard
        discard_label = "✓ DISCARD" if will_discard else "- KEEP"

        # 任务命中趋势分析
        if self.current_task_requests:
            hits = [r.cache_hit for r in self.current_task_requests]
            misses = [r.cache_miss for r in self.current_task_requests]
            first_hit_rate = hits[0] / (hits[0] + misses[0]) * 100 if (hits[0] + misses[0]) > 0 else 0
            last_hit_rate = hits[-1] / (hits[-1] + misses[-1]) * 100 if (hits[-1] + misses[-1]) > 0 else 0
            avg_hit_rate = sum(hits) / (sum(hits) + sum(misses)) * 100 if (sum(hits) + sum(misses)) > 0 else 0

            print(f"\n  ┌{'─' * 66}┐")
            print(f"  │  Task Cache Hit Trend{' ' * 45}│")
            print(f"  ├{'─' * 66}┤")
            print(f"  │  Turn #   Hit   Miss  Hit Rate          Discard{' ' * 24}│")
            for i, (h, m) in enumerate(zip(hits, misses), 1):
                rate = h / (h + m) * 100 if (h + m) > 0 else 0
                bar = "█" * min(int(rate // 10), 10) + "░" * max(0, 10 - int(rate // 10))
                discard_col = discard_label if i == len(hits) else ""
                print(f"  │    {i:2d}    {h:4d}  {m:4d}  [{bar}] {rate:5.1f}%  {discard_col:<12}{' ' * 12}│")
            print(f"  ├{'─' * 66}┤")
            print(f"  │  Avg:     {sum(hits):4d}  {sum(misses):4d}  {' ' * 12}{avg_hit_rate:5.1f}%{' ' * 24}│")
            print(f"  │  ΔHit:    {last_hit_rate - first_hit_rate:+5.1f}%{' ' * 48}│")
            print(f"  └{'─' * 66}┘")

        print(f"  {self.task_judge.explain(verdict)}")

        if not verdict.is_low_value or not self.config.discard.auto_discard:
            return

        # 执行丢弃
        # 1. pop 自然增长区中属于当前任务的所有消息
        n_to_pop = len(self.context.messages) - start_natural
        if n_to_pop <= 0:
            return

        popped = self.context.pop_natural(n_to_pop)
        popped_tokens = sum(self.tok.count(m.get("content", "")) for m in popped)

        # 2. 追加提取的高价值内容 + 摘要
        if verdict.high_value_requests:
            extracted_content = "\n".join(
                f"[{r.role}] {r.label}"
                for r in verdict.high_value_requests
            )
            self.context.append({"role": "system", "content": f"[EXTRACTED]\n{extracted_content}"})

        if verdict.summary:
            self.context.append({"role": "system", "content": verdict.summary})

        # 统计节省
        new_tokens = sum(self.tok.count(m.get("content", "")) for m in self.context.get_zone_messages("natural"))
        saved = popped_tokens - new_tokens
        self.stats.discarded_tasks += 1
        self.stats.tokens_saved += saved

        print(f"  Discard effect: popped {n_to_pop} msgs ({popped_tokens}t) → saved {saved} tokens")

    def print_stats(self):
        """打印统计信息"""
        hit_rate = self.stats.total_hit / self.stats.total_prompt * 100 if self.stats.total_prompt else 0
        print(f"\n{'=' * 60}")
        print("Stats")
        print(f"{'=' * 60}")
        print(f"  requests:   {self.stats.requests}")
        print(f"  prompt:     {self.stats.total_prompt} (hit {self.stats.total_hit}, miss {self.stats.total_miss}, {hit_rate:.1f}% hit)")
        print(f"  output:     {self.stats.total_output} (reasoning {self.stats.total_reasoning})")
        print(f"  cost:       ${self.stats.total_cost:.6f}")
        if self.stats.discarded_tasks > 0:
            print(f"  discarded:  {self.stats.discarded_tasks} tasks, saved {self.stats.tokens_saved} tokens")
        print(f"{'=' * 60}")
