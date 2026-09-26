"""限流器单测（M13-4 / M13-5 修复的核心组件）

`RateLimiter` 用注入时钟（`clock` 参数）驱动，无需真实 sleep —— 退避是
指数级增长（最长 300s），真实等待不可接受。
"""

import threading

import pytest

from app.utils.rate_limit import RateLimiter


class _FakeClock:
    """可控时钟：手动推进，避免真实等待"""

    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_threshold_below_does_not_block():
    """阈值内的失败不触发退避（正常用户输错几次不受影响）"""
    clock = _FakeClock()
    limiter = RateLimiter(threshold=3, base_delay=5.0, clock=clock)

    for _ in range(3):
        assert limiter.record_failure("k") == 0.0

    assert limiter.retry_after("k") == 0.0


def test_beyond_threshold_triggers_backoff():
    """超过阈值后返回退避秒数，且 retry_after 反映剩余等待"""
    clock = _FakeClock()
    limiter = RateLimiter(threshold=3, base_delay=5.0, max_delay=300.0, clock=clock)

    for _ in range(3):
        limiter.record_failure("k")

    assert limiter.record_failure("k") == 5.0  # 第 4 次 → base_delay
    assert limiter.retry_after("k") == 5.0


def test_backoff_grows_exponentially():
    """退避时长逐次翻倍（2 → 4 → 8 倍 base）"""
    clock = _FakeClock()
    limiter = RateLimiter(threshold=1, base_delay=2.0, max_delay=1000.0, clock=clock)

    limiter.record_failure("k")  # 第 1 次：等于阈值，不触发
    delays = [limiter.record_failure("k") for _ in range(4)]
    assert delays == [2.0, 4.0, 8.0, 16.0]


def test_backoff_is_capped_at_max_delay():
    """退避时长有上限，不会无限增长"""
    clock = _FakeClock()
    limiter = RateLimiter(threshold=1, base_delay=10.0, max_delay=40.0, clock=clock)

    limiter.record_failure("k")
    delays = [limiter.record_failure("k") for _ in range(6)]
    assert max(delays) == 40.0
    assert delays[-1] == 40.0


def test_retry_after_decreases_with_time():
    """退避期随时钟推进而缩短，归零后可再次尝试"""
    clock = _FakeClock()
    limiter = RateLimiter(threshold=1, base_delay=10.0, clock=clock)

    limiter.record_failure("k")
    limiter.record_failure("k")  # 触发 10s 退避

    clock.advance(4.0)
    assert limiter.retry_after("k") == pytest.approx(6.0)
    clock.advance(6.0)
    assert limiter.retry_after("k") == 0.0


def test_reset_clears_failures():
    """成功认证后清零失败记录（不惩罚后续正常使用）"""
    clock = _FakeClock()
    limiter = RateLimiter(threshold=2, base_delay=5.0, clock=clock)

    limiter.record_failure("k")
    limiter.record_failure("k")
    limiter.record_failure("k")  # 已进入退避
    assert limiter.retry_after("k") > 0

    limiter.reset("k")
    assert limiter.retry_after("k") == 0.0
    # 重新开始计数
    assert limiter.record_failure("k") == 0.0


def test_keys_are_isolated():
    """不同 key 互不影响（一个客户端被限不牵连其他客户端）"""
    clock = _FakeClock()
    limiter = RateLimiter(threshold=1, base_delay=5.0, clock=clock)

    limiter.record_failure("a")
    limiter.record_failure("a")  # a 进入退避
    assert limiter.retry_after("a") > 0
    assert limiter.retry_after("b") == 0.0
    assert limiter.record_failure("b") == 0.0


def test_window_expiry_resets_counter():
    """窗口过期后失败计数归零（陈年失败不永久影响）"""
    clock = _FakeClock()
    limiter = RateLimiter(threshold=2, base_delay=5.0, window=60.0, clock=clock)

    limiter.record_failure("k")
    limiter.record_failure("k")
    limiter.record_failure("k")  # 进入退避
    assert limiter.retry_after("k") > 0

    clock.advance(61.0)
    assert limiter.retry_after("k") == 0.0
    assert limiter.record_failure("k") == 0.0  # 计数已重置


def test_eviction_bounds_memory():
    """条目数超上限时被清理（防攻击者用海量 key 撑爆内存）"""
    clock = _FakeClock()
    limiter = RateLimiter(
        threshold=1, base_delay=1.0, window=10.0, max_entries=20, clock=clock
    )

    for i in range(200):
        limiter.record_failure(f"key-{i}")
        clock.advance(1.0)

    assert len(limiter._entries) <= 20


def test_thread_safety_under_concurrency():
    """并发失败计数不丢（FastAPI 同步路由跑在线程池里）"""
    limiter = RateLimiter(threshold=1000, base_delay=1.0)  # 阈值拉高，只数次数
    threads = [
        threading.Thread(
            target=lambda: [limiter.record_failure("k") for _ in range(50)]
        )
        for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert limiter._entries["k"].fails == 400


def test_retry_after_unknown_key_is_zero():
    """未记录的 key 直接放行"""
    limiter = RateLimiter()
    assert limiter.retry_after("never-seen") == 0.0
