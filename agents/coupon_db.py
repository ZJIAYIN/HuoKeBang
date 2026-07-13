"""
CouponDB — 体验券系统 MySQL 异步连接池与 CRUD

职责：
  - 管理 aiomysql 连接池（懒初始化）
  - coupon_order 表：插入、查询、更新状态
  - coupon_lead 表：插入、查询
  - coupon_outbox 表：插入、查询待发送、标记状态

设计原则：
  - 所有写入都在事务中完成
  - coupon_order 是最终判重依据（Redis Lua 只是预检）
  - Outbox 模式保证 RMQ 消息可靠投递
"""
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 默认 MySQL 连接 URL（从环境变量读取）
_DEFAULT_MYSQL_URL = os.getenv(
    "MYSQL_URL",
    "mysql://echomind:echomind123@localhost:3307/echomind",
)


def _parse_mysql_url(url: str) -> dict:
    """解析 MYSQL_URL 为 aiomysql.connect 参数。

    Args:
        url: mysql://user:password@host:port/dbname 格式

    Returns:
        dict: host, port, user, password, db
    """
    remainder = url
    if remainder.startswith("mysql://"):
        remainder = remainder[len("mysql://"):]

    user_password, rest = remainder.split("@", 1)
    user, password = user_password.split(":", 1)

    host_port, db = rest.split("/", 1)
    if ":" in host_port:
        host, port_str = host_port.split(":", 1)
        port = int(port_str)
    else:
        host = host_port
        port = 3306

    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "db": db,
    }


