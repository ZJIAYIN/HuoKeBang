"""
Redis 留资存储（LeadStore）

用 Redis 存留资信息和拒绝时间窗口：
  - lead:{user_id}:phone     → 手机号（无 TTL，永久）
  - lead:{user_id}:wechat    → 微信号（无 TTL，永久）
  - lead:{user_id}:refused   → "1" （TTL=24h，过期后自动解除冷却）

Redis 本身就是跨会话的，所以跨会话冷却、留资持久化全天然解决，
不需要文件 IO 或额外的跨会话注入逻辑。

降级策略：
  - Redis 连接失败 / 超时 → 静默降级，所有写操作不报错，读操作返回默认值
  - 不影响 AgentEngine 的正常启动和运行
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import redis

logger = logging.getLogger(__name__)

# 拒绝留资冷却 TTL（秒）
REFUSAL_TTL = 86400  # 24h

# Redis 连接超时 / 操作超时（秒）
_REDIS_TIMEOUT = 3


class LeadStore:
    """
    Redis 留资存储。

    Redis 不可用时自动降级为无操作模式，不阻塞主流程。

    用法：
        store = LeadStore()
        store.save_lead("user1", phone="13712345678")
        store.record_refusal("user1")
        store.is_in_cooldown("user1")  # True
        store.get_phone("user1")       # "13712345678"
    """

    _PREFIX = "lead:"

    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        """初始化 LeadStore。

        连接失败时静默降级，self._redis 保持 None。

        Args:
            redis_url: Redis 连接 URL
        """
        self._redis: Optional[redis.Redis] = None
        try:
            _redis = redis.from_url(
                redis_url,
                decode_responses=True,
                socket_timeout=_REDIS_TIMEOUT,
                socket_connect_timeout=_REDIS_TIMEOUT,
            )
            _redis.ping()
            self._redis = _redis
            logger.info(f"LeadStore 已连接 Redis: {redis_url}")
        except Exception as ex:
            self._redis = None
            logger.warning(f"LeadStore Redis 不可用，降级为无操作模式: {ex}")

    # ── 写 ──────────────────────────────────────────────────────────────────

    def save_lead(self, user_id: str, phone: Optional[str] = None,
                  wechat: Optional[str] = None, name: Optional[str] = None,
                  email: Optional[str] = None) -> None:
        """保存留资信息到 Redis。只写入非 None 的字段。

        Redis 不可用时静默跳过。

        Args:
            user_id: 用户 ID
            phone: 手机号
            wechat: 微信号
            name: 姓名
            email: 邮箱
        """
        if not self._redis:
            return
        try:
            pipe = self._redis.pipeline()
            if phone:
                pipe.set(f"{self._PREFIX}{user_id}:phone", phone)
            if wechat:
                pipe.set(f"{self._PREFIX}{user_id}:wechat", wechat)
            if name:
                pipe.set(f"{self._PREFIX}{user_id}:name", name)
            if email:
                pipe.set(f"{self._PREFIX}{user_id}:email", email)
            pipe.execute()
            if phone or wechat:
                logger.info(f"留资已保存: user={user_id} phone={'yes' if phone else 'no'} wechat={'yes' if wechat else 'no'}")
        except Exception as ex:
            logger.warning(f"保存留资失败: {ex}")

    def save_lead_from_slots(self, user_id: str, slots: Dict[str, Any]) -> None:
        """从 SlotManager 的 slots dict 提取留资字段写入 Redis。

        存电话/微信时顺手清除冷却标记，避免留了电话还被追着问。

        Args:
            user_id: 用户 ID
            slots: 当前会话槽位字典
        """
        has_contact = bool(slots.get("phone") or slots.get("wechat"))
        self.save_lead(
            user_id=user_id,
            phone=slots.get("phone"),
            wechat=slots.get("wechat"),
            name=slots.get("name"),
            email=slots.get("email"),
        )
        if has_contact and self._redis:
            try:
                self._redis.delete(f"{self._PREFIX}{user_id}:refused")
            except Exception as ex:
                logger.warning(f"清除冷却标记失败: {ex}")

    def record_refusal(self, user_id: str) -> None:
        """记录拒绝留资，TTL=24h。

        TTL 过期后 Redis 自动删除 key，is_in_cooldown 自然返回 False，
        不需要手动清理。

        Args:
            user_id: 用户 ID
        """
        if not self._redis:
            return
        try:
            self._redis.setex(f"{self._PREFIX}{user_id}:refused", REFUSAL_TTL, "1")
            logger.info(f"拒绝留资已记录（TTL=24h）: user={user_id}")
        except Exception as ex:
            logger.warning(f"记录拒绝留资失败: {ex}")

    # ── 读 ──────────────────────────────────────────────────────────────────

    def is_in_cooldown(self, user_id: str) -> bool:
        """用户在冷却窗口内？（24h 内拒绝过留资）

        如果用户已经留过电话，冷却解除（留了电话还冷处理没道理）。
        Redis 不可用时返回 False。

        Args:
            user_id: 用户 ID

        Returns:
            True=用户处于冷却期，不应追问留资
        """
        if not self._redis:
            return False
        try:
            if self.has_lead(user_id):
                return False
            return bool(self._redis.exists(f"{self._PREFIX}{user_id}:refused"))
        except Exception as ex:
            logger.warning(f"查询冷却状态失败: {ex}")
            return False

    def has_lead(self, user_id: str) -> bool:
        """用户是否留下过电话或微信？

        Redis 不可用时返回 False。

        Args:
            user_id: 用户 ID
        """
        if not self._redis:
            return False
        try:
            return bool(self._redis.exists(f"{self._PREFIX}{user_id}:phone"))
        except Exception as ex:
            logger.warning(f"查询留资状态失败: {ex}")
            return False

    def get_phone(self, user_id: str) -> Optional[str]:
        """获取用户手机号。

        Args:
            user_id: 用户 ID

        Returns:
            手机号字符串，不存在或 Redis 不可用时返回 None
        """
        if not self._redis:
            return None
        try:
            return self._redis.get(f"{self._PREFIX}{user_id}:phone")
        except Exception as ex:
            logger.warning(f"获取手机号失败: {ex}")
            return None

    def get_wechat(self, user_id: str) -> Optional[str]:
        """获取用户微信号。

        Args:
            user_id: 用户 ID

        Returns:
            微信号字符串，不存在或 Redis 不可用时返回 None
        """
        if not self._redis:
            return None
        try:
            return self._redis.get(f"{self._PREFIX}{user_id}:wechat")
        except Exception as ex:
            logger.warning(f"获取微信号失败: {ex}")
            return None

    def get_lead_info(self, user_id: str) -> Dict[str, str]:
        """获取用户所有留资信息。

        Args:
            user_id: 用户 ID

        Returns:
            包含 phone/wechat/name/email 的字典
        """
        if not self._redis:
            return {}
        info = {}
        try:
            for field in ("phone", "wechat", "name", "email"):
                val = self._redis.get(f"{self._PREFIX}{user_id}:{field}")
                if val:
                    info[field] = val
        except Exception as ex:
            logger.warning(f"获取留资信息失败: {ex}")
        return info

    def clear_user(self, user_id: str) -> None:
        """清除用户所有留资记录（测试/数据更正用）。

        Args:
            user_id: 用户 ID
        """
        if not self._redis:
            return
        try:
            keys = self._redis.keys(f"{self._PREFIX}{user_id}:*")
            if keys:
                self._redis.delete(*keys)
        except Exception as ex:
            logger.warning(f"清除留资记录失败: {ex}")

    # ── 统计 ────────────────────────────────────────────────────────────────

    @property
    def stats(self) -> Dict[str, Any]:
        """获取留资统计信息。

        Returns:
            包含 total_leads 和 total_in_cooldown 的字典
        """
        if not self._redis:
            return {"total_leads": 0, "total_in_cooldown": 0}
        try:
            lead_keys = self._redis.keys(f"{self._PREFIX}*:phone")
            refused_keys = self._redis.keys(f"{self._PREFIX}*:refused")
            return {
                "total_leads": len(lead_keys),
                "total_in_cooldown": len(refused_keys),
            }
        except Exception as ex:
            logger.warning(f"获取留资统计失败: {ex}")
            return {"total_leads": 0, "total_in_cooldown": 0}
