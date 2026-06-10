"""Dog-Rider 扩展工具集 - 基于 BaseToolRegistry 扩展"""
import fnmatch
import json
import os
import re
from pathlib import Path
from typing import Callable, Dict, Optional

from .base.tools import BaseToolRegistry, bash_command as base_bash, read_file as base_read, write_file as base_write
from .config import PROJECT_ROOT


def read_file(filepath: str, offset: int = 0, limit: int = 0) -> str:
    """扩展读取文件 - 支持 offset/limit 分页"""
    if offset == 0 and limit == 0:
        return base_read(filepath)

    try:
        path = PROJECT_ROOT / filepath
        if not path.exists():
            return f"(error: file not found: {filepath})"
        content = path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        start = max(0, offset - 1)
        end = start + limit if limit > 0 else len(lines)
        result = "\n".join(lines[start:end])
        result += f"\n[lines {start+1}-{end} of {len(lines)}]"
        if len(result) > 8000:
            result = result[:4000] + f"\n... [{len(result)} chars truncated] ...\n" + result[-1000:]
        return result
    except Exception as e:
        return f"(error reading file: {e})"


def edit_file(filepath: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """精确替换文件内容"""
    try:
        path = PROJECT_ROOT / filepath
        if not path.exists():
            return f"(error: file not found: {filepath})"
        content = path.read_text(encoding="utf-8")
        if old_string not in content:
            return f"(error: old_string not found in file)"
        if not replace_all and content.count(old_string) > 1:
            return f"(error: old_string appears {content.count(old_string)} times, need unique match)"

        count = content.count(old_string)
        content = content.replace(old_string, new_string, -1 if replace_all else 1)
        path.write_text(content, encoding="utf-8")
        return f"(success: replaced {count} occurrence(s) in {filepath})"
    except Exception as e:
        return f"(error editing file: {e})"


def glob(pattern: str, path: str = "") -> str:
    """文件模式匹配搜索"""
    try:
        root = PROJECT_ROOT / path if path else PROJECT_ROOT
        matches = []
        for dirpath, _, filenames in os.walk(root):
            for filename in filenames:
                full_path = os.path.join(dirpath, filename)
                rel_path = os.path.relpath(full_path, PROJECT_ROOT)
                if fnmatch.fnmatch(rel_path, pattern):
                    matches.append(rel_path)
        matches.sort()
        if not matches:
            return "(no files matched)"
        return "\n".join(matches[:100]) + (f"\n[... {len(matches)-100} more files]" if len(matches) > 100 else "")
    except Exception as e:
        return f"(error in glob: {e})"


def grep(pattern: str, path: str = "", glob_filter: str = "") -> str:
    """内容正则搜索"""
    try:
        root = PROJECT_ROOT / path if path else PROJECT_ROOT
        results = []
        for dirpath, _, filenames in os.walk(root):
            for filename in filenames:
                full_path = os.path.join(dirpath, filename)
                rel_path = os.path.relpath(full_path, PROJECT_ROOT)
                if glob_filter and not fnmatch.fnmatch(rel_path, glob_filter):
                    continue
                try:
                    text = Path(full_path).read_text(encoding="utf-8", errors="replace")
                    for i, line in enumerate(text.splitlines(), 1):
                        if re.search(pattern, line, re.IGNORECASE):
                            results.append(f"{rel_path}:{i}: {line[:120]}")
                            if len(results) >= 200:
                                break
                except:
                    continue
        if not results:
            return "(no matches)"
        return "\n".join(results[:200]) + (f"\n[... more matches truncated]" if len(results) > 200 else "")
    except Exception as e:
        return f"(error in grep: {e})"


# ========== 创建完整工具集 ==========

def create_dogrider_tools() -> BaseToolRegistry:
    """创建 Dog-Rider 完整工具集 - Claude Code Core Toolkit (Top 6)"""
    registry = BaseToolRegistry()

    # 基础工具
    registry.register(
        name="bash",
        description="Execute a shell command. Prefer dedicated tools (read_file, glob, grep) over bash for file operations.",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute."}
            },
            "required": ["command"]
        },
        handler=base_bash,
    )

    registry.register(
        name="write_file",
        description="Write content to a file. Creates or overwrites. Use for new files or full rewrites.",
        parameters={
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "Path to the file."},
                "content": {"type": "string", "description": "Content to write."},
            },
            "required": ["filepath", "content"]
        },
        handler=base_write,
    )

    # 扩展工具
    registry.register(
        name="read_file",
        description="Read content from a file. Use instead of 'cat' in bash.",
        parameters={
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "Path to the file."},
                "offset": {"type": "number", "description": "Start reading from line number (1-indexed)."},
                "limit": {"type": "number", "description": "Maximum number of lines to read."},
            },
            "required": ["filepath"]
        },
        handler=read_file,
    )

    registry.register(
        name="edit_file",
        description="Precise string replacement in a file. Prefer this over write_file for small changes.",
        parameters={
            "type": "object",
            "properties": {
                "filepath": {"type": "string", "description": "Path to the file."},
                "old_string": {"type": "string", "description": "Exact string to match (including indentation)."},
                "new_string": {"type": "string", "description": "Replacement string."},
                "replace_all": {"type": "boolean", "description": "Replace all occurrences instead of just the first."},
            },
            "required": ["filepath", "old_string", "new_string"]
        },
        handler=edit_file,
    )

    registry.register(
        name="glob",
        description="Search for files matching a glob pattern. Use instead of 'find' in bash.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern, e.g. '**/*.py'."},
                "path": {"type": "string", "description": "Starting directory (default: project root)."},
            },
            "required": ["pattern"]
        },
        handler=glob,
    )

    registry.register(
        name="grep",
        description="Search file contents with regex. Use instead of 'grep' in bash.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for."},
                "path": {"type": "string", "description": "Starting directory or file (default: project root)."},
                "glob_filter": {"type": "string", "description": "Filter files by glob, e.g. '**/*.py'."},
            },
            "required": ["pattern"]
        },
        handler=grep,
    )

    return registry


