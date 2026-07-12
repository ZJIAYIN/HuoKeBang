"""
Redis 留资存储（LeadStore）

体验券用户通过前端表单提交联系方式后，调用此模块写入 Redis。
不再参与 Orchestrator 编排流程，仅作为 coupon API 的存储后端。

Key 设计：
  - lead:{user_id}:phone     → 手机号（无 TTL，永久）
  - lead:{user_id}:wechat    → 微信号（无 TTL，永久）
  - lead:{user_id}:name      → 姓名（无 TTL，永久）

Redis 不可用时自动降级为无操作模式，不阻塞主流程。
"""
import json
import logging
from typing import Any, Dict, Optional

import redis

logger = logging.getLogger(__name__)

_REDIS_TIMEOUT = 3


class LeadStore:
    """
    Redis 留资存储。

    Redis 不可用时自动降级为无操作模式，不阻塞主流程。

    用法：
        store = LeadStore()
        store.save_lead("user1", phone="13712345678")
        store.get_phone("user1")       # "13712345678"
    """

    _PREFIX = "lead:"

    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
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

    # ── 写 ──

    def save_lead(self, user_id: str, phone: Optional[str] = None,
                  wechat: Optional[str] = None, name: Optional[str] = None,
                  email: Optional[str] = None) -> None:
        """保存留资信息到 Redis。只写入非 None 的字段。

        Redis 不可用时静默跳过。
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

    def has_lead(self, user_id: str) -> bool:
        """用户是否留下过电话或微信？"""
        if not self._redis:
            return False
        try:
            return bool(self._redis.exists(f"{self._PREFIX}{user_id}:phone"))
        except Exception as ex:
            logger.warning(f"查询留资状态失败: {ex}")
            return False

    def get_phone(self, user_id: str) -> Optional[str]:
        """获取用户手机号。"""
        if not self._redis:
            return None
        try:
            return self._redis.get(f"{self._PREFIX}{user_id}:phone")
        except Exception as ex:
            logger.warning(f"获取手机号失败: {ex}")
            return None

    def get_lead_info(self, user_id: str) -> Dict[str, str]:
        """获取用户所有留资信息。"""
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
        """清除用户所有留资记录（测试/数据更正用）。"""
        if not self._redis:
            return
        try:
            keys = self._redis.keys(f"{self._PREFIX}{user_id}:*")
            if keys:
                self._redis.delete(*keys)
        except Exception as ex:
            logger.warning(f"清除留资记录失败: {ex}")

    @property
    def stats(self) -> Dict[str, Any]:
        """获取留资统计信息。"""
        if not self._redis:
            return {"total_leads": 0}
        try:
            lead_keys = self._redis.keys(f"{self._PREFIX}*:phone")
            return {"total_leads": len(lead_keys)}
        except Exception as ex:
            logger.warning(f"获取留资统计失败: {ex}")
            return {"total_leads": 0}
