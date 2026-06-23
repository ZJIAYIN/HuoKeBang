"""
会话级 Slot 状态管理器（Slot Manager）

支持两种后端：
  - Redis Hash（跨会话持久化，带 7 天 TTL）
  - 内存 dict（无 Redis 时的降级方案）

职责：
  - 维护会话级别的槽位状态
  - 接收 Planner 输出的 slot_ops（增量 Diff），执行确定性 SET / DELETE 合并
  - 不支持智能合并——LLM 说怎么改就怎么改，不做推论

Slot Manager 是纯代码，不做任何 LLM 调用。
属于编排层（Orchestration Layer），与 Skill Orchestrator 配合使用。
"""
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Redis Hash 的 TTL（秒）
_SLOT_TTL = 604800  # 7 天


class SlotOpType(Enum):
    """槽位操作类型"""
    SET    = "SET"     # 设置/覆盖槽位值
    DELETE = "DELETE"  # 删除槽位（用户明确取消）


@dataclass
class SlotOp:
    """单条槽位操作"""
    op:    SlotOpType
    slot:  str
    value: Any = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"op": self.op.value, "slot": self.slot}
        if self.value is not None:
            d["value"] = self.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SlotOp":
        return cls(
            op=SlotOpType(d["op"]),
            slot=d["slot"],
            value=d.get("value"),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# SlotManager — 带 Redis 后端的槽位管理器
# ═══════════════════════════════════════════════════════════════════════════════

class SlotManager:
    """
    会话级槽位状态管理器。

    用法：
        sm = SlotManager(redis_client=redis, redis_key="slot:conv1")
        sm.apply([SlotOp("SET", "model", "M8")])
        sm.get("model")  # → "M8"
        sm.all          # → {"model": "M8"}

    当 redis_client 为 None 时退化为内存存储。
    """

    def __init__(self, redis_client=None, redis_key: str = ""):
        """
        参数：
            redis_client: Redis 连接（可选），提供时读写 Redis Hash
            redis_key:    Redis Hash key，如 "slot:conv123"
        """
        self._redis = redis_client
        self._redis_key = redis_key
        self._key_prefix = "slot:"
        # 无 Redis 时的内存降级
        self._slots: Dict[str, Any] = {}

    # ── 序列化辅助 ─────────────────────────────────────────────────────

    @staticmethod
    def _serialize(val: Any) -> str:
        """将 Python 值序列化为 Redis 可存的安全字符串。"""
        if val is None:
            return ""
        if isinstance(val, (str, int, float, bool)):
            return json.dumps(val, ensure_ascii=False)
        return json.dumps(val, ensure_ascii=False)

    @staticmethod
    def _deserialize(val: Any) -> Any:
        """从 Redis 读出后反序列化为 Python 值。"""
        if val is None:
            return None
        if isinstance(val, bytes):
            val = val.decode("utf-8")
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError, ValueError):
            return val

    # ── 写操作 ─────────────────────────────────────────────────────────

    def apply(self, ops: List[SlotOp]) -> int:
        """
        应用一组槽位操作。

        Redis 模式：HSET / HDEL + 刷新 TTL
        内存模式：直接操作 _slots dict
        """
        if self._redis and self._redis_key:
            return self._apply_redis(ops)
        return self._apply_memory(ops)

    def _apply_redis(self, ops: List[SlotOp]) -> int:
        """Redis Hash 实现。"""
        pipe = self._redis.pipeline()
        changes = 0
        for op in ops:
            if op.op == SlotOpType.SET:
                pipe.hset(self._redis_key, op.slot, self._serialize(op.value))
                changes += 1
                logger.debug(f"Slot SET (Redis): {op.slot} = {op.value!r}")
            elif op.op == SlotOpType.DELETE:
                pipe.hdel(self._redis_key, op.slot)
                changes += 1
                logger.debug(f"Slot DELETE (Redis): {op.slot}")
        pipe.expire(self._redis_key, _SLOT_TTL)
        pipe.execute()
        return changes

    def _apply_memory(self, ops: List[SlotOp]) -> int:
        """内存 dict 实现（原逻辑）。"""
        changed = 0
        for op in ops:
            if op.op == SlotOpType.SET:
                old = self._slots.get(op.slot)
                if old != op.value:
                    self._slots[op.slot] = op.value
                    changed += 1
                    logger.debug(f"Slot SET: {op.slot} = {op.value!r}")
            elif op.op == SlotOpType.DELETE:
                if op.slot in self._slots:
                    del self._slots[op.slot]
                    changed += 1
                    logger.debug(f"Slot DELETE: {op.slot}")
            else:
                logger.warning(f"未知 SlotOp 类型: {op.op}")
        return changed

    def set(self, key: str, value: Any) -> None:
        """便捷方法：设置单个槽位。"""
        self.apply([SlotOp(op=SlotOpType.SET, slot=key, value=value)])

    def delete(self, key: str) -> None:
        """便捷方法：删除单个槽位。"""
        self.apply([SlotOp(op=SlotOpType.DELETE, slot=key)])

    def reset(self) -> None:
        """清空所有槽位。"""
        if self._redis and self._redis_key:
            self._redis.delete(self._redis_key)
            logger.debug(f"Slot 已清空 (Redis): {self._redis_key}")
        else:
            self._slots.clear()
            logger.debug("Slot 已清空")

    # ── 读操作 ─────────────────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        """读取单个槽位值。"""
        if self._redis and self._redis_key:
            val = self._redis.hget(self._redis_key, key)
            return self._deserialize(val) if val is not None else default
        return self._slots.get(key, default)

    def has(self, key: str) -> bool:
        """检查槽位是否存在且不为 None。"""
        if self._redis and self._redis_key:
            return bool(self._redis.hexists(self._redis_key, key))
        return self._slots.get(key) is not None

    def missing(self, *keys: str) -> List[str]:
        """返回缺失的槽位列表。"""
        return [k for k in keys if not self.has(k)]

    @property
    def all(self) -> Dict[str, Any]:
        """返回当前所有槽位的快照。"""
        if self._redis and self._redis_key:
            raw = self._redis.hgetall(self._redis_key)
            return {
                k.decode("utf-8") if isinstance(k, bytes) else k: self._deserialize(v)
                for k, v in raw.items()
            }
        return dict(self._slots)

    # ── 序列化 / 反序列化 ──────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        return {"slots": self.all}

    @classmethod
    def from_dict(cls, data: Dict[str, Any], redis_client=None, redis_key: str = "") -> "SlotManager":
        sm = cls(redis_client=redis_client, redis_key=redis_key)
        slots = data.get("slots", {})
        if redis_client and redis_key:
            # 初始数据批量写入 Redis
            if slots:
                pipe = redis_client.pipeline()
                for k, v in slots.items():
                    pipe.hset(redis_key, k, cls._serialize(v))
                pipe.expire(redis_key, _SLOT_TTL)
                pipe.execute()
        else:
            sm._slots = dict(slots)
        return sm

    # ── 监控 ───────────────────────────────────────────────────────────

    @property
    def stats(self) -> Dict[str, Any]:
        slots = self.all
        return {
            "slot_count": len(slots),
            "keys": list(slots.keys()),
            "backend": "redis" if (self._redis and self._redis_key) else "memory",
        }

    def snapshot(self) -> str:
        """人类可读的槽位快照（用于调试/日志）。"""
        return json.dumps(self.all, ensure_ascii=False, default=str)