# ========== 兼容旧 API - 供现有代码使用 ==========
_DOGRIDER_TOOLS = create_dogrider_tools()
ALL_TOOLS = _DOGRIDER_TOOLS.get_tool_defs()
TOOL_HANDLERS: Dict[str, Callable] = {
    t["function"]["name"]: _DOGRIDER_TOOLS.get_handler(t["function"]["name"])
    for t in ALL_TOOLS
}


def get_tool_handler(name: str) -> Optional:
    """获取工具处理函数 - 兼容旧 API"""
    return _DOGRIDER_TOOLS.get_handler(name)


def detect_role_from_tool_name(name: str) -> str:
    """根据工具名称推断子请求角色"""
    if name in ("read_file", "glob", "grep"):
        return "read"
    if name in ("write_file", "edit_file"):
        return "write"
    if name in ("bash",):
        return "exploration"
    return "analyze"


def _read_file(filepath: str, offset: int = 0, limit: int = 0) -> str:
    """读取文件内容"""
    try:
        path = PROJECT_ROOT / filepath
        if not path.exists():
            return f"(error: file not found: {filepath})"
        content = path.read_text(encoding="utf-8", errors="replace")

        lines = content.splitlines()
        if offset > 0 or limit > 0:
            start = max(0, offset - 1)  # convert to 0-indexed
            end = start + limit if limit > 0 else len(lines)
            lines = lines[start:end]
            content = "\n".join(lines)
            content += f"\n[read lines {start+1}-{end} of {len(content.splitlines())}]"

        if len(content) > 8000:
            content = content[:4000] + f"\n... [{len(content)} chars truncated] ...\n" + content[-1000:]
        return content
    except Exception as e:
        return f"(error reading file: {e})"


def _write_file(filepath: str, content: str) -> str:
    """写入文件内容"""
    try:
        path = PROJECT_ROOT / filepath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"(success: wrote {len(content)} chars to {filepath})"
    except Exception as e:
        return f"(error writing file: {e})"


def _edit_file(filepath: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """精确替换文件内容"""
    try:
        path = PROJECT_ROOT / filepath
        if not path.exists():
            return f"(error: file not found: {filepath})"
        content = path.read_text(encoding="utf-8")

        if old_string not in content:
            return f"(error: old_string not found in file)"
        if not replace_all and content.count(old_string) > 1:
            return f"(error: old_string appears {content.count(old_string)} times, need unique match)"

        count = content.count(old_string)
        content = content.replace(old_string, new_string, -1 if replace_all else 1)
        path.write_text(content, encoding="utf-8")
        return f"(success: replaced {count} occurrence(s) in {filepath})"
    except Exception as e:
        return f"(error editing file: {e})"


def _glob(pattern: str, path: str = "") -> str:
    """文件模式匹配搜索"""
    try:
        root = PROJECT_ROOT / path if path else PROJECT_ROOT
        matches = []
        for dirpath, dirnames, filenames in os.walk(root):
            for filename in filenames:
                full_path = os.path.join(dirpath, filename)
                rel_path = os.path.relpath(full_path, PROJECT_ROOT)
                if fnmatch.fnmatch(rel_path, pattern):
                    matches.append(rel_path)
        matches.sort()
        if not matches:
            return "(no files matched)"
        return "\n".join(matches[:100]) + (f"\n[... {len(matches)-100} more files]" if len(matches) > 100 else "")
    except Exception as e:
        return f"(error in glob: {e})"


def _grep(pattern: str, path: str = "", glob_filter: str = "") -> str:
    """内容正则搜索"""
    import re
    try:
        root = PROJECT_ROOT / path if path else PROJECT_ROOT
        results = []
        for dirpath, dirnames, filenames in os.walk(root):
            for filename in filenames:
                full_path = os.path.join(dirpath, filename)
                rel_path = os.path.relpath(full_path, PROJECT_ROOT)
                if glob_filter and not fnmatch.fnmatch(rel_path, glob_filter):
                    continue
                try:
                    text = Path(full_path).read_text(encoding="utf-8", errors="replace")
                    for i, line in enumerate(text.splitlines(), 1):
                        if re.search(pattern, line, re.IGNORECASE):
                            results.append(f"{rel_path}:{i}: {line[:120]}")
                            if len(results) >= 200:
                                break
                except:
                    continue
        if not results:
            return "(no matches)"
        return "\n".join(results[:200]) + (f"\n[... more matches truncated]" if len(results) > 200 else "")
    except Exception as e:
        return f"(error in grep: {e})"


# 工具定义 (OpenAI format) - 匹配 Claude-Core-Toolkit.md 命名
BASH_TOOL = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Execute a shell command and return stdout+stderr. Prefer dedicated tools (read_file, glob, grep) over bash for file operations.",
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

READ_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read content from a file. Use this instead of 'cat' in bash.",
        "parameters": {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": "Path to the file (relative to project root)."
                },
                "offset": {
                    "type": "number",
                    "description": "Start reading from line number (1-indexed)."
                },
                "limit": {
                    "type": "number",
                    "description": "Maximum number of lines to read."
                }
            },
            "required": ["filepath"]
        }
    }
}

