"""
Redis 留资存储（LeadStore）

用 Redis 存留资信息和拒绝时间窗口：
  - lead:{user_id}:phone     → 手机号（无 TTL，永久）
  - lead:{user_id}:wechat    → 微信号（无 TTL，永久）
  - lead:{user_id}:refused   → "1" （TTL=24h，过期后自动解除冷却）

Redis 本身就是跨会话的，所以跨会话冷却、留资持久化全天然解决，
不需要文件 IO 或额外的跨会话注入逻辑。
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import redis

logger = logging.getLogger(__name__)

# 拒绝留资冷却 TTL（秒）
REFUSAL_TTL = 86400  # 24h


class LeadStore:
    """
    Redis 留资存储。

    用法：
        store = LeadStore()
        store.save_lead("user1", phone="13712345678")
        store.record_refusal("user1")
        store.is_in_cooldown("user1")  # True
        store.get_phone("user1")       # "13712345678"
    """

    _PREFIX = "lead:"

    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        self._redis = redis.from_url(redis_url, decode_responses=True)
        logger.info(f"LeadStore 已连接 Redis: {redis_url}")

    # ── 写 ──────────────────────────────────────────────────────────────────

    def save_lead(self, user_id: str, phone: Optional[str] = None,
                  wechat: Optional[str] = None, name: Optional[str] = None,
                  email: Optional[str] = None) -> None:
        """保存留资信息到 Redis。只写入非 None 的字段。"""
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

    def save_lead_from_slots(self, user_id: str, slots: Dict[str, Any]) -> None:
        """从 SlotManager 的 slots dict 提取留资字段写入 Redis。"""
        self.save_lead(
            user_id=user_id,
            phone=slots.get("phone"),
            wechat=slots.get("wechat"),
            name=slots.get("name"),
            email=slots.get("email"),
        )

    def record_refusal(self, user_id: str) -> None:
        """
        记录拒绝留资，TTL=24h。

        TTL 过期后 Redis 自动删除 key，is_in_cooldown 自然返回 False，
        不需要手动清理。
        """
        self._redis.setex(f"{self._PREFIX}{user_id}:refused", REFUSAL_TTL, "1")
        logger.info(f"拒绝留资已记录（TTL=24h）: user={user_id}")

    # ── 读 ──────────────────────────────────────────────────────────────────

    def is_in_cooldown(self, user_id: str) -> bool:
        """
        用户在冷却窗口内？（24h 内拒绝过留资）

        如果用户已经留过电话，冷却解除（留了电话还冷处理没道理）。
        """
        if self.has_lead(user_id):
            return False
        return bool(self._redis.exists(f"{self._PREFIX}{user_id}:refused"))

    def has_lead(self, user_id: str) -> bool:
        """用户是否留下过电话或微信？"""
        return bool(self._redis.exists(f"{self._PREFIX}{user_id}:phone"))

    def get_phone(self, user_id: str) -> Optional[str]:
        return self._redis.get(f"{self._PREFIX}{user_id}:phone")

    def get_wechat(self, user_id: str) -> Optional[str]:
        return self._redis.get(f"{self._PREFIX}{user_id}:wechat")

    def get_lead_info(self, user_id: str) -> Dict[str, str]:
        """获取用户所有留资信息。"""
        info = {}
        for field in ("phone", "wechat", "name", "email"):
            val = self._redis.get(f"{self._PREFIX}{user_id}:{field}")
            if val:
                info[field] = val
        return info

    def clear_user(self, user_id: str) -> None:
        """清除用户所有留资记录（测试/数据更正用）。"""
        keys = self._redis.keys(f"{self._PREFIX}{user_id}:*")
        if keys:
            self._redis.delete(*keys)

    # ── 统计 ────────────────────────────────────────────────────────────────

    @property
    def stats(self) -> Dict[str, Any]:
        lead_keys = self._redis.keys(f"{self._PREFIX}*:phone")
        refused_keys = self._redis.keys(f"{self._PREFIX}*:refused")
        return {
            "total_leads": len(lead_keys),
            "total_in_cooldown": len(refused_keys),
        }
