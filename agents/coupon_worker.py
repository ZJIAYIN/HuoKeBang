"""
CouponWorker — 体验券超时释放后台消费者

职责：
  1. 消费 coupon.timeout 死信队列（接收 TTL 到期的消息）
  2. 查 MySQL 确认用户是否已留资
  3. 已留资 → 悲观锁扣减 MySQL coupon_stock + 更新订单为 lead_submitted
  4. 未留资 → 仅更新订单为 released + Lua 恢复 Redis 库存（不碰 MySQL 库存）
  5. 发送补偿通知给用户（系统消息写入记忆）

库存设计要点：
  - claim 阶段：Redis Lua DECR 预扣库存，MySQL coupon_stock 从未扣减
  - 超时有留资：第一次也是唯一一次扣减 MySQL coupon_stock，用悲观锁防并发
  - 超时无留资：不操作 MySQL coupon_stock，只 Lua INCR 恢复 Redis 库存

运行方式：
  - 在 api/main.py 的 lifespan 中启动后台任务
  - 或单独运行: python -m agents.coupon_worker

依赖：
  - RabbitMQ (aio-pika)
  - Redis (redis-py)
  - MySQL (aiomysql) — 可选，降级为纯 Redis 模式
"""
import asyncio
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class CouponWorker:
    """体验券超时释放后台消费者。"""

    def __init__(self, coupon_manager, coupon_db=None, memory_manager=None):
        """初始化 CouponWorker。

        Args:
            coupon_manager: CouponManager 实例（用于执行 Lua 释放）
            coupon_db: 可选 CouponDB 实例（用于 MySQL 留资检查）
            memory_manager: 可选 MemoryManager 实例（用于发送补偿通知）
        """
        self._coupon_manager = coupon_manager
        self._db = coupon_db
        self._memory_manager = memory_manager
        self._task: Optional[asyncio.Task] = None

    # ── 启动/停止 ──────────────────────────────────────────────────────────────

    def start(self, rmq_client) -> None:
        """启动后台消费者任务。

        Args:
            rmq_client: 已连接的 RmqClient 实例
        """
        if self._task and not self._task.done():
            logger.warning("CouponWorker 已在运行")
            return

        self._task = asyncio.create_task(self._run(rmq_client))
        logger.info("CouponWorker 后台任务已创建")

    async def stop(self) -> None:
        """停止后台消费者。"""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            logger.info("CouponWorker 已停止")

    # ── 核心循环 ──────────────────────────────────────────────────────────────

    async def _run(self, rmq_client) -> None:
        """启动 RMQ 消费者（持续运行）。"""
        try:
            await rmq_client.consume_timeout(self._handle_timeout)
            # 保持协程存活
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            logger.info("CouponWorker 被取消")
        except Exception as ex:
            logger.error(f"CouponWorker 异常退出: {ex}")

    # ── 超时处理 ──────────────────────────────────────────────────────────────

    async def _handle_timeout(self, body: dict) -> None:
        """处理超时消息：检查留资 → 确认/释放 → 扣减/归还 Redis → 补偿通知。

        消费端核心逻辑：
          从 coupon_lead 扫描判断用户是否已留资
          ├─ 有留资 → 事务：更新 coupon_order→lead_submitted
          │            + 悲观锁（SELECT…FOR UPDATE）扣减 coupon_stock -1
          │            → 设 Redis lead 标记
          └─ 无留资 → 事务：更新 coupon_order→released
                       → Lua 清理 Redis 标记（DEL claimed + INCR stock）
                       → 补偿通知

        注意：
          - MySQL coupon_stock 在 claim 阶段从未扣减，因此无留资时
            绝不操作 MySQL 库存，只由 Lua 恢复 Redis 库存
          - 有留资时使用悲观锁而非乐观锁，避免并发自旋重试

        Args:
            body: {"user_id": "...", "order_id": N, "conv_id": "..."}
        """
        user_id = body.get("user_id", "")
        order_id = body.get("order_id", 0)
        conv_id = body.get("conv_id", "")

        if not user_id:
            logger.warning("超时消息缺少 user_id")
            return

        logger.info(
            f"超时处理: user={user_id} order_id={order_id} conv={conv_id}"
        )

        # ── 1. 检查 MySQL 是否有 lead 记录 ────────────────
        has_lead = await self._check_lead(user_id, order_id)

        if has_lead:
            # ── 有留资：确认 + 扣减库存（事务） ──────────
            await self._handle_with_lead(user_id, order_id, conv_id)
        else:
            # ── 无留资：释放订单 + 归还库存 + Lua 清理 ──
            await self._handle_without_lead(user_id, order_id, conv_id)

    async def _handle_with_lead(
        self, user_id: str, order_id: int, conv_id: str
    ) -> None:
        """有留资：事务内确认订单 + 悲观锁扣减 coupon_stock。"""
        logger.info(
            f"用户已留资，确认并扣减库存: user={user_id} order_id={order_id}"
        )

        if not self._db or not self._db.connected:
            # 仅设 Redis 标记（MySQL 不可用时降级）
            self._coupon_manager.set_lead_submitted(user_id, "{}")
            logger.info(
                f"MySQL 不可用，仅完成 Redis 标记: user={user_id}"
            )
            return

        # 事务：更新订单 + 悲观锁扣减 coupon_stock
        ok, msg = await self._db.confirm_lead_and_deduct(
            order_id=order_id,
            user_id=user_id,
        )

        if ok:
            # MySQL 成功后设置 Redis 标记（MySQL 是权威数据源）
            self._coupon_manager.set_lead_submitted(user_id, "{}")
            logger.info(
                f"留资确认+库存扣减成功: user={user_id} order_id={order_id}"
            )
            # Redis stock 在 claim 阶段已由 Lua DECR 设对，
            # MySQL 扣减只是对齐，无需再 sync 覆盖
        else:
            logger.warning(
                f"留资确认+库存扣减失败: user={user_id} order_id={order_id} "
                f"reason={msg}"
            )
            # 此时不设 Redis 标记，后续超时重试仍可正确处理

    async def _handle_without_lead(
        self, user_id: str, order_id: int, conv_id: str
    ) -> None:
        """无留资：仅释放订单 + Lua 清理 Redis，不碰 MySQL 库存。

        **为什么不归还 MySQL 库存？**
          claim 阶段 MySQL coupon_stock.remaining 从未扣减，
          预扣只在 Redis 层（Lua DECR），所以释放只需：
            MySQL: 更新 coupon_order.status = 'released'
            Redis: Lua release（DEL claimed + INCR stock）

        Args:
            user_id: 用户 ID
            order_id: 订单 ID
            conv_id: 会话 ID
        """
        logger.info(
            f"用户未留资，释放订单: user={user_id} order_id={order_id}"
        )

        # ── 1. MySQL：仅更新订单状态（不碰库存） ────────────
        if order_id and self._db and self._db.connected:
            ok, msg = await self._db.release_order_only(
                order_id=order_id,
                user_id=user_id,
            )
            if ok:
                logger.info(
                    f"MySQL 订单已释放: user={user_id} order_id={order_id}"
                )
            else:
                logger.warning(
                    f"MySQL 释放订单失败: user={user_id} "
                    f"order_id={order_id} reason={msg}"
                )
                # 竞态保护：DB 操作失败时重新检查 lead
                lead = await self._db.find_lead_by_user_id(user_id)
                if lead:
                    logger.info(
                        f"竞态检测: 用户已留资，跳过释放: user={user_id}"
                    )
                    self._coupon_manager.set_lead_submitted(user_id, "{}")
                    return

        # ── 2. Lua 清理 Redis（DEL claimed + INCR stock 还原） ──
        release_result = self._coupon_manager.release(user_id)
        status = release_result.get("status")

        if status == "released":
            logger.info(
                f"Redis 标记已清理，库存已归还: user={user_id} "
                f"stock={release_result.get('stock')}"
            )
            # 3. 发送补偿通知
            await self._notify_timeout(user_id, conv_id)
        elif status == "already_released":
            logger.info(
                f"幂等跳过（重复消息）: user={user_id} "
                f"stock={release_result.get('stock')}"
            )
        else:
            logger.warning(
                f"Redis 清理异常: user={user_id} result={release_result}"
            )

    async def _check_lead(self, user_id: str, order_id: int) -> bool:
        """综合检查用户是否已提交留资。

        优先查 MySQL，降级查 Redis。

        Args:
            user_id: 用户 ID
            order_id: 订单 ID

        Returns:
            是否已留资
        """
        # 先查 MySQL
        if self._db and self._db.connected:
            return await self._check_lead_direct(user_id, order_id)

        # 降级：查 Redis 缓存标记
        return self._coupon_manager.check_lead_submitted(user_id)

    async def _check_lead_direct(self, user_id: str, order_id: int) -> bool:
        """直接查询 MySQL 是否有 lead 记录。"""
        if order_id:
            lead = await self._db.find_lead_by_order_id(order_id)
            if lead:
                return True

        if user_id:
            lead = await self._db.find_lead_by_user_id(user_id)
            if lead:
                return True

        return False

    # ── 补偿通知 ──────────────────────────────────────────────────────────────

    async def _notify_timeout(self, user_id: str, conv_id: str) -> None:
        """向用户发送体验券超时释放的系统消息。

        通过 MemoryManager 写入一条 assistant 消息，
        用户下次打开对话时会看到。
        """
        if not self._memory_manager or not conv_id:
            return

        try:
            from memory.conversation_memory import MsgRole

            notice = (
                "⏰ 您的试驾体验券已超时释放。"
                "如果您仍感兴趣，可以继续向我了解更多车型信息，"
                "我会再次为您发放体验券！"
            )

            await self._memory_manager.add_message(
                user_id=user_id,
                conv_id=conv_id,
                role=MsgRole.ASSISTANT,
                content=notice,
            )
            logger.info(f"补偿通知已发送: user={user_id} conv={conv_id}")
        except Exception as ex:
            logger.warning(f"发送补偿通知失败: {ex}")


