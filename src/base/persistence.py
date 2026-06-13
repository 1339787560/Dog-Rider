"""会话持久化模块 - WAL + Checkpoint

提供长线运行的崩溃恢复能力：
- WAL: 每条消息追加 JSONL，崩溃可重建
- Checkpoint: 周期性快照（串行/并行触发策略不同）
- SessionManager: 协调 WAL + Checkpoint，提供 save/load/resume API

文件结构:
    {project_root}/.cache/sessions/{session_id}/
    ├── meta.json         # session 元信息
    ├── checkpoint.json   # 最近完整快照
    └── wal.jsonl         # 增量日志
"""
import json
import os
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


# 项目根目录 = src/base/persistence.py 上溯 3 层
PROJECT_ROOT = Path(__file__).parent.parent.parent
DEFAULT_CACHE_DIR = PROJECT_ROOT / ".cache" / "sessions"


# ========== 数据结构 ==========

@dataclass
class WALEvent:
    """WAL 单条事件"""
    timestamp: str  # ISO 格式
    event_type: str  # "message" / "stats_update" / "task_start" / "task_end"
    data: Dict[str, Any] = field(default_factory=dict)

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False) + "\n"

    @classmethod
    def from_jsonl(cls, line: str) -> "WALEvent":
        d = json.loads(line.strip())
        return cls(**d)


@dataclass
class SessionState:
    """完整 session 快照"""
    session_id: str
    created_at: str
    updated_at: str
    model: str
    messages: List[dict] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    last_error: str = ""
    parent_id: str = ""  # 并行模式下子线程关联到主 session

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SessionState":
        return cls(**d)


# ========== WAL Writer ==========