class CouponDB:
    """体验券系统 MySQL 异步连接池与 CRUD。"""

    def __init__(self, mysql_url: Optional[str] = None):
        """初始化 CouponDB。

        Args:
            mysql_url: MySQL 连接 URL，默认从 MYSQL_URL 环境变量读取
        """
        self._url = mysql_url or _DEFAULT_MYSQL_URL
        self._pool: Optional[Any] = None

    # ── 连接池管理 ──────────────────────────────────────────────────────────

    async def connect(self) -> bool:
        """建立连接池。

        Returns:
            是否连接成功
        """
        try:
            import aiomysql

            params = _parse_mysql_url(self._url)
            self._pool = await aiomysql.create_pool(
                host=params["host"],
                port=params["port"],
                user=params["user"],
                password=params["password"],
                db=params["db"],
                charset="utf8mb4",
                autocommit=False,
                maxsize=10,
                minsize=2,
                pool_recycle=3600,
            )
            logger.info(
                f"MySQL 连接池已建立: {params['host']}:{params['port']}/{params['db']}"
            )
            return True
        except ImportError:
            logger.error(
                "aiomysql 未安装，请执行: pip install aiomysql"
            )
            return False
        except Exception as ex:
            logger.warning(f"MySQL 连接池建立失败: {ex}")
            return False

    async def close(self) -> None:
        """关闭连接池。"""
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            logger.info("MySQL 连接池已关闭")

    @property
    def connected(self) -> bool:
        return self._pool is not None and not self._pool._closed

    # ── coupon_order 表 ─────────────────────────────────────────────────────

    async def find_order_by_user_id(
        self, user_id: str, status_list: Optional[List[str]] = None
    ) -> Optional[Dict]:
        """按 user_id 查询券订单（用于 MySQL 兜底判重）。

        Args:
            user_id: 用户 ID
            status_list: 要查询的状态列表，默认 ['claimed', 'lead_submitted']

        Returns:
            订单 dict 或 None
        """
        if status_list is None:
            status_list = ["claimed", "lead_submitted"]

        sql = (
            "SELECT id, user_id, conv_id, status, stock_snapshot, created_at "
            "FROM coupon_order WHERE user_id = %s AND status IN (%s)"
            % ("%s", ",".join(["%s"] * len(status_list)))
        )
        params = [user_id] + status_list

        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                row = await cur.fetchone()
                if row:
                    return {
                        "id": row[0],
                        "user_id": row[1],
                        "conv_id": row[2],
                        "status": row[3],
                        "stock_snapshot": row[4],
                        "created_at": row[5].isoformat() if row[5] else None,
                    }
                return None

    async def find_order_by_id(self, order_id: int) -> Optional[Dict]:
        """按 ID 查询券订单。"""
        sql = (
            "SELECT id, user_id, conv_id, status, stock_snapshot, created_at "
            "FROM coupon_order WHERE id = %s"
        )
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (order_id,))
                row = await cur.fetchone()
                if row:
                    return {
                        "id": row[0],
                        "user_id": row[1],
                        "conv_id": row[2],
                        "status": row[3],
                        "stock_snapshot": row[4],
                        "created_at": row[5].isoformat() if row[5] else None,
                    }
                return None

    async def find_stuck_orders(
        self, minutes: int = 10, limit: int = 100
    ) -> List[Dict]:
        """查询卡在 claimed 状态的超时订单（供对账脚本使用）。

        Args:
            minutes: 超时阈值，created_at 超过 N 分钟视为卡住
            limit: 最多返回条数

        Returns:
            订单 dict 列表
        """
        sql = (
            "SELECT id, user_id, conv_id, status, stock_snapshot, created_at "
            "FROM coupon_order "
            "WHERE status = 'claimed' AND created_at < NOW() - INTERVAL %s MINUTE "
            "ORDER BY id ASC LIMIT %s"
        )
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (minutes, limit))
                rows = await cur.fetchall()
                results = []
                for row in rows:
                    results.append({
                        "id": row[0],
                        "user_id": row[1],
                        "conv_id": row[2],
                        "status": row[3],
                        "stock_snapshot": row[4],
                        "created_at": row[5].isoformat() if row[5] else None,
                    })
                return results

    async def insert_order(
        self, user_id: str, conv_id: str, stock_snapshot: int = 0
    ) -> int:
        """插入券订单。

        Args:
            user_id: 用户 ID
            conv_id: 会话 ID
            stock_snapshot: 领取时的库存快照

        Returns:
            自增 ID
        """
        sql = (
            "INSERT INTO coupon_order (user_id, conv_id, status, stock_snapshot) "
            "VALUES (%s, %s, 'claimed', %s)"
        )
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (user_id, conv_id, stock_snapshot))
                await conn.commit()
                return cur.lastrowid

    async def update_order_status(
        self, order_id: int, new_status: str
    ) -> bool:
        """更新券订单状态。

        Args:
            order_id: 订单 ID
            new_status: 新状态 (lead_submitted|released)

        Returns:
            是否更新成功
        """
        sql = "UPDATE coupon_order SET status = %s WHERE id = %s"
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (new_status, order_id))
                await conn.commit()
                affected = cur.rowcount
                if affected == 0:
                    logger.warning(
                        f"更新订单状态失败: order_id={order_id} 不存在"
                    )
                    return False
                return True

    # ── coupon_lead 表 ─────────────────────────────────────────────────────

    async def insert_lead(
        self, order_id: int, user_id: str, name: str, phone: str, conv_id: str
    ) -> int:
        """插入留资记录。

        Args:
            order_id: 订单 ID
            user_id: 用户 ID
            name: 姓名
            phone: 手机号
            conv_id: 会话 ID

        Returns:
            自增 ID
        """
        sql = (
            "INSERT INTO coupon_lead (order_id, user_id, name, phone, conv_id) "
            "VALUES (%s, %s, %s, %s, %s)"
        )
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (order_id, user_id, name, phone, conv_id))
                await conn.commit()
                return cur.lastrowid

    async def find_lead_by_user_id(self, user_id: str) -> Optional[Dict]:
        """按 user_id 查询留资记录。

        Args:
            user_id: 用户 ID

        Returns:
            留资记录 dict 或 None
        """
        sql = (
            "SELECT id, order_id, user_id, name, phone, conv_id, created_at "
            "FROM coupon_lead WHERE user_id = %s ORDER BY id DESC LIMIT 1"
        )
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (user_id,))
                row = await cur.fetchone()
                if row:
                    return {
                        "id": row[0],
                        "order_id": row[1],
                        "user_id": row[2],
                        "name": row[3],
                        "phone": row[4],
                        "conv_id": row[5],
                        "created_at": row[6].isoformat() if row[6] else None,
                    }
                return None

    async def find_lead_by_order_id(self, order_id: int) -> Optional[Dict]:
        """按 order_id 查询留资记录。"""
        sql = (
            "SELECT id, order_id, user_id, name, phone, conv_id, created_at "
            "FROM coupon_lead WHERE order_id = %s"
        )
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (order_id,))
                row = await cur.fetchone()
                if row:
                    return {
                        "id": row[0],
                        "order_id": row[1],
                        "user_id": row[2],
                        "name": row[3],
                        "phone": row[4],
                        "conv_id": row[5],
                        "created_at": row[6].isoformat() if row[6] else None,
                    }
                return None

    # ── coupon_outbox 表 ────────────────────────────────────────────────────

    async def insert_outbox(
        self,
        order_id: int,
        event_type: str,
        payload: Dict[str, Any],
        max_retries: int = 3,
    ) -> int:
        """插入 outbox 消息。

        Args:
            order_id: 关联的订单 ID
            event_type: 事件类型（如 claim_delay）
            payload: 消息体
            max_retries: 最大重试次数

        Returns:
            自增 ID
        """
        sql = (
            "INSERT INTO coupon_outbox (order_id, event_type, payload, status, max_retries) "
            "VALUES (%s, %s, %s, 'init', %s)"
        )
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    sql,
                    (order_id, event_type, json.dumps(payload, ensure_ascii=False), max_retries),
                )
                await conn.commit()
                return cur.lastrowid

    async def fetch_pending_outbox(
        self, limit: int = 50
    ) -> List[Dict]:
        """查询所有待发送的 outbox 消息。

        Args:
            limit: 最多查询条数

        Returns:
            outbox 消息列表
        """
        sql = (
            "SELECT id, order_id, event_type, payload, status, retry_count, max_retries, "
            "       error_msg, created_at "
            "FROM coupon_outbox "
            "WHERE status = 'init' "
            "ORDER BY id ASC LIMIT %s"
        )
        async with self._pool.acquire() as conn:
            await conn.rollback()  # 清除池子残留事务的 MVCC snapshot
            async with conn.cursor() as cur:
                await cur.execute(sql, (limit,))
                rows = await cur.fetchall()
                results = []
                for row in rows:
                    payload_raw = row[3]
                    if isinstance(payload_raw, str):
                        try:
                            payload = json.loads(payload_raw)
                        except json.JSONDecodeError:
                            payload = {"raw": payload_raw}
                    else:
                        payload = payload_raw
                    results.append({
                        "id": row[0],
                        "order_id": row[1],
                        "event_type": row[2],
                        "payload": payload,
                        "status": row[4],
                        "retry_count": row[5],
                        "max_retries": row[6],
                        "error_msg": row[7],
                        "created_at": row[8].isoformat() if row[8] else None,
                    })
                return results

    async def mark_outbox_sent(self, outbox_id: int) -> bool:
        """标记 outbox 消息为已发送。

        Args:
            outbox_id: outbox 记录 ID

        Returns:
            是否更新成功
        """
        sql = "UPDATE coupon_outbox SET status = 'sent' WHERE id = %s"
        async with self._pool.acquire() as conn:
            await conn.rollback()  # 清除池子残留事务的 MVCC snapshot
            async with conn.cursor() as cur:
                await cur.execute(sql, (outbox_id,))
                await conn.commit()
                return cur.rowcount > 0

    async def mark_outbox_failed(
        self, outbox_id: int, error_msg: str
    ) -> bool:
        """标记 outbox 消息为发送失败。

        Args:
            outbox_id: outbox 记录 ID
            error_msg: 错误信息

        Returns:
            是否更新成功
        """
        sql = (
            "UPDATE coupon_outbox SET status = 'failed', error_msg = %s, "
            "    retry_count = retry_count + 1 "
            "WHERE id = %s"
        )
        async with self._pool.acquire() as conn:
            await conn.rollback()
            async with conn.cursor() as cur:
                await cur.execute(sql, (error_msg[:500], outbox_id))
                await conn.commit()
                return cur.rowcount > 0

    async def mark_outbox_retry(
        self, outbox_id: int, error_msg: str
    ) -> bool:
        """仅递增重试次数 + 记录错误，不下沉 status（下次扫描会重试）。

        Args:
            outbox_id: outbox 记录 ID
            error_msg: 错误信息

        Returns:
            是否更新成功
        """
        sql = (
            "UPDATE coupon_outbox SET error_msg = %s, "
            "    retry_count = retry_count + 1 "
            "WHERE id = %s AND status = 'init'"
        )
        async with self._pool.acquire() as conn:
            await conn.rollback()
            async with conn.cursor() as cur:
                await cur.execute(sql, (error_msg[:500], outbox_id))
                await conn.commit()
                return cur.rowcount > 0

    # ── 事务性操作 ──────────────────────────────────────────────────────────

    async def claim_in_transaction(
        self, user_id: str, conv_id: str, stock_snapshot: int
    ) -> Tuple[bool, int, str]:
        """事务：检查重复 → 插入订单 → 插入 outbox → 提交。

        这是 claim 流程的核心事务，以 MySQL 为最终判重依据。

        Args:
            user_id: 用户 ID
            conv_id: 会话 ID
            stock_snapshot: 领取时的 Redis 库存快照

        Returns:
            (success, order_id, message)
            success=True  → 插入成功，order_id 有效
            success=False → 重复或错误，order_id=0
        """
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                try:
                    # 1. MySQL 兜底判重
                    await cur.execute(
                        "SELECT id FROM coupon_order "
                        "WHERE user_id = %s AND status IN ('claimed', 'lead_submitted')",
                        (user_id,),
                    )
                    existing = await cur.fetchone()
                    if existing:
                        logger.info(
                            f"MySQL 判重: user={user_id} 已有订单 id={existing[0]}"
                        )
                        return False, 0, "duplicate"

                    # 2. 插入订单
                    await cur.execute(
                        "INSERT INTO coupon_order (user_id, conv_id, status, stock_snapshot) "
                        "VALUES (%s, %s, 'claimed', %s)",
                        (user_id, conv_id, stock_snapshot),
                    )
                    order_id = cur.lastrowid

                    # 3. 插入 outbox 消息（含 conv_id 用于补偿通知）
                    await cur.execute(
                        "INSERT INTO coupon_outbox (order_id, event_type, payload, status) "
                        "VALUES (%s, 'claim_delay', %s, 'init')",
                        (
                            order_id,
                            json.dumps(
                                {
                                    "user_id": user_id,
                                    "order_id": order_id,
                                    "conv_id": conv_id,
                                },
                                ensure_ascii=False,
                            ),
                        ),
                    )

                    await conn.commit()
                    logger.info(
                        f"事务提交成功: order_id={order_id} user={user_id}"
                    )
                    return True, order_id, "ok"

                except Exception as ex:
                    await conn.rollback()
                    logger.error(f"事务回滚: user={user_id} error={ex}")
                    return False, 0, f"db_error: {ex}"

    # ── coupon_stock 表 ────────────────────────────────────────────────────

    async def init_stock_in_db(self, coupon_type: str = "test_drive", total: int = 50) -> bool:
        """初始化 coupon_stock（幂等，仅首次生效）。

        Args:
            coupon_type: 券类型
            total: 总库存

        Returns:
            True 表示首次初始化成功，False 表示已存在
        """
        sql = (
            "INSERT IGNORE INTO coupon_stock (coupon_type, total, remaining, version) "
            "VALUES (%s, %s, %s, 0)"
        )
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (coupon_type, total, total))
                await conn.commit()
                affected = cur.rowcount
                if affected:
                    logger.info(f"coupon_stock 已初始化: {coupon_type} total={total}")
                    return True
                logger.info(f"coupon_stock 已存在: {coupon_type}")
                return False

    async def get_stock_from_db(self, coupon_type: str = "test_drive") -> Optional[Dict]:
        """从 MySQL 查询当前库存。

        Args:
            coupon_type: 券类型

        Returns:
            {"total": N, "remaining": N, "version": N} 或 None
        """
        sql = (
            "SELECT total, remaining, version FROM coupon_stock "
            "WHERE coupon_type = %s"
        )
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (coupon_type,))
                row = await cur.fetchone()
                if row:
                    return {"total": row[0], "remaining": row[1], "version": row[2]}
                return None

    async def deduct_stock(
        self, coupon_type: str = "test_drive", quantity: int = 1
    ) -> bool:
        """乐观锁扣减库存。

        仅在 remaining >= quantity 时扣减成功。

        Args:
            coupon_type: 券类型
            quantity: 扣减数量

        Returns:
            是否扣减成功
        """
        for attempt in range(3):
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT remaining, version FROM coupon_stock "
                        "WHERE coupon_type = %s",
                        (coupon_type,),
                    )
                    row = await cur.fetchone()
                    if not row:
                        logger.error(f"扣减失败: {coupon_type} 不存在")
                        return False

                    remaining, version = row
                    if remaining < quantity:
                        logger.warning(
                            f"扣减失败: {coupon_type} 库存不足 "
                            f"(remaining={remaining}, need={quantity})"
                        )
                        return False

                    await cur.execute(
                        "UPDATE coupon_stock SET "
                        "    remaining = remaining - %s, "
                        "    version = version + 1 "
                        "WHERE coupon_type = %s AND version = %s",
                        (quantity, coupon_type, version),
                    )
                    await conn.commit()

                    if cur.rowcount > 0:
                        return True

                    # 乐观锁冲突，重试
                    logger.debug(f"乐观锁冲突，重试: {coupon_type} version={version}")
                    continue

        logger.error(f"乐观锁扣减失败（重试耗尽）: {coupon_type}")
        return False

    async def restore_stock(
        self, coupon_type: str = "test_drive", quantity: int = 1
    ) -> bool:
        """乐观锁归还库存。

        Args:
            coupon_type: 券类型
            quantity: 归还数量

        Returns:
            是否归还成功
        """
        for attempt in range(3):
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT remaining, version FROM coupon_stock "
                        "WHERE coupon_type = %s",
                        (coupon_type,),
                    )
                    row = await cur.fetchone()
                    if not row:
                        logger.error(f"归还失败: {coupon_type} 不存在")
                        return False

                    remaining, version = row

                    await cur.execute(
                        "UPDATE coupon_stock SET "
                        "    remaining = remaining + %s, "
                        "    version = version + 1 "
                        "WHERE coupon_type = %s AND version = %s",
                        (quantity, coupon_type, version),
                    )
                    await conn.commit()

                    if cur.rowcount > 0:
                        return True

                    logger.debug(f"乐观锁冲突，重试归还: {coupon_type} version={version}")
                    continue

        logger.error(f"乐观锁归还失败（重试耗尽）: {coupon_type}")
        return False

    # ── 消费者事务操作 ──────────────────────────────────────────────────────

    async def confirm_lead_and_deduct(
        self, order_id: int, user_id: str, coupon_type: str = "test_drive"
    ) -> Tuple[bool, str]:
        """消费者：确认留资 + 悲观锁扣减库存（同一事务）。

        供 CouponWorker 超时处理时调用：
          有留资记录 → 更新订单状态 + 扣减 coupon_stock

        使用 SELECT ... FOR UPDATE 悲观锁替代乐观锁版本校验，
        避免并发冲突下的自旋重试开销。

        Args:
            order_id: 订单 ID
            user_id: 用户 ID
            coupon_type: 券类型

        Returns:
            (success, message)
        """
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                try:
                    # 1. 更新订单状态
                    await cur.execute(
                        "UPDATE coupon_order SET status = 'lead_submitted' "
                        "WHERE id = %s AND user_id = %s AND status = 'claimed'",
                        (order_id, user_id),
                    )
                    if cur.rowcount == 0:
                        # 可能已更新过
                        await cur.execute(
                            "SELECT status FROM coupon_order WHERE id = %s",
                            (order_id,),
                        )
                        row = await cur.fetchone()
                        if row and row[0] in ("lead_submitted", "released"):
                            await conn.commit()
                            return True, f"already_{row[0]}"
                        await conn.rollback()
                        return False, "order_not_found"

                    # 2. 悲观锁：锁定 coupon_stock 行，避免并发扣减
                    await cur.execute(
                        "SELECT remaining FROM coupon_stock "
                        "WHERE coupon_type = %s FOR UPDATE",
                        (coupon_type,),
                    )
                    stock = await cur.fetchone()
                    if not stock:
                        await conn.rollback()
                        return False, "stock_not_found"

                    remaining = stock[0]
                    if remaining <= 0:
                        await conn.rollback()
                        return False, "stock_exhausted"

                    # 3. 扣减库存（行已被锁定，无需版本校验）
                    await cur.execute(
                        "UPDATE coupon_stock SET "
                        "    remaining = remaining - 1, "
                        "    version = version + 1 "
                        "WHERE coupon_type = %s",
                        (coupon_type,),
                    )

                    await conn.commit()
                    logger.info(
                        f"留资确认+扣减成功（悲观锁）: order_id={order_id} user={user_id} "
                        f"remaining={remaining - 1}"
                    )
                    return True, "ok"

                except Exception as ex:
                    await conn.rollback()
                    logger.error(
                        f"留资确认+扣减事务回滚: order_id={order_id} error={ex}"
                    )
                    return False, f"db_error: {ex}"

    async def release_order_only(
        self, order_id: int, user_id: str
    ) -> Tuple[bool, str]:
        """消费者：超时释放订单（仅更新状态，不碰库存）。

        供 CouponWorker 超时处理时调用：
          无留资记录 → 仅更新订单为 released

        **为什么不能加库存？**
          claim 阶段 MySQL coupon_stock.remaining 从未扣减，
          预扣只在 Redis 层完成（Lua DECR），因此释放时只需
          更新 coupon_order 状态 + Lua 恢复 Redis，不需要
          也不应该归还 MySQL 库存。

        Args:
            order_id: 订单 ID
            user_id: 用户 ID

        Returns:
            (success, message)
        """
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(
                        "UPDATE coupon_order SET status = 'released' "
                        "WHERE id = %s AND user_id = %s AND status = 'claimed'",
                        (order_id, user_id),
                    )
                    if cur.rowcount == 0:
                        await cur.execute(
                            "SELECT status FROM coupon_order WHERE id = %s",
                            (order_id,),
                        )
                        row = await cur.fetchone()
                        if row and row[0] == "released":
                            await conn.commit()
                            return True, "already_released"
                        await conn.rollback()
                        return False, "order_not_found_or_not_claimable"

                    await conn.commit()
                    logger.info(
                        f"订单已释放（无留资，不碰库存）: "
                        f"order_id={order_id} user={user_id}"
                    )
                    return True, "ok"

                except Exception as ex:
                    await conn.rollback()
                    logger.error(
                        f"释放订单事务回滚: order_id={order_id} error={ex}"
                    )
                    return False, f"db_error: {ex}"

    async def release_in_transaction(
        self, order_id: int, user_id: str
    ) -> bool:
        """事务：检查 lead → 更新订单状态为 released。

        Args:
            order_id: 订单 ID
            user_id: 用户 ID

        Returns:
            True=释放成功, False=已留资或不存在
        """
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                try:
                    # 1. 检查订单是否存在
                    await cur.execute(
                        "SELECT id, status FROM coupon_order WHERE id = %s",
                        (order_id,),
                    )
                    order = await cur.fetchone()
                    if not order:
                        logger.warning(f"释放: order_id={order_id} 不存在")
                        return False

                    order_status = order[1]
                    if order_status == "lead_submitted":
                        logger.info(
                            f"释放: order_id={order_id} 已留资，跳过释放"
                        )
                        return False  # 已留资，不释放

                    if order_status == "released":
                        logger.info(
                            f"释放: order_id={order_id} 已释放，幂等跳过"
                        )
                        return True

                    # 2. 检查是否有 lead 记录
                    await cur.execute(
                        "SELECT id FROM coupon_lead WHERE order_id = %s",
                        (order_id,),
                    )
                    lead = await cur.fetchone()
                    if lead:
                        # 有 lead 但状态没更新 → 修复状态
                        await cur.execute(
                            "UPDATE coupon_order SET status = 'lead_submitted' WHERE id = %s",
                            (order_id,),
                        )
                        await conn.commit()
                        logger.info(
                            f"释放: order_id={order_id} 有 lead 记录，跳过释放"
                        )
                        return False

                    # 3. 更新为 released
                    await cur.execute(
                        "UPDATE coupon_order SET status = 'released' WHERE id = %s",
                        (order_id,),
                    )
                    await conn.commit()
                    logger.info(
                        f"释放成功: order_id={order_id} user={user_id}"
                    )
                    return True

                except Exception as ex:
                    await conn.rollback()
                    logger.error(
                        f"释放事务回滚: order_id={order_id} error={ex}"
                    )
                    return False
