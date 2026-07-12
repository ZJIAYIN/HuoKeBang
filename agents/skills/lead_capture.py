"""留资引导 Skill"""
from datetime import datetime, timedelta
from typing import Any, Dict

from .base import BaseSkill, Tool


# 拒绝留资冷却时间窗口（默认 24 小时内不再追问）
LEAD_COOLDOWN_HOURS = 24


class LeadCaptureSkill(BaseSkill):
    """留资引导 Skill：引导用户留资或确认已留联系方式。"""

    name = "LEAD_CAPTURE"
    required_slots = ["phone"]
    required_tools = [Tool.PHONE_VALIDATE]
    auto_evaluate = True

    # instruction 只处理"确认"场景。
    # "引导留资"场景由框架自动处理——phone 缺失时进入 pending(missing=phone)，
    # LLM 在回复末尾自然追问。
    instruction = (
        "用户提供了联系方式，确认并感谢。"
        "告知用户稍后会有顾问联系。"
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
        执行条件：情绪 OK + 不在冷却期 + phone 已提供。

        四种结果（对应 get_pending_info 诊断）：
        - 情绪差          → False（pending：情绪不适合）
        - 拒绝冷却期内    → False（静默跳过）
        - phone 缺失      → False（pending：缺少 phone → LLM 追问）
        - phone 存在      → True（completed：确认联系方式）
        """
        if not cls.check_emotion(emotion):
            return False
        if cls._is_lead_refused(slots):
            return False
        # phone 检查走默认 check_slots（required_slots=["phone"]）
        return len(cls.check_slots(slots)) == 0

    @classmethod
    def get_pending_info(cls, slots: Dict[str, Any], emotion: str) -> Dict[str, Any]:
        """
        覆盖基类：将 _is_lead_refused 放在 check_slots 之前判断，
        避免"拒绝留资但 phone 也缺失"时被错误诊断为"缺少 phone"。
        """
        if not cls.check_emotion(emotion):
            return {"reason": f"情绪 '{emotion}' 不适合执行此任务"}
        if cls._is_lead_refused(slots):
            return {"silent": True}
        if not slots.get("phone"):
            return {"missing": ["phone"]}
        return {"silent": True}
