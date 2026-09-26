"""失败计数 + 指数退避限流器（防在线枚举；零新增依赖）

背景（安全审计 M13-4 / M13-5）：

两条「凭猜测的短字符串换取身份」的通道原先没有任何速率限制：

1. **开放 API Token 认证**（`token_service.authenticate`）——Token 熵足够高
   （128 bit），枚举不可行；风险在「无限次尝试」本身消耗 CPU（每次都要
   SHA-256 + 查库）。
2. **家庭邀请码**（`family_service.join_family`）——8 位 × 31 字符集 ≈ 40 bit，
   对在线枚举**偏弱**：按每秒 100 次估算，穷举一半空间约需数百年（单机），
   但并发/分布式下并非不可想象。这是 P1 的重点。

设计取舍：

- **按 key 计数**（key = 通道 + 客户端标识），而非全局计数——避免一个攻击者
  把所有人锁死（拒绝服务）。
- **指数退避**：第 N 次连续失败后需等待 `BASE * 2^(N-THRESHOLD)` 秒，上限
  `MAX_DELAY`。成功即清零。
- **纯内存**：应用重启即重置。对本场景足够（重启是管理员的主动行为，且
  重启后攻击者需重新累积失败次数）；换来零依赖与零落盘。
- **线程安全**：内部 `threading.Lock`。FastAPI 同步路由跑在线程池里，
  必须加锁。
- **容量上限**：字典条目数超限时清理过期项，防内存增长（攻击者用海量
  不同 key 灌爆）。

与 `_safe_remove` 的沙箱护栏无关；本模块不做任何文件操作。
"""

import threading
import time
from dataclasses import dataclass, field

# 触发退避前允许的连续失败次数（前几次失败通常是用户输错，不该立刻惩罚）
DEFAULT_THRESHOLD = 5
# 首次退避时长（秒）；每次继续失败翻倍
DEFAULT_BASE_DELAY = 2.0
# 单次退避上限（秒）：再久也意义不大，且会长期占住条目
DEFAULT_MAX_DELAY = 300.0
# 失败记录保留时长（秒）：超过则视为「重新开始」，避免陈年失败永久影响
DEFAULT_WINDOW = 900.0
# 字典条目上限（防内存增长）
DEFAULT_MAX_ENTRIES = 4096


@dataclass
class _Entry:
    """单 key 的失败状态"""

    fails: int = 0
    blocked_until: float = 0.0
    updated_at: float = field(default_factory=time.monotonic)


class RateLimiter:
    """按 key 的失败计数 + 指数退避限流器

    用法::

        limiter = RateLimiter()
        wait = limiter.retry_after("token:1.2.3.4")
        if wait > 0:
            raise TooManyRequests(...)   # 还在退避期
        if ok:
            limiter.reset(key)
        else:
            limiter.record_failure(key)
    """

    def __init__(
        self,
        *,
        threshold: int = DEFAULT_THRESHOLD,
        base_delay: float = DEFAULT_BASE_DELAY,
        max_delay: float = DEFAULT_MAX_DELAY,
        window: float = DEFAULT_WINDOW,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock=time.monotonic,
    ):
        self.threshold = threshold
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.window = window
        self.max_entries = max_entries
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}

    # ---------- 查询 ----------

    def retry_after(self, key: str) -> float:
        """还需等待的秒数；0 表示当前未被限制（可直接尝试）"""
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return 0.0
            if now - entry.updated_at > self.window:
                # 窗口过期：视为重新开始
                self._entries.pop(key, None)
                return 0.0
            remaining = entry.blocked_until - now
            return remaining if remaining > 0 else 0.0

    # ---------- 记账 ----------

    def record_failure(self, key: str) -> float:
        """记录一次失败，返回本次之后的退避秒数（0 表示尚未触发退避）"""
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or now - entry.updated_at > self.window:
                entry = _Entry()
                self._entries[key] = entry
            entry.fails += 1
            entry.updated_at = now
            if entry.fails <= self.threshold:
                # 未触发退避也要检查容量：攻击者用海量不同 key 各失败 1 次
                # 就能撑爆字典（实测过这个漏法），故驱逐必须放在早退之前
                self._maybe_evict_locked()
                return 0.0
            # 第 threshold+1 次失败 → base；之后每次翻倍
            exponent = entry.fails - self.threshold - 1
            delay = min(self.base_delay * (2**exponent), self.max_delay)
            entry.blocked_until = now + delay
            self._maybe_evict_locked()
            return delay

    def reset(self, key: str) -> None:
        """认证成功：清零该 key 的失败记录"""
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        """清空全部记录（测试用）"""
        with self._lock:
            self._entries.clear()

    # ---------- 内部 ----------

    def _maybe_evict_locked(self) -> None:
        """条目数超限时清理过期项（最旧的先走）；仍在调用方持有锁"""
        if len(self._entries) <= self.max_entries:
            return
        now = self._clock()
        expired = [
            key
            for key, entry in self._entries.items()
            if now - entry.updated_at > self.window and entry.blocked_until <= now
        ]
        for key in expired:
            self._entries.pop(key, None)
        # 仍然超限：按 updated_at 淘汰最旧的一批（保留一半，避免抖动）
        if len(self._entries) > self.max_entries:
            ordered = sorted(self._entries.items(), key=lambda kv: kv[1].updated_at)
            remove_count = len(ordered) - self.max_entries // 2
            for key, _ in ordered[:remove_count]:
                self._entries.pop(key, None)
