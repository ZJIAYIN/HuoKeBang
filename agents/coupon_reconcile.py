"""
Coupon 对账脚本 — 手动触发

扫描 coupon_order 中卡在 claimed 状态的超时订单，
根据是否已有留资记录，执行补偿操作。

用法:
    python -m agents.coupon_reconcile                    # 执行
    python -m agents.coupon_reconcile --dry-run          # 只展示，不修改
    python -m agents.coupon_reconcile --minutes 30       # 查 30 分钟前的
"""
import argparse
import asyncio
import logging
import os
import sys
from typing import Dict, List, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
for _noisy in ("aiormq", "pamqp", "aio_pika"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logger = logging.getLogger("coupon_reconcile")


async def _reconcile(dry_run: bool, minutes: int) -> None:
    """执行一次对账。

    流程：
      1. 查 coupon_order WHERE status='claimed' AND 超过 N 分钟
      2. 逐条检查 coupon_lead
         ├─ 有留资 → confirm_lead_and_deduct（悲观锁扣库存）
         └─ 无留资 → release_order_only + Lua release（不碰 MySQL 库存）
      3. 打印汇总

    Args:
        dry_run: 仅打印不做实际修改
        minutes: 超时阈值（分钟）
    """
    import redis as _redis_module
    from agents.coupon_db import CouponDB
    from agents.coupon_manager import CouponManager

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    mysql_url = os.getenv("MYSQL_URL", "mysql://echomind:echomind123@localhost:3307/echomind")

    # ── 连接 ────────────────────────────────────────────
    r = _redis_module.from_url(redis_url, decode_responses=True)
    cm = CouponManager(redis_client=r)
    cm.load_scripts()

    db = CouponDB(mysql_url=mysql_url)
    ok = await db.connect()
    if not ok:
        logger.error("MySQL 连接失败，退出")
        return

    logger.info(f"对账扫描: status=claimed 超过 {minutes} 分钟" + (" (DRY RUN)" if dry_run else ""))

    # ── 1. 查询卡住的订单 ────────────────────────────────
    orders = await db.find_stuck_orders(minutes=minutes, limit=200)
    if not orders:
        logger.info("没有发现卡住的订单")
        await db.close()
        return

    logger.info(f"发现 {len(orders)} 条卡住订单")

    stats: Dict[str, int] = {"total": len(orders), "lead_ok": 0, "lead_skip": 0,
                             "release_ok": 0, "release_skip": 0, "error": 0}

    for order in orders:
        order_id = order["id"]
        user_id = order["user_id"]
        conv_id = order["conv_id"]
        created_at = order["created_at"]

        # ── 2. 查是否有留资 ──────────────────────────────
        lead = await db.find_lead_by_order_id(order_id)
        if not lead:
            lead = await db.find_lead_by_user_id(user_id)

        if lead:
            # ── 有留资：确认扣减 ──────────────────────────
            ok, msg = await _reconcile_lead(db, cm, order, dry_run)
            if ok:
                stats["lead_ok"] += 1
            else:
                stats["lead_skip" if msg and "already" in msg else "error"] += 1
        else:
            # ── 无留资：释放 ──────────────────────────────
            ok, msg = await _reconcile_release(db, cm, order, dry_run)
            if ok:
                stats["release_ok"] += 1
            else:
                stats["release_skip" if msg and "already" in msg else "error"] += 1

    # ── 汇总 ────────────────────────────────────────────
    logger.info("=" * 50)
    logger.info(f"对账完成{' (DRY RUN)' if dry_run else ''}")
    logger.info(f"  总计:     {stats['total']}")
    logger.info(f"  有留资:   已确认 {stats['lead_ok']}  跳过 {stats['lead_skip']}")
    logger.info(f"  无留资:   已释放 {stats['release_ok']}  跳过 {stats['release_skip']}")
    logger.info(f"  异常:     {stats['error']}")

    r.close()
    await db.close()


async def _reconcile_lead(
    db: 'CouponDB', cm: 'CouponManager', order: Dict, dry_run: bool
) -> Tuple[bool, str]:
    """有留资：确认订单 + 扣减库存。"""
    order_id = order["id"]
    user_id = order["user_id"]

    logger.info(f"  [{order_id}] 有留资 → 确认扣减: user={user_id}")

    if dry_run:
        return True, "dry_run"

    ok, msg = await db.confirm_lead_and_deduct(order_id=order_id, user_id=user_id)
    if ok:
        cm.set_lead_submitted(user_id, "{}")
        logger.info(f"  [{order_id}] ✅ 确认+扣减成功")
    else:
        logger.warning(f"  [{order_id}] ⚠️ {msg}")

    return ok, msg


async def _reconcile_release(
    db: 'CouponDB', cm: 'CouponManager', order: Dict, dry_run: bool
) -> Tuple[bool, str]:
    """无留资：释放订单 + Lua 清理 Redis。"""
    order_id = order["id"]
    user_id = order["user_id"]

    logger.info(f"  [{order_id}] 无留资 → 释放: user={user_id}")

    if dry_run:
        return True, "dry_run"

    # MySQL：仅更新订单状态（不碰库存）
    ok, msg = await db.release_order_only(order_id=order_id, user_id=user_id)
    if not ok:
        logger.warning(f"  [{order_id}] ⚠️ MySQL 释放失败: {msg}")
        return False, msg

    # Lua：清理 Redis
    result = cm.release(user_id)
    status = result.get("status")

    if status == "released":
        logger.info(f"  [{order_id}] ✅ 释放+Redis 清理成功 stock={result.get('stock')}")
        return True, "ok"
    elif status == "already_released":
        logger.info(f"  [{order_id}] ⏭️ Redis 已释放（幂等跳过）")
        return True, "already_released"
    else:
        logger.warning(f"  [{order_id}] ⚠️ Redis 清理异常: {result}")
        return False, status


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Coupon 对账脚本")
    parser.add_argument("--dry-run", action="store_true", help="仅展示，不做实际修改")
    parser.add_argument("--minutes", type=int, default=10,
                        help="超时阈值（分钟），默认 10")
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    await _reconcile(dry_run=args.dry_run, minutes=args.minutes)


if __name__ == "__main__":
    asyncio.run(_main())
