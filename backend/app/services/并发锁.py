"""并发投递锁（hook-10 争用失败测救绿服务）。

按『事件号』分键的进程内互斥锁，回放旁路与正式投递打同一把锁，
把『读状态 -> 校验 -> 写尝试/改状态 -> 提交』变成临界区，杜绝：
  * 同一事件被并发正式投递写出多条成功流水（重复投递）；
  * 回放与正式投递并发对现行水位/事件行的脏写、丢失更新。

单容器 `python -m app.main`（Flask threaded）内即同一进程，锁有效；
另在 db.连接 设 busy_timeout，并在写口临界区内复检状态，双保险。
"""
from __future__ import annotations

import threading
import time
import uuid
from contextlib import contextmanager

_元锁 = threading.Lock()
_锁表: dict[str, threading.RLock] = {}

#: 同锁命名空间，响应/脚本据此核对“两个请求打同一把锁”
命名空间 = "hook-10:并发投递锁"


class 锁占用错误(RuntimeError):
    """等待超时仍拿不到该事件锁。"""


def 锁键(事件号: str) -> str:
    return f"{命名空间}:{事件号}"


def _取锁(事件号: str) -> threading.RLock:
    with _元锁:
        lk = _锁表.get(事件号)
        if lk is None:
            lk = threading.RLock()
            _锁表[事件号] = lk
        return lk


@contextmanager
def 同锁(事件号: str, 超时: float = 10.0):
    """获取该事件的同锁，返回锁凭据 dict；退出释放。可重入。"""
    lk = _取锁(事件号)
    上限 = time.monotonic() + max(0.0, float(超时))
    got = lk.acquire(timeout=max(0.0, 上限 - time.monotonic()))
    if not got:
        raise 锁占用错误(f"事件 {事件号} 的投递锁被占用，已拒绝并发脏写")
    凭据 = {
        "事件号": 事件号,
        "锁键": 锁键(事件号),
        "同锁": True,
        "持有者": f"LOCK-{uuid.uuid4().hex[:10]}",
    }
    try:
        yield 凭据
    finally:
        lk.release()


def 尝试获锁(事件号: str, 超时: float = 0.05) -> bool:
    """非阻塞/短等尝试。成功则由调用线程负责 释放(事件号)。"""
    return _取锁(事件号).acquire(timeout=max(0.0, float(超时)))


def 释放(事件号: str) -> None:
    _取锁(事件号).release()
