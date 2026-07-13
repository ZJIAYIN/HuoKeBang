"""
CouponManager — 体验券库存与状态管理（Redis + MySQL 混合架构）

架构：
  ┌──────────────────────────────────────────────────────────────┐
  │  POST /coupon/claim                                          │
  │                                                              │
  │  ① Lua 脚本（Redis）                                         │
  │     ├── 一人一单：claimed:<user_id> 存在性检查                 │
  │     ├── 不超卖：GET stock → 如果 <=0 返回 sold_out            │
  │     ├── DECR stock（预扣）                                    │
  │     └── SET claimed:<user_id>=conv_id EX TTL                 │
  │                                                              │
  │  ② MySQL 兜底判重（同一事务）                                  │
  │     ├── SELECT 1 FROM coupon_order                           │
  │     │   WHERE user_id=? AND status IN ('claimed','lead_submitted') │
  │     │   → 查到则回滚 Redis（Lua 脚本补库存 + DEL claimed）      │
  │     │   → 返回前端 duplicate                                  │
  │     │                                                         │
  │     ├── 未查到 → INSERT coupon_order (status='claimed')       │
  │     ├── INSERT coupon_outbox (status='init')                  │
  │     └── COMMIT                                                │
  └──────────────────────────────────────────────────────────────┘

职责：
  - 加载并执行 Lua 脚本（原子扣减 / 释放 / 回滚）
  - 库存查询与初始化
  - 冷却管理（24h 不重复发券检查）
  - 留资标记管理
  - MySQL 事务性操作（判重 + 插入订单 + outbox）
"""
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Redis Key 前缀
_COUPON_PREFIX = "coupon:test_drive"
_STOCK_KEY      = f"{_COUPON_PREFIX}:stock"
_CLAIMED_PREFIX = f"{_COUPON_PREFIX}:claimed"    # :<user_id>
_LEAD_PREFIX    = f"{_COUPON_PREFIX}:lead"       # :<user_id>
_COOLDOWN_PREFIX = f"{_COUPON_PREFIX}:cooldown"  # :<user_id> — 前端拒绝后的冷却

# 默认库存
_DEFAULT_STOCK = 50

# 默认 claimed TTL（秒）
_CLAIM_TTL_SEC = 3600  # 1h — 超时后由 RMQ DLQ 接管

# 留资标记 TTL（秒）
_LEAD_TTL_SEC = 86400 * 30  # 30 天

# 冷却 TTL（秒）
_COOLDOWN_TTL_SEC = 86400  # 24h

# Lua 脚本路径
_LUA_DIR = Path(__file__).parent.parent / "data"


