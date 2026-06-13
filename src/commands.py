"""交互式 Slash 命令系统

提供可扩展的 / 命令注册和分发机制。

使用：
    registry = CommandRegistry()
    registry.register("help", show_help, "Show available commands")

    # 在主循环中
    if user_input.startswith("/"):
        handled, response = registry.dispatch(user_input, agent)
        if handled:
            continue
"""
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple


@dataclass
class CommandResult:
    """命令执行结果"""
    handled: bool          # 是否成功处理
    should_continue: bool  # True=继续主循环，False=退出
    output: str = ""       # 给用户看的输出


@dataclass
class CommandSpec:
    """命令规范"""
    name: str              # 不含 "/" 前缀
    handler: Callable      # 签名: (agent, args: List[str]) -> CommandResult
    description: str
    usage: str = ""        # 用法示例


class CommandRegistry:
    """斜杠命令注册表"""

    def __init__(self):
        self._commands: Dict[str, CommandSpec] = {}
        self._register_builtins()

    def register(self, name: str, handler: Callable, description: str, usage: str = ""):
        """注册一个命令

        Args:
            name: 命令名（不含 /，例如 "help"）
            handler: 处理函数 (agent, args) -> CommandResult
            description: 一行说明
            usage: 用法示例
        """
        self._commands[name] = CommandSpec(name, handler, description, usage)

    def dispatch(self, user_input: str, agent) -> CommandResult:
        """分发命令

        Returns:
            CommandResult，handled=False 表示不是命令（仍由主流程当作普通输入处理）
        """
        if not user_input.startswith("/"):
            return CommandResult(handled=False, should_continue=True)

        parts = user_input[1:].split()
        if not parts:
            return CommandResult(handled=True, should_continue=True, output="")

        name = parts[0]
        args = parts[1:]

        spec = self._commands.get(name)
        if not spec:
            return CommandResult(
                handled=True,
                should_continue=True,
                output=f"Unknown command: /{name}. Type /help to see all commands.",
            )

        try:
            return spec.handler(agent, args)
        except Exception as e:
            return CommandResult(
                handled=True,
                should_continue=True,
                output=f"Error executing /{name}: {e}",
            )

    def list_commands(self) -> List[CommandSpec]:
        """列出所有命令"""
        return sorted(self._commands.values(), key=lambda c: c.name)

    # ========== 内置命令 ==========

    def _register_builtins(self):
        self.register("help", self._cmd_help, "Show available commands", "/help")
        self.register("quit", self._cmd_quit, "Exit the agent", "/quit")
        self.register("stats", self._cmd_stats, "Show usage statistics", "/stats")
        self.register("context", self._cmd_context, "Show context report", "/context")
        self.register("sessions", self._cmd_sessions, "List recoverable sessions", "/sessions")
        self.register("resume", self._cmd_resume, "Resume a session", "/resume <session_id>")
        self.register("session", self._cmd_session, "Show current session info", "/session")
        self.register("reset", self._cmd_reset, "Reset context (keep system prompt)", "/reset")

    def _cmd_help(self, agent, args) -> CommandResult:
        lines = ["Available commands:"]
        for spec in self.list_commands():
            usage = spec.usage or f"/{spec.name}"
            lines.append(f"  {usage:<32} — {spec.description}")
        return CommandResult(True, True, "\n".join(lines))

    def _cmd_quit(self, agent, args) -> CommandResult:
        agent.print_stats()
        return CommandResult(True, False, "Goodbye.")

    def _cmd_stats(self, agent, args) -> CommandResult:
        agent.print_stats()
        return CommandResult(True, True, "")

    def _cmd_context(self, agent, args) -> CommandResult:
        return CommandResult(True, True, agent.context.report())

    def _cmd_sessions(self, agent, args) -> CommandResult:
        from .base.persistence import SessionManager
        from pathlib import Path
        if not agent.session_manager:
            return CommandResult(True, True, "Persistence not enabled.")
        cache_dir = agent.session_manager.cache_dir.parent
        ids = SessionManager.list_sessions(cache_dir)
        if not ids:
            return CommandResult(True, True, "No saved sessions found.")
        lines = [f"Saved sessions ({len(ids)} total):"]
        for sid in ids:
            meta_path = cache_dir / sid / "meta.json"
            if meta_path.exists():
                import json
                try:
                    with open(meta_path, encoding="utf-8") as f:
                        meta = json.load(f)
                    marker = " [CURRENT]" if sid == agent.session_id else ""
                    lines.append(f"  {sid}{marker}  ({meta.get('updated_at', '?')})  {meta.get('model', '?')}")
                    if meta.get("last_error"):
                        lines.append(f"    last_error: {meta['last_error'][:80]}")
                except Exception:
                    lines.append(f"  {sid}  (corrupted meta)")
            else:
                lines.append(f"  {sid}  (no meta)")
        return CommandResult(True, True, "\n".join(lines))

    def _cmd_resume(self, agent, args) -> CommandResult:
        if not args:
            return CommandResult(True, True, "Usage: /resume <session_id>")
        if not agent.session_manager:
            return CommandResult(True, True, "Persistence not enabled.")

        target_id = args[0]
        cache_dir = agent.session_manager.cache_dir.parent
        if not (cache_dir / target_id).exists():
            return CommandResult(True, True, f"Session '{target_id}' not found. Try /sessions to list.")

        # 重建 session_manager 指向目标 session
        from .base.persistence import SessionManager, SerialTrigger, ParallelTrigger
        agent.session_manager.close()

        trigger = (
            ParallelTrigger() if agent.config.discard.merge_mode == "parallel"
            else SerialTrigger(
                interval_sec=agent.config.persistence.checkpoint_interval_sec,
                request_threshold=agent.config.persistence.checkpoint_request_threshold,
            )
        )
        agent.session_manager = SessionManager(
            session_id=target_id,
            cache_dir=cache_dir,
            trigger=trigger,
            wal_fsync=agent.config.persistence.wal_fsync,
        )

        state = agent.session_manager.resume()
        if state is None:
            return CommandResult(True, True, f"Failed to resume session '{target_id}'.")

        # 恢复到 agent
        agent.session_id = target_id
        agent.context.messages = list(state.messages)
        agent._created_at = state.created_at
        # 恢复 stats（不破坏 _lock）
        for k, v in state.stats.items():
            if hasattr(agent.stats, k) and not k.startswith("_"):
                try:
                    setattr(agent.stats, k, v)
                except Exception:
                    pass

        return CommandResult(
            True, True,
            f"Resumed session {target_id}. "
            f"Messages: {len(agent.context.messages)}, requests: {agent.stats.requests}",
        )

    def _cmd_session(self, agent, args) -> CommandResult:
        if not agent.session_manager:
            return CommandResult(True, True, "Persistence not enabled.")
        return CommandResult(
            True, True,
            f"Current session: {agent.session_id}\n"
            f"  Created at: {agent._created_at}\n"
            f"  Cache dir:  {agent.session_manager.cache_dir}\n"
            f"  Messages:   {len(agent.context.messages)}\n"
            f"  Requests:   {agent.stats.requests}",
        )

    def _cmd_reset(self, agent, args) -> CommandResult:
        # 保留 system prompt
        if agent.context.messages and agent.context.messages[0].get("role") == "system":
            agent.context.messages = agent.context.messages[:1]
        else:
            agent.context.messages = []
        return CommandResult(True, True, "Context reset (system prompt preserved).")