WRITE_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "Write content to a file. Creates or overwrites. Use for new files or full rewrites.",
        "parameters": {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": "Path to the file (relative to project root)."
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file."
                }
            },
            "required": ["filepath", "content"]
        }
    }
}

EDIT_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "edit_file",
        "description": "Precise string replacement in a file. Prefer this over write_file for small changes.",
        "parameters": {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": "Path to the file (relative to project root)."
                },
                "old_string": {
                    "type": "string",
                    "description": "Exact string to match in the file (including indentation)."
                },
                "new_string": {
                    "type": "string",
                    "description": "Replacement string."
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace all occurrences instead of just the first."
                }
            },
            "required": ["filepath", "old_string", "new_string"]
        }
    }
}

GLOB_TOOL = {
    "type": "function",
    "function": {
        "name": "glob",
        "description": "Search for files matching a glob pattern. Use this instead of 'find' in bash.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern, e.g. '**/*.py' or 'src/**/*.ts'."
                },
                "path": {
                    "type": "string",
                    "description": "Starting directory (default: project root)."
                }
            },
            "required": ["pattern"]
        }
    }
}

GREP_TOOL = {
    "type": "function",
    "function": {
        "name": "grep",
        "description": "Search file contents with regex. Use this instead of 'grep' in bash.",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for."
                },
                "path": {
                    "type": "string",
                    "description": "Starting directory or file (default: project root)."
                },
                "glob_filter": {
                    "type": "string",
                    "description": "Filter files by glob pattern, e.g. '**/*.py'."
                }
            },
            "required": ["pattern"]
        }
    }
}

# 工具注册表 - Claude Code Core Toolkit Top 6
ALL_TOOLS = [BASH_TOOL, READ_FILE_TOOL, WRITE_FILE_TOOL, EDIT_FILE_TOOL, GLOB_TOOL, GREP_TOOL]

TOOL_HANDLERS: Dict[str, Callable] = {
    "bash": lambda **kwargs: base_bash(kwargs.get("command", "")),
    "read_file": lambda **kwargs: _read_file(
        kwargs.get("filepath", ""),
        kwargs.get("offset", 0),
        kwargs.get("limit", 0),
    ),
    "write_file": lambda **kwargs: _write_file(kwargs.get("filepath", ""), kwargs.get("content", "")),
    "edit_file": lambda **kwargs: _edit_file(
        kwargs.get("filepath", ""),
        kwargs.get("old_string", ""),
        kwargs.get("new_string", ""),
        kwargs.get("replace_all", False),
    ),
    "glob": lambda **kwargs: _glob(kwargs.get("pattern", ""), kwargs.get("path", "")),
    "grep": lambda **kwargs: _grep(kwargs.get("pattern", ""), kwargs.get("path", ""), kwargs.get("glob_filter", "")),
}


def get_tool_handler(name: str) -> Optional[Callable]:
    """获取工具处理函数"""
    return TOOL_HANDLERS.get(name)


def detect_role_from_tool_name(name: str) -> str:
    """根据工具名称推断子请求角色"""
    if name in ("read_file", "glob", "grep"):
        return "read"
    if name in ("write_file", "edit_file"):
        return "write"
    if name in ("bash",):
        return "exploration"
    return "analyze"
