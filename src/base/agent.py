"""基础 Agent 主循环 - 最小实现，无 Dog-Rider 扩展"""
import json
import signal
import sys
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import BaseConfig
from .context import BaseContext
from .tools import BaseToolRegistry
from .persistence import (
    SessionManager, SessionState, SerialTrigger, ParallelTrigger,
    DEFAULT_CACHE_DIR,
)


@dataclass
class UsageStats:
    """API 调用统计"""
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    requests: int = 0


class BaseAgentLoop:
    """基础 Agent 主循环 - 纯工具调用 + 聊天

    支持持久化（WAL + checkpoint）：
    - 每条消息追加到 WAL
    - 每 60s/20req 触发 checkpoint（串行）
    - SIGTERM/SIGINT 优雅关闭
    - resume(session_id) 从 checkpoint + WAL 恢复
    """

    def __init__(
        self,
        config: BaseConfig,
        tools: BaseToolRegistry,
        system_prompt: str = "You are a helpful assistant.",
        session_id: Optional[str] = None,
        enable_persistence: bool = True,
    ):
        self.config = config
        self.tools = tools
        self.context = BaseContext()
        self.context.init_with_system(system_prompt)
        self.stats = UsageStats()
        self._created_at = datetime.now().isoformat()

        # 持久化
        self.session_manager: Optional[SessionManager] = None
        if enable_persistence:
            self._init_persistence(session_id)

    def _init_persistence(self, session_id: Optional[str]):
        """初始化持久化层"""
        # 选择 trigger（默认 serial）
        cache_dir = self._get_cache_dir()
        trigger = SerialTrigger(
            interval_sec=self._get_persistence_attr("checkpoint_interval_sec", 60),
            request_threshold=self._get_persistence_attr("checkpoint_request_threshold", 20),
        )
        self.session_manager = SessionManager(
            session_id=session_id,
            cache_dir=cache_dir,
            trigger=trigger,
            wal_fsync=self._get_persistence_attr("wal_fsync", True),
        )
        # 注册信号处理
        try:
            signal.signal(signal.SIGTERM, self._signal_handler)
            signal.signal(signal.SIGINT, self._signal_handler)
        except (ValueError, OSError):
            # 非主线程或不支持的平台
            pass

    def _get_cache_dir(self) -> Path:
        """获取缓存目录（兼容 BaseConfig 没有 persistence 属性的场景）"""
        if hasattr(self.config, "persistence") and getattr(self.config.persistence, "cache_dir", None):
            cache_dir_str = self.config.persistence.cache_dir
            return Path(cache_dir_str) if Path(cache_dir_str).is_absolute() else (Path.cwd() / cache_dir_str)
        return DEFAULT_CACHE_DIR

    def _get_persistence_attr(self, name: str, default):
        """安全读取 persistence 配置"""
        if hasattr(self.config, "persistence"):
            return getattr(self.config.persistence, name, default)
        return default

    def _signal_handler(self, signum, frame):
        """优雅关闭"""
        print(f"\n[Agent] Received signal {signum}, flushing checkpoint...", file=sys.stderr)
        self._save_checkpoint(force=True)
        if self.session_manager:
            self.session_manager.close()
        sys.exit(0)

    def _build_state(self) -> SessionState:
        """构建当前 SessionState"""
        return SessionState(
            session_id=self.session_manager.session_id if self.session_manager else "no-session",
            created_at=self._created_at,
            updated_at=datetime.now().isoformat(),
            model=self.config.model.model,
            messages=list(self.context.messages),
            stats=asdict(self.stats),
        )

    def _save_checkpoint(self, force: bool = False) -> bool:
        """触发 checkpoint"""
        if not self.session_manager:
            return False
        state = self._build_state()
        return self.session_manager.maybe_checkpoint(state, force=force)

    def _wal_message(self, message: dict):
        """记录消息到 WAL"""
        if self.session_manager:
            self.session_manager.append_wal("message", message=message)

    def resume(self, session_id: str) -> bool:
        """从已有 session 恢复

        Returns:
            True 如果恢复成功，False 如果 session 不存在
        """
        if not self.session_manager:
            self._init_persistence(session_id)
        else:
            # 重新初始化指向目标 session_id
            self.session_manager.close()
            cache_dir = self._get_cache_dir()
            self.session_manager = SessionManager(
                session_id=session_id,
                cache_dir=cache_dir,
                trigger=SerialTrigger(
                    interval_sec=self._get_persistence_attr("checkpoint_interval_sec", 60),
                    request_threshold=self._get_persistence_attr("checkpoint_request_threshold", 20),
                ),
                wal_fsync=self._get_persistence_attr("wal_fsync", True),
            )

        state = self.session_manager.resume()
        if state is None:
            return False

        self.context.messages = list(state.messages)
        self._created_at = state.created_at
        # 恢复 stats
        for k, v in state.stats.items():
            if hasattr(self.stats, k):
                setattr(self.stats, k, v)
        return True


    def _call_api(self, messages: List[dict]) -> dict:
        """调用 LLM API — 自动重试 + 指数退避"""
        # 并行模式且开启冻结时，强制 temperature=0 保证确定性
        temp = 0.0 if (hasattr(self.config, 'discard')
                       and self.config.discard.merge_mode == "parallel"
                       and getattr(self.config.discard, 'isFrozenForParallel', True)) else self.config.model.temperature
        body = {
            "model": self.config.model.model,
            "messages": messages,
            "max_tokens": self.config.model.max_tokens,
            "temperature": temp,
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
        - 连续 N 次失败直接放弃任务（快速响应）
        - 每步 try/except 防止单点崩溃
        - 工具参数解析失败 → 注入错误消息，继续
        - API 响应结构异常 → 注入错误消息，继续
        - 持久化：每条消息追加到 WAL，按 trigger 触发 checkpoint
        """
        user_msg = {"role": "user", "content": user_input}
        self.context.append(user_msg)
        self._wal_message(user_msg)

        consecutive_failures = 0
        max_failures = getattr(self.config, "max_consecutive_failures", 2)

        for turn in range(self.config.max_turns):
            # ── API 调用 ──
            try:
                result = self._call_api(self.context.messages)
                consecutive_failures = 0  # 成功后重置计数器
            except Exception as e:
                consecutive_failures += 1
                err_text = f"API call failed after {self.config.max_retries} retries: {e}"
                if consecutive_failures >= max_failures:
                    # 快速失败：连续多次错误直接退出（红色加粗）
                    RED = "\033[1;31m"
                    RESET = "\033[0m"
                    print(f"\n{RED}[Agent] Aborted: {consecutive_failures} consecutive API failures{RESET}",
                          file=sys.stderr)
                    print(f"{RED}{err_text}{RESET}", file=sys.stderr)
                    # 不保存 checkpoint（空任务，不污染状态）
                    return f"{RED}(Task aborted: {err_text}){RESET}"
                # 注入错误消息让 LLM 感知，继续循环
                err_msg = {
                    "role": "system",
                    "content": f"[ERROR] {err_text}. "
                               f"Please continue with what you know, or try a different approach.",
                }
                self.context.append(err_msg)
                self._wal_message(err_msg)
                continue

            # ── 解析响应 ──
            try:
                choice = result["choices"][0]
                message = choice["message"]
                finish_reason = choice.get("finish_reason")
            except (KeyError, IndexError, TypeError) as e:
                err_msg = {
                    "role": "system",
                    "content": f"[ERROR] Unexpected API response structure: {e}. "
                               f"Please continue with what you know.",
                }
                self.context.append(err_msg)
                self._wal_message(err_msg)
                continue

            # ── 工具调用轮次 ──
            if finish_reason == "tool_calls" and message.get("tool_calls"):
                self.context.append(message)
                self._wal_message(message)
                for tc in message["tool_calls"]:
                    fn_name = tc["function"]["name"]
                    fn_id = tc.get("id", f"call_{turn}")

                    # 解析参数 — 失败不崩溃
                    try:
                        args = json.loads(tc["function"]["arguments"])
                    except (json.JSONDecodeError, KeyError, TypeError) as e:
                        tool_output = f"(error: failed to parse arguments for '{fn_name}': {e})"
                        tool_msg = {
                            "role": "tool",
                            "tool_call_id": fn_id,
                            "content": tool_output,
                        }
                        self.context.append(tool_msg)
                        self._wal_message(tool_msg)
                        continue

                    # 执行工具
                    try:
                        tool_output = self.tools.execute(fn_name, **args)
                    except Exception as e:
                        tool_output = f"(error executing '{fn_name}': {e})"

                    tool_msg = {
                        "role": "tool",
                        "tool_call_id": fn_id,
                        "content": tool_output,
                    }
                    self.context.append(tool_msg)
                    self._wal_message(tool_msg)

                # 每完成一轮工具调用，尝试 checkpoint
                self._save_checkpoint(force=False)
                continue

            # ── 最终回复 ──
            content = message.get("content", "")
            self.context.append(message)
            self._wal_message(message)
            # 任务结束 → 强制 checkpoint
            self._save_checkpoint(force=True)
            return content

        # 达到 max_turns 上限 — 注入错误，给 LLM 最后回应机会
        max_msg = {
            "role": "system",
            "content": f"[ERROR] Reached max_turns={self.config.max_turns}. "
                       f"Please provide your final answer now based on what you have so far.",
        }
        self.context.append(max_msg)
        self._wal_message(max_msg)
        try:
            result = self._call_api(self.context.messages)
            final_msg = result["choices"][0]["message"]
            content = final_msg.get("content", "")
            self.context.append(final_msg)
            self._wal_message(final_msg)
            self._save_checkpoint(force=True)
            return content
        except Exception:
            self._save_checkpoint(force=True)
            return f"(Agent loop stopped: reached max_turns={self.config.max_turns})"

    def reset(self):
        """重置对话，保留 system prompt"""
        self.context.clear_natural()

    def close(self):
        """关闭 agent，flush 持久化资源"""
        if self.session_manager:
            self._save_checkpoint(force=True)
            self.session_manager.close()
