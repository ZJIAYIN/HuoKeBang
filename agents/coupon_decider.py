"""
试驾体验券发放决策引擎（CouponDecider）

职责：
  判断当前对话是否满足触发条件，决定是否发放体验券。

触发规则：
  1. 轮数 > 5（用户已进行多轮有意义的对话）
  2. 用户意图包含 PRICE 或 PRODUCT（表现出购买意向）
  3. 用户情绪为 positive（情绪好，发券转化率高）

用法：
    decider = CouponDecider()
    result = decider.should_issue(
        round_count=6,
        emotion="positive",
        sub_tasks=["PRICE", "PRODUCT"],
        user_id="u123",
    )
    # → CouponDecision(issue=True, reason="轮数>5 + PRICE/PRODUCT + 情绪好")
"""
import logging
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger(__name__)

# 触发条件常量
MIN_ROUNDS = 2                      # 最少对话轮数（第 3 轮触发，history 已截断为 5 条）
REQUIRED_INTENTS = {"PRICE", "PRODUCT"}  # 需要的意图集合（至少包含其一）
ALLOWED_EMOTIONS = {"positive", "neutral"}  # 允许发券的情绪（positive 最佳）


@dataclass
class CouponDecision:
    """CouponDecider 的决策结果"""
    issue: bool = False             # 是否发券
    reason: str = ""                # 决策原因（用于日志/调试）


class CouponDecider:
    """试驾体验券发放决策引擎。"""

    def __init__(self):
        self._enabled = True

    # ── 主入口 ──────────────────────────────────────────────────────────────────

    def should_issue(
        self,
        round_count: int,
        emotion: str,
        sub_tasks: List[str],
        user_id: str = "",
    ) -> CouponDecision:
        """判断是否应当发放试驾体验券。

        Args:
            round_count: 当前对话轮数（第几轮）
            emotion: 用户情绪（planner 输出）
            sub_tasks: 用户意图列表（planner 输出）
            user_id: 用户 ID（用于日志追踪）

        Returns:
            CouponDecision 决策结果
        """
        if not self._enabled:
            return CouponDecision(issue=False, reason="CouponDecider 未启用")

        reasons: List[str] = []

        # 1. 轮数检查
        if round_count <= MIN_ROUNDS:
            return CouponDecision(
                issue=False,
                reason=f"轮数={round_count} ≤ {MIN_ROUNDS}，未达发券门槛",
            )
        reasons.append(f"轮数={round_count}>{MIN_ROUNDS}")

        # 2. 意图检查：至少包含 PRICE 或 PRODUCT
        sub_set = {s.upper() for s in sub_tasks}
        matched_intents = sub_set & REQUIRED_INTENTS
        if not matched_intents:
            return CouponDecision(
                issue=False,
                reason=f"意图={sub_tasks} 不含 PRICE/PRODUCT，不发券",
            )
        reasons.append(f"意图含 {matched_intents}")

        # 3. 情绪检查
        emotion_lower = emotion.lower().strip()
        if emotion_lower not in ALLOWED_EMOTIONS:
            return CouponDecision(
                issue=False,
                reason=f"情绪={emotion} 非 positive/neutral，不发券",
            )
        reasons.append(f"情绪={emotion}")

        # 全部条件满足 → 发券
        reason_str = " + ".join(reasons)
        logger.info(
            f"CouponDecider 决策: 发券 | user={user_id} | {reason_str}"
        )

        return CouponDecision(
            issue=True,
            reason=reason_str,
        )

    # ── 开关 ────────────────────────────────────────────────────────────────────

    def enable(self) -> None:
        """启用发券决策。"""
        self._enabled = True
        logger.info("CouponDecider 已启用")

    def disable(self) -> None:
        """停用发券决策。"""
        self._enabled = False
        logger.info("CouponDecider 已停用")