# ═══════════════════════════════════════════════════════════════════════════════
# 独立运行入口（用于调试/测试）
# ═══════════════════════════════════════════════════════════════════════════════

async def _main():
    """独立运行 CouponWorker（不依赖 FastAPI）。

    用法:
        python -m agents.coupon_worker

    需要环境变量:
        REDIS_URL, RABBITMQ_URL
    """
    logging.basicConfig(level=logging.INFO)
    for _noisy in ("aiormq", "pamqp", "aio_pika"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)

    import redis as _redis_module
    from agents.coupon_manager import CouponManager
    from agents.rmq_client import RmqClient
    from agents.coupon_db import CouponDB

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    r = _redis_module.from_url(redis_url, decode_responses=True)

    cm = CouponManager(redis_client=r)
    cm.load_scripts()

    db = CouponDB()
    await db.connect()

    rmq = RmqClient()
    ok = await rmq.connect()
    if not ok:
        logger.error("RabbitMQ 连接失败，退出")
        return

    worker = CouponWorker(coupon_manager=cm, coupon_db=db)
    worker.start(rmq)

    logger.info("CouponWorker 独立运行中...")
    try:
        await asyncio.Future()  # 永久运行
    except KeyboardInterrupt:
        await worker.stop()
        await rmq.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(_main())
