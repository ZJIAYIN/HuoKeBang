"""
OutboxScanner — 体验券本地消息表扫描器

职责：
  1. 后台线程定时扫描 coupon_outbox 表中 status='init' 的记录
  2. 将消息发送到 RabbitMQ coupon.delay 队列（TTL=60s）
  3. 发送成功 → UPDATE status='sent'
  4. 发送失败 → 重试，超限 → UPDATE status='failed' 报警

设计原则：
  - 使用 Outbox 模式保证 RMQ 消息可靠投递
  - 每次最多扫描 50 条，避免锁表
  - 扫描间隔 2s，兼顾实时性与 DB 压力
"""
import asyncio
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 默认扫描间隔（秒）
_SCAN_INTERVAL_SEC = 2


class OutboxScanner:
    """本地消息表扫描器。

    定时扫描 coupon_outbox 表，将 status='init' 的消息发送到 RMQ。
    """

    def __init__(self, coupon_db, rmq_client):
        """初始化 OutboxScanner。

        Args:
            coupon_db: CouponDB 实例（用于查询和更新 outbox）
            rmq_client: RmqClient 实例（用于发送消息）
        """
        self._db = coupon_db
        self._rmq = rmq_client
        self._task: Optional[asyncio.Task] = None

    # ── 启动/停止 ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """启动后台扫描任务。"""
        if self._task and not self._task.done():
            logger.warning("OutboxScanner 已在运行")
            return

        self._task = asyncio.create_task(self._run_loop())
        logger.info("OutboxScanner 后台任务已创建")

    async def stop(self) -> None:
        """停止后台扫描任务。"""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            logger.info("OutboxScanner 已停止")

    # ── 核心循环 ──────────────────────────────────────────────────────────

    async def _run_loop(self) -> None:
        """定时扫描循环。"""
        logger.info(f"OutboxScanner 扫描间隔: {_SCAN_INTERVAL_SEC}s")
        while True:
            try:
                await self._scan()
            except asyncio.CancelledError:
                break
            except Exception as ex:
                logger.error(f"OutboxScanner 扫描异常: {ex}")

            await asyncio.sleep(_SCAN_INTERVAL_SEC)

        logger.info("OutboxScanner 循环已退出")

    async def _scan(self) -> None:
        """执行一次扫描：查询待发送消息 → 发送到 RMQ → 更新状态。"""
        if not self._db.connected:
            logger.debug("OutboxScanner: MySQL 未连接，跳过扫描")
            return

        if not self._rmq.connected:
            logger.debug("OutboxScanner: RMQ 未连接，跳过扫描")
            return

        # 1. 查询待发送消息
        records = await self._db.fetch_pending_outbox(limit=50)
        if not records:
            return

        logger.info(f"OutboxScanner: 发现 {len(records)} 条待发送消息")

        for record in records:
            await self._process_record(record)

    async def _process_record(self, record: dict) -> None:
        """处理单条 outbox 记录。

        Args:
            record: outbox 记录 dict（含 id, order_id, event_type, payload 等）
        """
        outbox_id = record["id"]
        event_type = record.get("event_type", "")
        payload = record.get("payload", {})
        retry_count = record.get("retry_count", 0)
        max_retries = record.get("max_retries", 3)

        # 1. 发送到 RMQ
        success = await self._rmq.publish_delay(payload)

        if success:
            # 2a. 标记已发送
            marked = await self._db.mark_outbox_sent(outbox_id)
            logger.info(
                f"Outbox 消息已发送: id={outbox_id} "
                f"event={event_type} order_id={payload.get('order_id')} "
                f"marked={marked}"
            )
        else:
            # 2b. 重试或标记失败
            if retry_count + 1 >= max_retries:
                error_msg = f"发送失败超过上限 ({max_retries} 次)"
                await self._db.mark_outbox_failed(outbox_id, error_msg)
                logger.error(
                    f"Outbox 消息发送失败（已达上限）: "
                    f"id={outbox_id} retry={retry_count}"
                )
            else:
                # 仅记录错误，不下沉状态（下次扫描会重试）
                await self._db.mark_outbox_retry(
                    outbox_id,
                    f"发送失败 (retry={retry_count + 1})",
                )
                logger.warning(
                    f"Outbox 消息发送失败（将重试）: "
                    f"id={outbox_id} retry={retry_count}"
                )


# ── 独立测试入口 ─────────────────────────────────────────────

async def _test_scan():
    """手动执行一次 _scan，方便断点调试。

    用法:
        python -m agents.outbox_scanner
    """
    import redis as _redis_module
    from agents.coupon_db import CouponDB
    from agents.rmq_client import RmqClient

    logging.basicConfig(level=logging.DEBUG)
    for _noisy in ("aiormq", "pamqp", "aio_pika"):
        logging.getLogger(_noisy).setLevel(logging.WARNING)

    db = CouponDB()
    ok = await db.connect()
    if not ok:
        print("MySQL 连接失败")
        return

    rmq = RmqClient()
    ok = await rmq.connect()
    if not ok:
        print("RMQ 连接失败")
        return

    scanner = OutboxScanner(coupon_db=db, rmq_client=rmq)
    await scanner._scan()  # <-- 在这里断点

    await db.close()
    await rmq.close()


if __name__ == "__main__":
    import pathlib, sys
    _ROOT = str(pathlib.Path(__file__).parent.parent.resolve())
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)
    asyncio.run(_test_scan())