class WALWriter:
    """WAL 文件写入器 - 线程安全 + 可选 fsync"""

    def __init__(self, filepath: Path, fsync: bool = True):
        self.filepath = filepath
        self.fsync = fsync
        self._lock = threading.Lock()
        # 确保父目录存在
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        # 打开文件，append 模式
        self._file = open(self.filepath, "a", encoding="utf-8")

    def append(self, event: WALEvent):
        """追加一条事件"""
        with self._lock:
            self._file.write(event.to_jsonl())
            self._file.flush()
            if self.fsync:
                os.fsync(self._file.fileno())

    def close(self):
        """关闭文件"""
        with self._lock:
            if not self._file.closed:
                self._file.flush()
                if self.fsync:
                    try:
                        os.fsync(self._file.fileno())
                    except (OSError, ValueError):
                        pass
                self._file.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    @staticmethod
    def read_all(filepath: Path) -> List[WALEvent]:
        """读取全部事件，跳过损坏行"""
        if not filepath.exists():
            return []
        events = []
        with open(filepath, "r", encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(WALEvent.from_jsonl(line))
                except (json.JSONDecodeError, TypeError, KeyError) as e:
                    print(f"[WAL] Skipping corrupted line {i}: {e}", file=sys.stderr)
                    continue
        return events


# ========== Checkpoint Store ==========

class CheckpointStore:
    """Checkpoint 存储 - 原子写入（temp + rename）"""

    def __init__(self, filepath: Path):
        self.filepath = filepath
        self._lock = threading.Lock()
        self.filepath.parent.mkdir(parents=True, exist_ok=True)

    def save(self, state: SessionState):
        """保存快照 - 原子操作"""
        with self._lock:
            state.updated_at = datetime.now().isoformat()
            data = state.to_dict()
            # 先写入临时文件，再 rename
            fd, tmp_path = tempfile.mkstemp(
                dir=self.filepath.parent,
                prefix=".tmp_checkpoint_"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self.filepath)
            except Exception:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
                raise

    def load(self) -> Optional[SessionState]:
        """加载快照"""
        with self._lock:
            if not self.filepath.exists():
                return None
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return SessionState.from_dict(data)
            except (json.JSONDecodeError, TypeError, KeyError) as e:
                print(f"[Checkpoint] Failed to load: {e}", file=sys.stderr)
                return None


# ========== Trigger 策略 ==========

class CheckpointTrigger:
    """触发器基类"""

    def should_trigger(self) -> bool:
        raise NotImplementedError

    def reset(self):
        raise NotImplementedError

    def on_request(self):
        """每次 API 请求后调用"""
        pass

    def on_exception(self, exc: Exception):
        """异常时调用"""
        pass


class SerialTrigger(CheckpointTrigger):
    """串行模式：60 秒 OR 20 请求触发"""

    def __init__(self, interval_sec: int = 60, request_threshold: int = 20):
        self.interval_sec = interval_sec
        self.request_threshold = request_threshold
        self._lock = threading.Lock()
        self._last_save_time = time.time()
        self._request_count = 0

    def should_trigger(self) -> bool:
        with self._lock:
            elapsed = time.time() - self._last_save_time
            if elapsed >= self.interval_sec:
                return True
            if self._request_count >= self.request_threshold:
                return True
            return False

    def on_request(self):
        with self._lock:
            self._request_count += 1

    def reset(self):
        with self._lock:
            self._last_save_time = time.time()
            self._request_count = 0


class ParallelTrigger(CheckpointTrigger):
    """并行模式：仅异常退出时触发"""

    def __init__(self):
        self._lock = threading.Lock()
        self._has_exception = False

    def should_trigger(self) -> bool:
        with self._lock:
            return self._has_exception

    def on_exception(self, exc: Exception):
        with self._lock:
            self._has_exception = True

    def reset(self):
        with self._lock:
            self._has_exception = False


# ========== Session Manager ==========

class SessionManager:
    """会话持久化管理器 - 协调 WAL + Checkpoint"""

    def __init__(
        self,
        session_id: Optional[str] = None,
        cache_dir: Optional[Path] = None,
        trigger: Optional[CheckpointTrigger] = None,
        wal_fsync: bool = True,
    ):
        self.session_id = session_id or str(uuid.uuid4())[:8]
        self.cache_dir = (cache_dir or DEFAULT_CACHE_DIR) / self.session_id
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.checkpoint_path = self.cache_dir / "checkpoint.json"
        self.wal_path = self.cache_dir / "wal.jsonl"
        self.meta_path = self.cache_dir / "meta.json"

        self.checkpoint_store = CheckpointStore(self.checkpoint_path)
        self.wal_writer = WALWriter(self.wal_path, fsync=wal_fsync)
        self.trigger = trigger or SerialTrigger()
        self._closed = False

    def write_meta(self, state: SessionState):
        """写入 meta.json"""
        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump({
                "session_id": state.session_id,
                "created_at": state.created_at,
                "updated_at": state.updated_at,
                "model": state.model,
                "parent_id": state.parent_id,
                "last_error": state.last_error,
            }, f, ensure_ascii=False, indent=2)

    def append_wal(self, event_type: str, **data):
        """追加 WAL 事件"""
        if self._closed:
            return
        event = WALEvent(
            timestamp=datetime.now().isoformat(),
            event_type=event_type,
            data=data,
        )
        self.wal_writer.append(event)
        self.trigger.on_request()

    def maybe_checkpoint(self, state: SessionState, force: bool = False) -> bool:
        """根据 trigger 决定是否触发 checkpoint"""
        if self._closed:
            return False
        if force or self.trigger.should_trigger():
            self.checkpoint_store.save(state)
            self.write_meta(state)
            self.trigger.reset()
            return True
        return False

    def on_exception(self, exc: Exception, state: SessionState):
        """异常时强制触发"""
        self.trigger.on_exception(exc)
        state.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
        self.checkpoint_store.save(state)
        self.write_meta(state)

    def close(self):
        """关闭所有资源"""
        if self._closed:
            return
        self._closed = True
        self.wal_writer.close()

    def resume(self) -> Optional[SessionState]:
        """从 checkpoint + WAL 恢复 SessionState

        策略：load checkpoint → 重放 WAL 中 checkpoint 之后的事件
        """
        state = self.checkpoint_store.load()
        if state is None:
            # 没有 checkpoint，仅从 WAL 重建
            events = WALWriter.read_all(self.wal_path)
            if not events:
                return None
            # 从 WAL 第一条 task_start 重建（最简：找到所有 message 事件）
            state = SessionState(
                session_id=self.session_id,
                created_at=events[0].timestamp,
                updated_at=events[-1].timestamp,
                model="",
            )

        # 重放 WAL 中 updated_at 之后的事件
        events = WALWriter.read_all(self.wal_path)
        checkpoint_time = state.updated_at
        for event in events:
            if event.timestamp <= checkpoint_time:
                continue
            if event.event_type == "message":
                msg = event.data.get("message")
                if msg:
                    state.messages.append(msg)
            elif event.event_type == "stats_update":
                stats_delta = event.data.get("delta", {})
                for k, v in stats_delta.items():
                    if isinstance(v, (int, float)):
                        state.stats[k] = state.stats.get(k, 0) + v

        return state

    @staticmethod
    def list_sessions(cache_dir: Optional[Path] = None) -> List[str]:
        """列出所有已存在的 session_id"""
        cache_dir = cache_dir or DEFAULT_CACHE_DIR
        if not cache_dir.exists():
            return []
        return [
            p.name for p in cache_dir.iterdir()
            if p.is_dir() and (p / "meta.json").exists()
        ]
