"""留资引导 Skill"""
from datetime import datetime, timedelta
from typing import Any, Dict

from .base import BaseSkill, Tool


# 拒绝留资冷却时间窗口（默认 24 小时内不再追问）
LEAD_COOLDOWN_HOURS = 24


class LeadCaptureSkill(BaseSkill):
    name = "LEAD_CAPTURE"
    required_slots = ["phone"]
    required_tools = []
    instruction = (
        "用户提供了联系方式，确认并感谢。"
        "告知用户稍后会有顾问联系。"
        "如果用户没有主动提供但情绪良好，可以自然引导留资。"
        "如果用户拒绝，不要纠缠，表示理解。"
    )

    @classmethod
    def check_emotion(cls, emotion: str) -> bool:
        """
        情绪差时不追问留资。

        受情绪影响的 Skill 只有这一个——其他 Skill 不受 emotion 限制。
        """
        blocked = {"angry", "very_negative", "negative", "skeptical"}
        return emotion.lower() not in blocked

    @classmethod
    def _is_lead_refused(cls, slots: Dict[str, Any]) -> bool:
        """
        检查用户在冷却期内是否拒绝过留资。

        支持两种格式：
          - lead_refused = True（当前会话拒绝过，Planner 输出）
          - lead_refused_at = ISO 时间戳（精确时间窗口，由代码设置）

        返回 True 表示用户最近拒绝过，不应该追问。
        """
        # 简单布尔值 → 当前会话拒绝过
        if slots.get("lead_refused") is True:
            return True

        # 时间戳格式 → 检查是否在冷却窗口内
        raw = slots.get("lead_refused_at")
        if raw:
            try:
                if isinstance(raw, str):
                    refused_at = datetime.fromisoformat(raw)
                elif isinstance(raw, (int, float)):
                    refused_at = datetime.fromtimestamp(raw)
                else:
                    refused_at = raw
                elapsed = datetime.now() - refused_at
                if elapsed < timedelta(hours=LEAD_COOLDOWN_HOURS):
                    return True
                # 超过冷却期 → 清除标记（返回 False 允许再次询问）
                return False
            except (ValueError, TypeError):
                return True  # 格式异常时保守处理：不追问

        return False

    @classmethod
    def can_execute(cls, slots: Dict[str, Any], emotion: str = "") -> bool:
        """
        留资 Skill 有特殊的执行逻辑：
        - 用户已给 phone → 可以执行（确认并感谢）
        - 用户没给 phone → 检查拒绝留资标记 + 情绪
        """
        if not cls.check_emotion(emotion):
            return False
        # 有 phone → 可以执行（即使用户之前拒绝过，这次主动给了也要处理）
        if slots.get("phone"):
            return True
        # 没 phone，检查用户是否在冷却期内拒绝过
        if cls._is_lead_refused(slots):
            return False
        # 没 phone，情绪好，没有拒绝标记 → 可以追问留资
        return True
