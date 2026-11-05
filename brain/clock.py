"""clock.py — 引擎唯一时间源（v4.1 新增）

为什么需要它：v4.0 的引擎到处直接调 `time.time()`，导致"半年后会发生什么"
根本没法测——衰减、归档、到期提醒、周年回响全是长时程行为，
而在真实时间里等半年不是测试。

有了可注入时钟，`tools/simulate.py` 就能在几秒内跑完 180 天：
第 0 天写下的一堆记忆，第 30/90/180 天分别还剩多少可检索、哪些归档、
到期承诺有没有在正确的那天浮出来。这是"记住几个月"这句承诺唯一可验证的方式。

用法：
    from brain import clock
    clock.set_clock(clock.VirtualClock("2026-01-01").now)   # 模拟
    clock.reset()                                            # 恢复真实时间
"""
from __future__ import annotations
import datetime as _dt
import time as _time


def _real() -> float:
    return _time.time()


_now = _real


def now() -> float:
    """当前时间戳。引擎里所有"现在"都必须走这里，不要再直接 time.time()。"""
    return _now()


def set_clock(fn) -> None:
    global _now
    _now = fn


def reset() -> None:
    global _now
    _now = _real


def localtime(ts: float = None):
    return _time.localtime(now() if ts is None else ts)


def strftime(fmt: str, ts: float = None) -> str:
    return _time.strftime(fmt, localtime(ts))


class VirtualClock:
    """可推进的假时钟。默认从指定日期零点开始。"""

    def __init__(self, start="2026-01-01", timestamp: float = None):
        if timestamp is not None:
            self._t = float(timestamp)
        else:
            d = _dt.datetime.strptime(start, "%Y-%m-%d")
            self._t = _time.mktime(d.timetuple())

    def now(self) -> float:
        return self._t

    def advance(self, days: float = 0.0, hours: float = 0.0, seconds: float = 0.0) -> float:
        self._t += days * 86400.0 + hours * 3600.0 + seconds
        return self._t

    def date(self) -> str:
        return _time.strftime("%Y-%m-%d", _time.localtime(self._t))

    def __enter__(self):
        self._prev = _now
        set_clock(self.now)
        return self

    def __exit__(self, *exc):
        global _now
        _now = self._prev
        return False
