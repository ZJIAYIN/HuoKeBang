"""联系方式修改 Skill

用户想要更改/更新已留的联系方式（电话、微信等）。
新号码通过 Planner 的 slot_ops 以 SET 写入，自动覆盖旧值。
"""
from .base import BaseSkill


class ContactFixSkill(BaseSkill):
    name = "CONTACT_FIX"
    required_slots = ["phone"]    # 必须有新手机号才能执行
    required_tools = []
    instruction = (
        "用户想要修改联系方式（已提供了新手机号）。你的任务是：\n"
        "1. 确认用户的新手机号已更新\n"
        "2. 告知用户后续会使用新联系方式联系\n"
        "3. 如果用户还提供了新微信号，一并确认"
    )