class CouponManager:
    """体验券库存与状态管理（Redis + MySQL 混合架构）。"""

    def __init__(self, redis_client, coupon_db=None, rmq_client=None):
        """初始化 CouponManager。

        Args:
            redis_client: Redis 连接实例（decode_responses=True）
            coupon_db: 可选 CouponDB 实例（用于 MySQL 持久化）
            rmq_client: 可选 RmqClient 实例（直接发送时用，推荐用 OutboxScanner）
        """
        self._redis = redis_client
        self._db = coupon_db
        self._rmq = rmq_client
        self._sha_claim = None          # coupon_claim.lua SHA
        self._sha_release = None        # coupon_release.lua SHA
        self._sha_rollback = None       # coupon_claim_rollback.lua SHA

    # ── 初始化 ──────────────────────────────────────────────────────────────────

    def load_scripts(self) -> None:
        """加载 Lua 脚本到 Redis，缓存 SHA。"""
        claim_path = _LUA_DIR / "coupon_claim.lua"
        release_path = _LUA_DIR / "coupon_release.lua"
        rollback_path = _LUA_DIR / "coupon_claim_rollback.lua"

        if claim_path.exists():
            with open(claim_path, "r", encoding="utf-8") as f:
                self._sha_claim = self._redis.script_load(f.read())
            logger.info("Lua 脚本已加载: coupon_claim.lua")
        else:
            logger.warning(f"Lua 脚本不存在: {claim_path}")

        if release_path.exists():
            with open(release_path, "r", encoding="utf-8") as f:
                self._sha_release = self._redis.script_load(f.read())
            logger.info("Lua 脚本已加载: coupon_release.lua")
        else:
            logger.warning(f"Lua 脚本不存在: {release_path}")

        if rollback_path.exists():
            with open(rollback_path, "r", encoding="utf-8") as f:
                self._sha_rollback = self._redis.script_load(f.read())
            logger.info("Lua 脚本已加载: coupon_claim_rollback.lua")
        else:
            logger.warning(f"Lua 脚本不存在: {rollback_path}")

    async def init_stock(self, stock: int = _DEFAULT_STOCK) -> bool:
        """初始化库存。

        优先从 MySQL coupon_stock 读取（权威数据源），
        如果 MySQL 不可用或不存在，则回退 Redis SETNX。

        Args:
            stock: 初始库存数量（MySQL 和 Redis 均不存在时使用）

        Returns:
            True 表示初始化成功，False 表示已有库存
        """
        # 1. 优先从 MySQL 读取
        if self._db and self._db.connected:
            try:
                db_stock = await self._db.get_stock_from_db()
                if db_stock:
                    remaining = db_stock["remaining"]
                    self._redis.set(_STOCK_KEY, remaining)
                    logger.info(
                        f"库存已从 MySQL 同步: remaining={remaining} "
                        f"(total={db_stock['total']})"
                    )
                    return True
            except Exception as ex:
                logger.warning(f"从 MySQL 读取库存失败: {ex}")

        # 2. 首次初始化 MySQL
        if self._db and self._db.connected:
            try:
                await self._db.init_stock_in_db(total=stock)
            except Exception as ex:
                logger.warning(f"MySQL coupon_stock 初始化失败: {ex}")

        # 3. Redis SETNX（兼容纯 Redis 模式）
        ok = self._redis.setnx(_STOCK_KEY, stock)
        if ok:
            logger.info(f"体验券库存已初始化: {stock} (Redis)")
        else:
            logger.info(f"体验券库存已存在: {self._redis.get(_STOCK_KEY)} (Redis)")
        return bool(ok)

    def set_stock(self, remaining: int) -> None:
        """强制设置 Redis 库存值（从 MySQL 同步时调用）。

        Args:
            remaining: 剩余库存
        """
        self._redis.set(_STOCK_KEY, remaining)
        logger.debug(f"Redis 库存已设置: {remaining}")

    async def sync_stock_from_db(self) -> bool:
        """从 MySQL coupon_stock 同步库存到 Redis。

        Returns:
            是否同步成功
        """
        if not self._db or not self._db.connected:
            return False
        try:
            stock = await self._db.get_stock_from_db()
            if stock:
                self._redis.set(_STOCK_KEY, stock["remaining"])
                logger.info(
                    f"库存已从 MySQL 同步: remaining={stock['remaining']}"
                )
                return True
            logger.warning("sync_stock_from_db: MySQL 无库存记录")
            return False
        except Exception as ex:
            logger.error(f"sync_stock_from_db 失败: {ex}")
            return False

    # ── 核心操作 ────────────────────────────────────────────────────────────────

    async def claim(self, user_id: str, conv_id: str) -> Dict:
        """原子领取体验券（Redis + MySQL 混合事务）。

        流程：
          1. Lua 脚本（Redis）：快速预检 + 预扣库存
          2. MySQL 事务：兜底判重 → 插入订单 + outbox
          3. MySQL 失败 → Lua 回滚脚本撤销 Redis

        Args:
            user_id: 用户 ID
            conv_id: 会话 ID

        Returns:
            {"status": "ok"|"duplicate"|"sold_out"|"error", "stock": N, "order_id": N}
        """
        # ── 0. 冷却检查 ────────────────────────────────────
        if self.check_cooldown(user_id):
            return {
                "status": "cooldown",
                "message": "24h 冷却中",
                "stock": self.get_stock(),
            }

        # ── 1. Lua 脚本（Redis 层快速校验） ─────────────────
        lua_result = self._lua_claim(user_id, conv_id)
        if lua_result.get("status") != "ok":
            return lua_result

        stock_after = lua_result.get("stock", 0)

        # 无 DB → 纯 Redis 模式，直接返回成功
        if not self._db or not self._db.connected:
            logger.warning(f"MySQL 不可用，使用纯 Redis 模式: user={user_id}")
            return {"status": "ok", "stock": stock_after, "order_id": 0}

        # ── 2. MySQL 事务兜底判重 ──────────────────────────
        db_ok, order_id, message = await self._db.claim_in_transaction(
            user_id=user_id,
            conv_id=conv_id,
            stock_snapshot=stock_after,
        )

        if not db_ok:
            if message == "duplicate":
                # MySQL 查到已有订单 → 回滚 Redis
                self._lua_rollback(user_id)
                logger.info(
                    f"MySQL 判重，Redis 已回滚: user={user_id}"
                )
                return {
                    "status": "duplicate",
                    "message": "已经领取过体验券",
                    "stock": self.get_stock(),
                }
            else:
                # DB 异常 → 回滚 Redis
                self._lua_rollback(user_id)
                logger.error(
                    f"DB 写入失败，Redis 已回滚: user={user_id} error={message}"
                )
                return {
                    "status": "error",
                    "message": "系统繁忙，请稍后再试",
                    "stock": self.get_stock(),
                }

        logger.info(
            f"领取成功: user={user_id} order_id={order_id} "
            f"stock={stock_after}"
        )

        return {
            "status": "ok",
            "stock": stock_after,
            "order_id": order_id,
        }

    def _lua_claim(self, user_id: str, conv_id: str) -> Dict:
        """执行 Lua claim 脚本（Redis 层快速校验）。

        Args:
            user_id: 用户 ID
            conv_id: 会话 ID

        Returns:
            {"status": "ok"|"duplicate"|"sold_out"|"error", "stock": N}
        """
        if not self._sha_claim:
            logger.error("_lua_claim: Lua 脚本未加载")
            return {"status": "error", "message": "脚本未加载"}

        claim_key = f"{_CLAIMED_PREFIX}:{user_id}"

        try:
            raw = self._redis.evalsha(
                self._sha_claim,
                2,  # KEYS 数量
                _STOCK_KEY,
                claim_key,
                user_id,
                conv_id,
                str(_CLAIM_TTL_SEC),
            )
            result = json.loads(raw)
            logger.debug(
                f"_lua_claim: user={user_id} → {result.get('status')} "
                f"(stock={result.get('stock')})"
            )
            return result
        except Exception as ex:
            logger.error(f"_lua_claim 执行失败: {ex}")
            return {"status": "error", "message": str(ex)}

    def _lua_rollback(self, user_id: str) -> Dict:
        """执行 Lua rollback 脚本（撤销 Redis 预扣）。

        Args:
            user_id: 用户 ID

        Returns:
            {"status": "rolled_back"|"error", "stock": N}
        """
        if not self._sha_rollback:
            logger.error("_lua_rollback: Lua 脚本未加载")
            return {"status": "error", "message": "脚本未加载"}

        claim_key = f"{_CLAIMED_PREFIX}:{user_id}"

        try:
            raw = self._redis.evalsha(
                self._sha_rollback,
                2,  # KEYS 数量
                _STOCK_KEY,
                claim_key,
            )
            result = json.loads(raw)
            logger.info(
                f"Redis 回滚成功: user={user_id} "
                f"stock={result.get('stock')}"
            )
            return result
        except Exception as ex:
            logger.error(f"_lua_rollback 执行失败: {ex}")
            return {"status": "error", "message": str(ex)}

    def release(self, user_id: str) -> Dict:
        """释放体验券（超时/取消）。

        通过 Lua 脚本释放 Redis 中的 claimed 标记和库存。
        注意：已留资的跳过逻辑由 CouponWorker 查 MySQL 决定，
        Lua 脚本只做简单的 DEL + INCR。

        Args:
            user_id: 用户 ID

        Returns:
            {"status": "released"|"error", "stock": N}
        """
        if not self._sha_release:
            logger.error("release: Lua 脚本未加载")
            return {"status": "error", "message": "脚本未加载"}

        claim_key = f"{_CLAIMED_PREFIX}:{user_id}"

        try:
            raw = self._redis.evalsha(
                self._sha_release,
                2,  # KEYS 数量
                _STOCK_KEY,
                claim_key,
                user_id,
            )
            result = json.loads(raw)
            logger.info(
                f"release: user={user_id} → {result.get('status')} "
                f"(stock={result.get('stock')})"
            )
            return result
        except Exception as ex:
            logger.error(f"release Lua 执行失败: {ex}")
            return {"status": "error", "message": str(ex)}

    # ── 查询 ────────────────────────────────────────────────────────────────────

    def get_stock(self) -> int:
        """查询当前剩余库存。"""
        val = self._redis.get(_STOCK_KEY)
        return int(val) if val is not None else 0

    def check_claimed(self, user_id: str) -> bool:
        """检查用户是否已领取。"""
        return bool(self._redis.exists(f"{_CLAIMED_PREFIX}:{user_id}"))

    def check_lead_submitted(self, user_id: str) -> bool:
        """检查用户是否已提交留资表单。"""
        return bool(self._redis.exists(f"{_LEAD_PREFIX}:{user_id}"))

    def check_cooldown(self, user_id: str) -> bool:
        """检查用户是否处于 24h 冷却（前端拒绝后标记）。"""
        return bool(self._redis.exists(f"{_COOLDOWN_PREFIX}:{user_id}"))

    # ── 留资管理 ────────────────────────────────────────────────────────────────

    def set_lead_submitted(self, user_id: str, lead_data: str = "") -> bool:
        """标记用户已提交留资（Redis 缓存层）。

        MySQL 中的实际写入由 API handler 完成。
        Redis 标记用于快速查询。

        Args:
            user_id: 用户 ID
            lead_data: 留资内容（JSON 字符串）

        Returns:
            是否设置成功
        """
        try:
            self._redis.set(
                f"{_LEAD_PREFIX}:{user_id}", lead_data, ex=_LEAD_TTL_SEC
            )
            logger.info(f"留资 Redis 标记已设置: user={user_id}")
            return True
        except Exception as ex:
            logger.error(f"设置留资 Redis 标记失败: {ex}")
            return False

    def get_lead_data(self, user_id: str) -> Optional[str]:
        """获取用户的留资数据（Redis 缓存）。"""
        val = self._redis.get(f"{_LEAD_PREFIX}:{user_id}")
        return val if val else None

    def set_cooldown(self, user_id: str) -> bool:
        """设置用户 24h 冷却（前端拒绝后调用）。"""
        try:
            self._redis.set(f"{_COOLDOWN_PREFIX}:{user_id}", "1", ex=_COOLDOWN_TTL_SEC)
            logger.info(f"冷却标记已设置: user={user_id} (24h)")
            return True
        except Exception as ex:
            logger.error(f"设置冷却标记失败: {ex}")
            return False

    # ── 管理 ────────────────────────────────────────────────────────────────────

    def reset_stock(self, stock: int = _DEFAULT_STOCK) -> bool:
        """强制重置库存（管理接口用）。"""
        try:
            self._redis.set(_STOCK_KEY, stock)
            logger.info(f"库存已强制重置: {stock}")
            return True
        except Exception as ex:
            logger.error(f"重置库存失败: {ex}")
            return False

    def stats(self) -> Dict:
        """返回当前体验券统计信息。"""
        stock = self.get_stock()
        return {
            "stock": stock,
            "claimed_count": len(self._redis.keys(f"{_CLAIMED_PREFIX}:*") or []),
            "lead_count": len(self._redis.keys(f"{_LEAD_PREFIX}:*") or []),
        }
