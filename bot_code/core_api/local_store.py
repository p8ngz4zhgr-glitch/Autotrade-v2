import time
import json
import asyncio
import logging
import threading
from typing import Optional, Any, List, Tuple

log = logging.getLogger("LocalStore")

class LocalRedisShim:
    """
    ⚡ THAY THẾ REDIS DÀNH CHO RENDER FREE (512MB RAM):
    - Không bị giới hạn 10,000 request/ngày như Upstash Redis Free.
    - Không chạy Redis Server riêng giúp tiết kiệm RAM (< 1MB RAM footprint).
    - Hỗ trợ In-Memory Queue + Async Queue cho FastAPI & Signal Scanner.
    - Tự động fallback khi Redis thật bị ngắt kết nối/hết quota.
    """
    def __init__(self):
        self._store: dict[str, tuple[str, Optional[float]]] = {} # key -> (val_str, expire_at)
        self._lists: dict[str, list[str]] = {}                  # key -> [val1, val2]
        self._async_queues: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = {}
        self._lock = threading.Lock()

    def _clean_expired(self):
        now = time.time()
        expired_keys = [k for k, (v, exp) in self._store.items() if exp and now > exp]
        for k in expired_keys:
            del self._store[k]

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            self._clean_expired()
            if key in self._store:
                val, exp = self._store[key]
                if exp and time.time() > exp:
                    del self._store[key]
                    return None
                return val
            return None

    def set(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        with self._lock:
            val_str = json.dumps(value) if not isinstance(value, str) else value
            exp_time = (time.time() + ex) if ex else None
            self._store[key] = (val_str, exp_time)
            return True

    def setex(self, key: str, time_sec: int, value: Any) -> bool:
        return self.set(key, value, ex=time_sec)

    def delete(self, *keys: str) -> int:
        count = 0
        with self._lock:
            for k in keys:
                if k in self._store:
                    del self._store[k]
                    count += 1
                if k in self._lists:
                    del self._lists[k]
                    count += 1
        return count

    def exists(self, key: str) -> bool:
        return self.get(key) is not None

    def lpush(self, name: str, *values: str) -> int:
        with self._lock:
            if name not in self._lists:
                self._lists[name] = []
            for v in values:
                v_str = json.dumps(v) if not isinstance(v, str) else v
                self._lists[name].insert(0, v_str)

            if name in self._async_queues:
                loop, q = self._async_queues[name]
                for v in values:
                    v_str = json.dumps(v) if not isinstance(v, str) else v
                    try:
                        loop.call_soon_threadsafe(q.put_nowait, (name, v_str))
                    except Exception:
                        pass
            return len(self._lists[name])

    def rpush(self, name: str, *values: str) -> int:
        with self._lock:
            if name not in self._lists:
                self._lists[name] = []
            for v in values:
                v_str = json.dumps(v) if not isinstance(v, str) else v
                self._lists[name].append(v_str)

            if name in self._async_queues:
                loop, q = self._async_queues[name]
                for v in values:
                    v_str = json.dumps(v) if not isinstance(v, str) else v
                    try:
                        loop.call_soon_threadsafe(q.put_nowait, (name, v_str))
                    except Exception:
                        pass
            return len(self._lists[name])

    def lrange(self, name: str, start: int, stop: int) -> list:
        with self._lock:
            items = self._lists.get(name, [])
            if stop == -1:
                return items[start:]
            return items[start:stop + 1]

    def ltrim(self, name: str, start: int, stop: int) -> bool:
        with self._lock:
            if name in self._lists:
                if stop == -1:
                    self._lists[name] = self._lists[name][start:]
                else:
                    self._lists[name] = self._lists[name][start:stop + 1]
            return True

    def ping(self) -> bool:
        return True

    def register_async_loop(self, name: str, loop: asyncio.AbstractEventLoop) -> asyncio.Queue:
        with self._lock:
            if name not in self._async_queues or self._async_queues[name][0] != loop:
                q = asyncio.Queue()
                if name in self._lists:
                    for item in reversed(self._lists[name]):
                        q.put_nowait((name, item))
                self._async_queues[name] = (loop, q)
            return self._async_queues[name][1]

    async def blpop_async(self, name: str, timeout: int = 2) -> Optional[Tuple[str, str]]:
        loop = asyncio.get_running_loop()
        q = self.register_async_loop(name, loop)
        try:
            res = await asyncio.wait_for(q.get(), timeout=timeout)
            return res
        except asyncio.TimeoutError:
            with self._lock:
                if name in self._lists and len(self._lists[name]) > 0:
                    val = self._lists[name].pop()
                    return (name, val)
            return None

local_store = LocalRedisShim()
