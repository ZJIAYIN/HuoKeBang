
"""问候/闲聊 Skill"""
from .base import BaseSkill, Tool


class GreetingSkill(BaseSkill):
    name = "GREETING"
    required_slots = []
    required_tools = []
    instruction = (
        "用户打招呼或闲聊。友好回应，如果用户没有明确需求，"
        "可以主动询问想了解哪方面信息（车型、价格、优惠等）。"
        "保持热情自然，不要直接要联系方式。"
    )
