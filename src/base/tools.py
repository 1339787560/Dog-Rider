"""基础工具注册表 - 可扩展工具系统"""
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .config import PROJECT_ROOT


@dataclass
class ToolDef:
    """工具定义"""
    name: str
    description: str
    parameters: Dict[str, Any]
    handler: Callable


class BaseToolRegistry:
    """基础工具注册表 - 可扩展"""

    def __init__(self):
        self._tools: List[dict] = []
        self._handlers: Dict[str, Callable] = {}

    def register(self, name: str, description: str, parameters: dict, handler: Callable):
        """注册一个工具"""
        self._tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            }
        })
        self._handlers[name] = handler

    def get_tool_defs(self) -> List[dict]:
        """获取所有工具定义（API 格式）"""
        return self._tools.copy()

    def get_handler(self, name: str) -> Optional[Callable]:
        """获取工具处理函数"""
        return self._handlers.get(name)

    def execute(self, name: str, **kwargs) -> str:
        """执行工具"""
        handler = self._handlers.get(name)
        if not handler:
            return f"(error: unknown tool '{name}')"
        try:
            return handler(**kwargs)
        except Exception as e:
            return f"(error executing '{name}': {e})"


# ========== 基础工具实现 ==========

def bash_command(command: str, cwd: Path = None) -> str:
    """执行 shell 命令"""
    actual_cwd = cwd or PROJECT_ROOT
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30,
            cwd=actual_cwd,
        )
        output = result.stdout + result.stderr
        if not output.strip():
            return "(no output)"
        if len(output) > 4000:
            output = output[:2000] + f"\n... [{len(output)} chars truncated] ...\n" + output[-1000:]
        return output
    except subprocess.TimeoutExpired:
        return "(command timed out after 30s)"
    except Exception as e:
        return f"(error: {e})"


def read_file(filepath: str) -> str:
    """读取文件内容"""
    try:
        path = PROJECT_ROOT / filepath
        if not path.exists():
            return f"(error: file not found: {filepath})"
        content = path.read_text(encoding="utf-8", errors="replace")
        if len(content) > 8000:
            content = content[:4000] + f"\n... [{len(content)} chars truncated] ...\n" + content[-1000:]
        return content
    except Exception as e:
        return f"(error reading file: {e})"


def write_file(filepath: str, content: str) -> str:
    """写入文件内容"""
    try:
        path = PROJECT_ROOT / filepath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"(success: wrote {len(content)} chars to {filepath})"
    except Exception as e:
        return f"(error writing file: {e})"


# 默认基础工具集
def create_default_tools() -> BaseToolRegistry:
    """创建默认基础工具集"""
    registry = BaseToolRegistry()

    registry.register(
        name="bash",
        description="Execute a shell command and return stdout+stderr.",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute."}
            },
            "required": ["command"]
        },
        handler=bash_command,
    )

    registry.register(
        name="read_file",
        description="Read content from a file.",
        parameters={
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "Path to the file."}
            },
            "required": ["filepath"]
        },
        handler=read_file,
    )

    registry.register(
        name="write_file",
        description="Write content to a file. Creates or overwrites.",
        parameters={
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "Path to the file."},
                "content": {"type": "string", "description": "Content to write."},
            },
            "required": ["filepath", "content"]
        },
        handler=write_file,
    )

    return registry
