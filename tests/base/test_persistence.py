"""持久化模块单元测试

覆盖:
- WALWriter: 追加、fsync、并发、损坏行处理
- CheckpointStore: 原子写、加载、部分写不损坏
- SessionManager: resume、WAL replay、损坏容错、列出 sessions
- SerialTrigger: 60s/20req 触发、reset
- ParallelTrigger: 异常触发、正常不触发
"""
import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.base.persistence import (
    WALEvent, SessionState,
    WALWriter, CheckpointStore, SessionManager,
    SerialTrigger, ParallelTrigger,
)


# ========== Fixtures ==========

@pytest.fixture
def tmp_dir():
    """临时目录"""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


# ========== WALWriter ==========

class TestWALWriter:

    def test_append_jsonl_format(self, tmp_dir):
        wal_path = tmp_dir / "wal.jsonl"
        writer = WALWriter(wal_path, fsync=False)
        event = WALEvent(
            timestamp="2025-01-01T00:00:00",
            event_type="message",
            data={"role": "user", "content": "hi"}
        )
        writer.append(event)
        writer.close()

        # 验证文件内容
        lines = wal_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["event_type"] == "message"
        assert parsed["data"]["content"] == "hi"

    def test_fsync_blocking(self, tmp_dir):
        """fsync 启用时 append 不抛异常"""
        wal_path = tmp_dir / "wal.jsonl"
        writer = WALWriter(wal_path, fsync=True)
        event = WALEvent(
            timestamp="2025-01-01T00:00:00",
            event_type="message",
            data={"x": 1}
        )
        writer.append(event)
        writer.close()
        assert wal_path.exists()

    def test_concurrent_append_no_interleave(self, tmp_dir):
        """多线程追加不交错"""
        wal_path = tmp_dir / "wal.jsonl"
        writer = WALWriter(wal_path, fsync=False)
        n_threads = 10
        n_per_thread = 50

        def worker(tid):
            for i in range(n_per_thread):
                writer.append(WALEvent(
                    timestamp=datetime.now().isoformat(),
                    event_type="message",
                    data={"tid": tid, "i": i}
                ))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        writer.close()

        # 验证总行数 + 每行可解析
        lines = wal_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == n_threads * n_per_thread
        for line in lines:
            parsed = json.loads(line)  # 不抛异常
            assert "tid" in parsed["data"]

    def test_corrupted_last_line_handling(self, tmp_dir):
        """损坏行被跳过"""
        wal_path = tmp_dir / "wal.jsonl"
        # 手动写入：1 行正常 + 1 行损坏
        with open(wal_path, "w", encoding="utf-8") as f:
            f.write('{"timestamp": "2025-01-01", "event_type": "message", "data": {}}\n')
            f.write('{"incomplete":')  # 损坏

        events = WALWriter.read_all(wal_path)
        assert len(events) == 1
        assert events[0].event_type == "message"


# ========== CheckpointStore ==========

class TestCheckpointStore:

    def _make_state(self, sid="test"):
        return SessionState(
            session_id=sid,
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
            model="deepseek-chat",
            messages=[{"role": "user", "content": "hi"}],
            stats={"requests": 1},
        )

    def test_save_atomic_via_rename(self, tmp_dir):
        """save 使用 temp + rename 保证原子性"""
        cp_path = tmp_dir / "checkpoint.json"
        store = CheckpointStore(cp_path)
        store.save(self._make_state())
        assert cp_path.exists()
        # 临时文件应该已被 rename，不留残骸
        residual = list(tmp_dir.glob(".tmp_checkpoint_*"))
        assert residual == []

    def test_load_returns_identical_state(self, tmp_dir):
        cp_path = tmp_dir / "checkpoint.json"
        store = CheckpointStore(cp_path)
        original = self._make_state()
        store.save(original)
        loaded = store.load()
        assert loaded is not None
        assert loaded.session_id == original.session_id
        assert loaded.messages == original.messages
        assert loaded.stats == original.stats

    def test_load_missing_returns_none(self, tmp_dir):
        cp_path = tmp_dir / "missing.json"
        store = CheckpointStore(cp_path)
        assert store.load() is None

    def test_load_corrupted_returns_none(self, tmp_dir):
        cp_path = tmp_dir / "checkpoint.json"
        cp_path.write_text("{invalid json", encoding="utf-8")
        store = CheckpointStore(cp_path)
        assert store.load() is None


# ========== SerialTrigger ==========

class TestSerialTrigger:

    def test_request_threshold_triggers(self):
        trigger = SerialTrigger(interval_sec=9999, request_threshold=3)
        assert not trigger.should_trigger()
        trigger.on_request()
        trigger.on_request()
        assert not trigger.should_trigger()
        trigger.on_request()  # 第 3 次
        assert trigger.should_trigger()

    def test_time_interval_triggers(self):
        trigger = SerialTrigger(interval_sec=0, request_threshold=999)
        time.sleep(0.01)
        assert trigger.should_trigger()

    def test_reset_clears_both(self):
        trigger = SerialTrigger(interval_sec=9999, request_threshold=3)
        trigger.on_request()
        trigger.on_request()
        trigger.on_request()
        assert trigger.should_trigger()
        trigger.reset()
        assert not trigger.should_trigger()


# ========== ParallelTrigger ==========

