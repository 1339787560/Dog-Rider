"""Dog-Rider Agent - 基于 BaseAgentLoop 的扩展实现

扩展功能：
- 三区上下文模型（永久冻结/暂时冻结/自然增长）
- 任务级价值判定与丢弃策略
- 缓存命中率追踪与优化
- Claude Core Toolkit 完整工具集
"""
import atexit
import json
import sys
import threading
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
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
    """扩展统计 - 增加缓存和丢弃相关指标 - 线程安全"""
    total_hit: int = 0
    total_miss: int = 0
    total_reasoning: int = 0
    total_cost: float = 0.0
    discarded_tasks: int = 0
    tokens_saved: int = 0
    tasks_completed: int = 0
    tasks_kept: int = 0

    def __post_init__(self):
        self._lock = threading.Lock()

    def incr_requests(self, prompt: int, completion: int, hit: int, miss: int, reasoning: int, cost: float):
        """原子增加请求统计"""
        with self._lock:
            self.total_prompt_tokens += prompt
            self.total_completion_tokens += completion
            self.total_hit += hit
            self.total_miss += miss
            self.total_reasoning += reasoning
            self.total_cost += cost
            self.requests += 1

    def incr_task(self, kept: bool, saved: int = 0):
        """原子增加任务统计"""
        with self._lock:
            self.tasks_completed += 1
            if kept:
                self.tasks_kept += 1
            else:
                self.discarded_tasks += 1
                self.tokens_saved += saved


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
        self._pending_discard_hint: str = ""  # 下次任务注入的低价值标记
        self.session_id = str(uuid.uuid4())[:8]  # 会话ID用于持久化
        self._created_at = datetime.now().isoformat()
        self.silent = False  # True=不打印任务过程，仅统计
        # 注册退出时持久化
        atexit.register(self._persist_session)

    def _call_api(self, messages: List[dict]) -> dict:
        """调用 DeepSeek API - 扩展缓存统计"""
        # 并行模式且开启冻结时，强制 temperature=0 保证确定性
        temp = 0.0 if (self.config.discard.merge_mode == "parallel"
                       and self.config.discard.isFrozenForParallel) else self.config.model.temperature
        body = {
            "model": self.config.model.model,
            "messages": messages,
            "max_tokens": self.config.model.max_tokens,
            "temperature": temp,
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

        # 扩展统计：缓存命中 + 成本（线程安全）
        usage = result.get("usage", {})
        p = usage.get("prompt_tokens", 0)
        h = usage.get("prompt_cache_hit_tokens", 0)
        m = usage.get("prompt_cache_miss_tokens", p)
        o = usage.get("completion_tokens", 0)
        r = usage.get("completion_tokens_details", {}).get("reasoning_tokens", 0)

        price = PRICING.get(self.config.model.model, PRICING["deepseek-v4-flash"])
        cost = (m * price.miss + h * price.hit + o * price.output) / 1_000_000
        self.stats.incr_requests(p, o, h, m, r, cost)

        return result

    def _record_subrequest(self, role: str, usage: dict, label: str = "",
                           msg_start: int = -1, msg_end: int = -1):
        """记录子请求 — 含消息范围以便后续提取"""
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
            msg_start=msg_start,
            msg_end=msg_end,
        )
        self.current_task_requests.append(req)

    def run_task(self, user_input: str) -> str:
        """执行任务 — 健壮循环，在独立快照上运行，结束后选择性合并。

        snapshot → append user → agent loop → verdict → merge/discard
        """
        self.current_task_requests = []

        # 注入上次丢弃标记（如果有）
        if self._pending_discard_hint:
            user_input = f"{self._pending_discard_hint}\n\n---\n\n{user_input}"
            self._pending_discard_hint = ""

        # 创建隔离工作副本
        working = self.context.snapshot()
        working.append({"role": "user", "content": user_input})

        for turn in range(1, self.config.max_turns + 1):
            # ── API 调用 ──
            if not self.silent:
                print(f"  [T{turn}] Calling API...", end="", flush=True)
            try:
                result = self._call_api(working.messages)
            except Exception as e:
                # 注入错误消息让 LLM 感知，继续循环
                working.append({
                    "role": "system",
                    "content": f"[ERROR] API call failed after {self.config.max_retries} retries: {e}. "
                               f"Please continue with what you know, or try a different approach.",
                })
                continue

            # ── 解析响应 ──
            try:
                choice = result["choices"][0]
                message = choice["message"]
                finish = choice["finish_reason"]
            except (KeyError, IndexError, TypeError) as e:
                working.append({
                    "role": "system",
                    "content": f"[ERROR] Unexpected API response structure: {e}. "
                               f"Please continue with what you know.",
                })
                continue

            usage = result.get("usage", {})
            h = usage.get("prompt_cache_hit_tokens", 0)
            m = usage.get("prompt_cache_miss_tokens", usage.get("prompt_tokens", 0))
            hit_rate = h / (h + m) * 100 if (h + m) > 0 else 0

            # 命中可视化
            hit_bar = "█" * min(int(hit_rate // 10), 10)
            miss_bar = "░" * (10 - len(hit_bar))
            if not self.silent:
                print(f" hit={h:4d} miss={m:4d} [{hit_bar}{miss_bar}] {hit_rate:5.1f}%")

            if finish == "tool_calls" and message.get("tool_calls"):
                # 工具调用轮次 — 记录消息范围
                msg_start = len(working.messages)
                tool_names = [tc["function"]["name"] for tc in message["tool_calls"]]
                role = detect_role_from_tool_name(tool_names[0]) if tool_names else "analyze"

                working.append(message)

                # 执行所有工具调用
                for tc in message["tool_calls"]:
                    fn_name = tc["function"]["name"]
                    fn_id = tc.get("id", f"call_{turn}")

                    # 解析参数 — 失败不崩溃
                    try:
                        args = json.loads(tc["function"]["arguments"])
                    except (json.JSONDecodeError, KeyError, TypeError) as e:
                        tool_output = f"(error: failed to parse arguments for '{fn_name}': {e})"
                        working.append({
                            "role": "tool",
                            "tool_call_id": fn_id,
                            "content": tool_output,
                        })
                        continue

                    handler = get_tool_handler(fn_name)
                    if not self.silent:
                        print(f"\n  $ {fn_name}({', '.join(f'{k}={repr(v)[:40]}' for k, v in args.items())})",
                              end="", flush=True)

                    if handler:
                        try:
                            tool_output = handler(**args)
                        except Exception as e:
                            tool_output = f"(tool error: {e})"
                    else:
                        tool_output = f"(unknown tool: {fn_name})"

                    working.append({
                        "role": "tool",
                        "tool_call_id": fn_id,
                        "content": tool_output,
                    })
                msg_end = len(working.messages)
                self._record_subrequest(role, usage, f"tool: {', '.join(tool_names)}",
                                       msg_start=msg_start, msg_end=msg_end)
                continue

            # ── 最终回复 ──
            content = message.get("content", "")
            msg_start = len(working.messages)
            working.append(message)
            self._record_subrequest("output", usage, "final reply",
                                   msg_start=msg_start, msg_end=len(working.messages))

            self._handle_task_end(user_input, working)

            if self.config.verbose:
                print(f"\n  [usage] hit={usage.get('prompt_cache_hit_tokens', 0)} "
                      f"miss={usage.get('prompt_cache_miss_tokens', 0)} "
                      f"out={usage.get('completion_tokens', 0)}")

            return content

        # 达到 max_turns 上限 — 注入错误，给 LLM 最后回应机会
        working.append({
            "role": "system",
            "content": f"[ERROR] Reached max_turns={self.config.max_turns}. "
                       f"Please provide your final answer now based on what you have so far.",
        })
        try:
            result = self._call_api(working.messages)
            content = result["choices"][0]["message"].get("content", "")
            working.append(result["choices"][0]["message"])
        except Exception:
            content = f"(Task stopped: reached max_turns={self.config.max_turns})"
        self._handle_task_end(user_input, working)
        return content

    def _handle_task_end(self, task_description: str, working: ContextManager):
        """任务结束：价值判定 + 合并决策。

        KEEP    → working 替换 self.context
        DISCARD → working 丢弃，self.context 不变
        PARTIAL → 提取高价值消息合并入 self.context
        """
        analysis = working.analyze()
        context_total = analysis.total_tokens

        verdict = self.task_judge.assess(
            self.current_task_requests,
            context_total,
            task_description,
        )
        self.last_verdict = verdict
        will_discard = verdict.is_low_value and self.config.discard.auto_discard
        discard_label = "✓ DISCARD" if will_discard else "- KEEP"

        # ── 任务命中趋势分析 ──
        if self.current_task_requests and not self.silent:
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

        if not self.silent:
            print(f"  {self.task_judge.explain(verdict)}")

        # ── 上下文用量 ──
        if not self.silent:
            print(f"\n  ┌{'─' * 56}┐")
            print(f"  │  Context Usage{' ' * 46}│")
            print(f"  ├{'─' * 56}┤")
            print(f"  │  Permanent:  {analysis.permanent.tokens:5d} tokens  ({analysis.permanent.message_count:2d} msgs){' ' * 22}│")
            print(f"  │  Temporary:  {analysis.temporary.tokens:5d} tokens  ({analysis.temporary.message_count:2d} msgs){' ' * 22}│")
            print(f"  │  Natural:    {analysis.natural.tokens:5d} tokens  ({analysis.natural.message_count:2d} msgs){' ' * 22}│")
            print(f"  ├{'─' * 56}┤")
            print(f"  │  Total:      {analysis.total_tokens:5d} tokens{' ' * 36}│")
            print(f"  │  Hit Rate:   {analysis.hit_rate:5.1f}%{' ' * 40}│")
            print(f"  │  Est. Cost:  ${analysis.cost_estimate_with_cache:.6f}{' ' * 33}│")
            print(f"  └{'─' * 56}┘")

        # ── 合并决策 ──
        if not verdict.is_low_value:
            # KEEP: 根据 merge_on_keep 决定是否修改共享上下文
            if self.config.discard.merge_on_keep:
                if self.config.discard.merge_mode == "parallel":
                    n = self.context.merge_parallel(working)
                    if self.config.verbose and not self.silent:
                        print(f"  [Parallel merge] Appended {n} messages")
                else:
                    self.context.replace_with(working)
            self.stats.incr_task(kept=True)
            return

        if not self.config.discard.auto_discard:
            # dry-run 模式: 保留 working
            if self.config.discard.merge_on_keep:
                if self.config.discard.merge_mode == "parallel":
                    self.context.merge_parallel(working)
                else:
                    self.context.replace_with(working)
            self.stats.incr_task(kept=True)
            return

        # DISCARD: 仅存标记，不修改上下文（保持前缀完整）
        working_natural_tokens = sum(
            self.tok.count(m.get("content", ""))
            for m in working.get_zone_messages("natural")
        )

        # 生成低价值标记：告知模型此段已丢弃，无需重建
        hint = (
            f"[DISCARDED CONTEXT — low value, do NOT re-fetch or rebuild]\n"
            f"  Task: {task_description}\n"
            f"  Reason: {verdict.reason}\n"
            f"  The content above was discarded. You already know it. Continue normally."
        )
        self._pending_discard_hint = hint

        self.stats.incr_task(kept=False, saved=working_natural_tokens)
        if not self.silent:
            print(f"  Discard effect: saved {working_natural_tokens} tokens (context unchanged, hint queued)")

    def _extract_messages_for_requests(self, working: ContextManager,
                                       requests: List[SubRequest]) -> List[dict]:
        """从工作副本中提取高价值子请求对应的消息。

        使用 SubRequest.msg_start/msg_end 定位消息范围，
        去重合并重叠区间，按索引顺序返回。
        """
        seen = set()
        extracted = []
        for req in requests:
            start = req.msg_start
            end = req.msg_end
            if start < 0 or end <= start:
                continue
            for i in range(start, min(end, len(working.messages))):
                if i not in seen:
                    extracted.append(dict(working.messages[i]))
                    seen.add(i)
        return extracted

    def print_stats(self):
        """打印统计信息"""
        hit_rate = self.stats.total_hit / self.stats.total_prompt_tokens * 100 if self.stats.total_prompt_tokens else 0
        print(f"\n{'=' * 60}")
        print(f"Stats  (session: {self.session_id})")
        print(f"{'=' * 60}")
        print(f"  requests:   {self.stats.requests}")
        print(f"  prompt:     {self.stats.total_prompt_tokens} (hit {self.stats.total_hit}, miss {self.stats.total_miss}, {hit_rate:.1f}% hit)")
        print(f"  output:     {self.stats.total_completion_tokens} (reasoning {self.stats.total_reasoning})")
        print(f"  cost:       ${self.stats.total_cost:.6f}")
        if self.stats.discarded_tasks > 0:
            print(f"  discarded:  {self.stats.discarded_tasks} tasks, saved {self.stats.tokens_saved} tokens")
        print(f"{'=' * 60}")

    def _persist_session(self):
        """程序退出时持久化会话统计到文件"""
        try:
            sessions_dir = Path.home() / ".dogrider" / "sessions"
            sessions_dir.mkdir(parents=True, exist_ok=True)
            filepath = sessions_dir / f"{self.session_id}.json"
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump({
                    "session_id": self.session_id,
                    "created_at": self._created_at,
                    "closed_at": datetime.now().isoformat(),
                    "model": self.config.model.model,
                    "merge_mode": self.config.discard.merge_mode,
                    **asdict(self.stats),
                }, f, indent=2, ensure_ascii=False)
        except Exception:
            pass  # 持久化失败不中断程序
