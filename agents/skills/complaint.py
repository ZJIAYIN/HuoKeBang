"""投诉处理 Skill"""
from typing import Dict, Any, List

from .base import BaseSkill, Tool


class ComplaintSkill(BaseSkill):
    name = "COMPLAINT"
    required_slots = ["issue"]
    required_tools = []       # RAG 可选：需要查政策时由 Orchestrator 统一调度
    instruction = (
        "用户有投诉或不满。先真诚道歉，安抚用户情绪。"
        "不要推销产品或引导留资。"
        "如果用户提到具体问题，说明会将情况反馈给相关团队。"
        "保持耐心和同理心。"
    )

    @classmethod
    def check_slots(cls, slots: Dict[str, Any]) -> List[str]:
        """投诉 Skill 没有强制槽位要求——用户可能只说我要投诉"""
        return []  # issue 不是强制的，没有也不影响执行
