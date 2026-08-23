"""后台任务管理：一键检测拆为"启动任务 + 轮询进度"两个接口。

为什么不直接 SSE：AstrBot 插件 Page 桥接层只支持 JSON 同步调用，
无法稳定把流式响应透传到前端。所以采用经典异步任务 + 轮询：
- POST /panel/providers/test_all_stream  立即返回 {session_id, started: true}
  同步启动一个 asyncio.Task 在后台跑检测，每完成一个模型就更新
  全局 _session_state[session_id].items 列表。
- GET  /panel/providers/session/{id}      返回当前进度（已完成的 items + 完成度统计）
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional


class SessionState:
    """一次后台检测任务的实时状态（内存中）。"""

    def __init__(self, session_id: int, total: int):
        self.session_id = session_id
        self.total = total
        self.items: list[dict] = []
        self.done = False
        self.error: Optional[str] = None
        self.started_at = int(time.time())
        self.finished_at: Optional[int] = None
        self.ok_count = 0
        self.fail_count = 0
        self.skip_count = 0

    def snapshot(self) -> dict:
        return {
            "session_id": self.session_id,
            "total": self.total,
            "done": self.done,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "ok_count": self.ok_count,
            "fail_count": self.fail_count,
            "skip_count": self.skip_count,
            "items": list(self.items),
        }


class SessionManager:
    """简易 in-memory session 状态机，支持轮询与并发去重。"""

    def __init__(self, max_history: int = 50):
        self._states: dict[int, SessionState] = {}
        self._lock = asyncio.Lock()
        self._max_history = max_history

    async def create(self, session_id: int, total: int) -> SessionState:
        async with self._lock:
            # 清理过老的会话
            if len(self._states) >= self._max_history:
                finished_ids = sorted(
                    [s.session_id for s in self._states.values() if s.done],
                    key=lambda i: self._states[i].finished_at or 0,
                )
                for sid in finished_ids[: len(self._states) - self._max_history + 1]:
                    self._states.pop(sid, None)
            st = SessionState(session_id, total)
            self._states[session_id] = st
            return st

    def get(self, session_id: int) -> Optional[SessionState]:
        return self._states.get(session_id)

    def append_item(self, session_id: int, item: dict) -> None:
        st = self._states.get(session_id)
        if st is None:
            return
        st.items.append(item)
        if item.get("skipped"):
            st.skip_count += 1
        elif item.get("ok"):
            st.ok_count += 1
        else:
            st.fail_count += 1

    def finish(self, session_id: int, error: Optional[str] = None) -> None:
        st = self._states.get(session_id)
        if st is None:
            return
        st.done = True
        st.error = error
        st.finished_at = int(time.time())

    def is_busy(self) -> bool:
        return any((not s.done) for s in self._states.values())
