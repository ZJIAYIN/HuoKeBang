"""
限流层核心组件。

三层递进限流，从粗到细逐层拦截：
  1. 全局令牌桶（TokenBucket）—— 控制系统总入口 QPS，内存实现
  2. 用户维度频控（UserRateLimiter）—— 同用户 30s 内最多 N 条，Redis 实现
  3. 同请求去重（RequestDedup）—— 同用户同内容正在处理时不重复调 LLM

所有组件在依赖不可用时静默降级放行，不影响主流程。
"""
import asyncio
import hashlib
import logging
import time as _time
from typing import Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 全局令牌桶（内存实现，零外部依赖）
# ═══════════════════════════════════════════════════════════════════════════════

class TokenBucket:
    """令牌桶全局限流器。

    内存实现，不依赖 Redis，毫秒级判断。
    支持突发（burst）和稳定速率（rate）两维控制。

    用法：
        bucket = TokenBucket(rate=20, capacity=10)
        if bucket.consume():
            # 放行
        else:
            # 429
    """

    def __init__(self, rate: float = 20.0, capacity: int = 10):
        """初始化令牌桶。

        Args:
            rate: 每秒补充的令牌数（稳定速率）
            capacity: 桶容量（最大突发量）
        """
        self._rate = rate
        self._capacity = capacity
        self._tokens = float(capacity)
        self._last = _time.monotonic()

    def consume(self, tokens: int = 1) -> bool:
        """消耗 tokens 个令牌。

        Args:
            tokens: 本次消耗的令牌数，默认 1

        Returns:
            True=允许通过，False=被限流
        """
        now = _time.monotonic()
        elapsed = now - self._last
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last = now

        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False

    @property
    def stats(self) -> dict:
        """获取令牌桶状态（调试用）。"""
        return {
            "rate": self._rate,
            "capacity": self._capacity,
            "tokens": round(self._tokens, 1),
            "available_pct": round(self._tokens / self._capacity * 100, 1) if self._capacity else 0,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 用户维度频控（Redis Sorted Set 滑动窗口）
# ═══════════════════════════════════════════════════════════════════════════════

class UserRateLimiter:
    """单用户频控（滑动窗口）。

    基于 Redis Sorted Set，score = 时间戳。
    每次请求到来时：
      1. 删除窗口外的旧记录（zremrangebyscore）
      2. 统计窗口内记录数（zcard）
      3. 未超限则写入当前记录（zadd）
    窗口是真正滑动的，不会出现固定窗口的边界双倍放行问题。

    Redis 不可用时降级放行所有用户。
    """

    def __init__(self, redis_client, limit: int = 5, window: int = 30):
        """初始化用户频控。

        Args:
            redis_client: Redis 连接（None 时放行所有）
            limit: 滑动窗口内允许的最大请求数
            window: 时间窗口（秒）
        """
        self._redis = redis_client
        self._limit = limit
        self._window = window

    def is_allowed(self, user_id: str) -> bool:
        """检查用户是否被限流（滑动窗口）。

        用 zcount 统计窗口内请求数，未超限则写入当前请求。
        旧记录不需要主动清理，由 TTL 过期自动回收。

        Args:
            user_id: 用户 ID

        Returns:
            True=允许通过
        """
        if not self._redis:
            return True  # Redis 不可用时降级放行

        key = f"ratelimit:sliding:{user_id}"
        now = _time.time()

        try:
            # 统计窗口内记录数（zcount O(log N)，不删除旧数据）
            count = self._redis.zcount(key, now - self._window, now)
            if count >= self._limit:
                return False

            # 写入当前请求，TTL 自动过期清理
            self._redis.zadd(key, {str(now): now})
            self._redis.expire(key, self._window)
            return True

        except Exception as ex:
            logger.warning(f"用户频控查询失败，降级放行: {ex}")
            return True


# ═══════════════════════════════════════════════════════════════════════════════
# 3. 同请求去重（Redis SETNX + TTL）
# ═══════════════════════════════════════════════════════════════════════════════

class RequestDedup:
    """请求去重器。

    基于 Redis SETNX，当相同 user_id + message_hash 正在处理时，
    后续请求直接拒绝不再重复调 LLM。

    TTL 自动过期，不会死锁。
    Redis 不可用时降级放行所有请求。
    """

    def __init__(self, redis_client, ttl: int = 15):
        """初始化请求去重。

        Args:
            redis_client: Redis 连接（None 时放行所有）
            ttl: 去重锁超时秒数，应大于单次 LLM 调用最大耗时
        """
        self._redis = redis_client
        self._ttl = ttl

    def try_acquire(self, user_id: str, msg_hash: str) -> bool:
        """尝试获取请求锁。

        Args:
            user_id: 用户 ID
            msg_hash: 消息的 MD5 哈希

        Returns:
            True=获得锁（继续处理），False=重复请求（拒绝）
        """
        if not self._redis:
            return True  # Redis 不可用时降级放行

        key = f"dedup:{user_id}:{msg_hash}"
        try:
            ok = self._redis.setnx(key, "1")
            if ok:
                self._redis.expire(key, self._ttl)
            return bool(ok)
        except Exception as ex:
            logger.warning(f"去重检查失败，降级放行: {ex}")
            return True

    def release(self, user_id: str, msg_hash: str) -> None:
        """释放请求锁（请求完成时调用）。

        Args:
            user_id: 用户 ID
            msg_hash: 消息的 MD5 哈希
        """
        if not self._redis:
            return
        key = f"dedup:{user_id}:{msg_hash}"
        try:
            self._redis.delete(key)
        except Exception as ex:
            logger.warning(f"去重锁释放失败: {ex}")

    @staticmethod
    def hash_message(message: str) -> str:
        """对消息内容做 MD5 哈希。

        Args:
            message: 用户消息

        Returns:
            MD5 十六进制字符串
        """
        return hashlib.md5(message.encode("utf-8")).hexdigest()