class TestParallelTrigger:

    def test_normal_no_trigger(self):
        trigger = ParallelTrigger()
        assert not trigger.should_trigger()
        trigger.on_request()
        trigger.on_request()
        assert not trigger.should_trigger()

    def test_exception_triggers(self):
        trigger = ParallelTrigger()
        trigger.on_exception(RuntimeError("test"))
        assert trigger.should_trigger()

    def test_reset_clears_exception(self):
        trigger = ParallelTrigger()
        trigger.on_exception(ValueError("x"))
        assert trigger.should_trigger()
        trigger.reset()
        assert not trigger.should_trigger()


# ========== SessionManager ==========

class TestSessionManager:

    def test_resume_with_checkpoint_only(self, tmp_dir):
        mgr = SessionManager(session_id="s1", cache_dir=tmp_dir)
        state = SessionState(
            session_id="s1",
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
            model="m",
            messages=[{"role": "user", "content": "hi"}],
        )
        mgr.checkpoint_store.save(state)
        mgr.close()

        mgr2 = SessionManager(session_id="s1", cache_dir=tmp_dir)
        resumed = mgr2.resume()
        mgr2.close()

        assert resumed is not None
        assert len(resumed.messages) == 1

    def test_resume_replays_wal(self, tmp_dir):
        mgr = SessionManager(session_id="s2", cache_dir=tmp_dir, wal_fsync=False)
        state = SessionState(
            session_id="s2",
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
            model="m",
            messages=[{"role": "user", "content": "msg1"}],
        )
        mgr.checkpoint_store.save(state)
        mgr.write_meta(state)

        # 模拟 checkpoint 之后追加事件
        time.sleep(0.01)
        mgr.append_wal("message", message={"role": "assistant", "content": "msg2"})
        mgr.append_wal("message", message={"role": "user", "content": "msg3"})
        mgr.close()

        mgr2 = SessionManager(session_id="s2", cache_dir=tmp_dir, wal_fsync=False)
        resumed = mgr2.resume()
        mgr2.close()

        assert resumed is not None
        # checkpoint 有 1 条，WAL 重放追加 2 条
        assert len(resumed.messages) == 3
        assert resumed.messages[1]["content"] == "msg2"
        assert resumed.messages[2]["content"] == "msg3"

    def test_resume_corrupted_wal(self, tmp_dir):
        mgr = SessionManager(session_id="s3", cache_dir=tmp_dir, wal_fsync=False)
        state = SessionState(
            session_id="s3",
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
            model="m",
        )
        mgr.checkpoint_store.save(state)
        mgr.close()

        # 手动追加 1 条正常 + 1 条损坏
        wal_path = tmp_dir / "s3" / "wal.jsonl"
        with open(wal_path, "a", encoding="utf-8") as f:
            f.write('{"timestamp": "9999-01-01", "event_type": "message", "data": {"message": {"role": "user", "content": "x"}}}\n')
            f.write('{"corrupted":')

        mgr2 = SessionManager(session_id="s3", cache_dir=tmp_dir, wal_fsync=False)
        resumed = mgr2.resume()  # 不抛异常
        mgr2.close()
        assert resumed is not None

    def test_list_sessions(self, tmp_dir):
        # 创建 3 个 session
        for sid in ["a", "b", "c"]:
            mgr = SessionManager(session_id=sid, cache_dir=tmp_dir)
            state = SessionState(session_id=sid, created_at="t", updated_at="t", model="m")
            mgr.checkpoint_store.save(state)
            mgr.write_meta(state)
            mgr.close()

        sessions = SessionManager.list_sessions(tmp_dir)
        assert sorted(sessions) == ["a", "b", "c"]

    def test_maybe_checkpoint_with_serial_trigger(self, tmp_dir):
        trigger = SerialTrigger(interval_sec=9999, request_threshold=2)
        mgr = SessionManager(
            session_id="s4",
            cache_dir=tmp_dir,
            trigger=trigger,
            wal_fsync=False,
        )
        state = SessionState(session_id="s4", created_at="t", updated_at="t", model="m")

        # 第 1 次 append，未到阈值
        mgr.append_wal("message", message={"role": "user", "content": "1"})
        assert not mgr.maybe_checkpoint(state)

        # 第 2 次 append，达到阈值
        mgr.append_wal("message", message={"role": "user", "content": "2"})
        assert mgr.maybe_checkpoint(state)

        # 触发后 reset，再次 append 不触发
        mgr.append_wal("message", message={"role": "user", "content": "3"})
        assert not mgr.maybe_checkpoint(state)

        mgr.close()

    def test_on_exception_forces_checkpoint(self, tmp_dir):
        mgr = SessionManager(
            session_id="s5",
            cache_dir=tmp_dir,
            trigger=ParallelTrigger(),
            wal_fsync=False,
        )
        state = SessionState(session_id="s5", created_at="t", updated_at="t", model="m")

        try:
            raise RuntimeError("test error")
        except RuntimeError as e:
            mgr.on_exception(e, state)

        # checkpoint 文件应已存在
        assert (tmp_dir / "s5" / "checkpoint.json").exists()
        # last_error 应记录
        loaded = mgr.checkpoint_store.load()
        assert "RuntimeError" in loaded.last_error

        mgr.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
