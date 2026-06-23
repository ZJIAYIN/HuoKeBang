"""拒绝留资处理 Skill"""
from .base import BaseSkill


class ContactNoSkill(BaseSkill):
    name = "CONTACT_NO"
    required_slots = []       # 不需要任何槽位
    required_tools = []       # 不需要工具
    instruction = (
        "用户表示不方便留联系方式或不感兴趣。你的任务是：\n"
        "1. 表示理解，尊重用户的选择，绝不追问或施压\n"
        "2. 告知用户后续如果有需要可以随时联系我们\n"
        "3. 自然地转向其他话题（如继续回答产品问题），不要停留在留资话题上"
    )
