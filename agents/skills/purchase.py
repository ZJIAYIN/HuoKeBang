"""购买意向 Skill"""
from .base import BaseSkill, Tool


class PurchaseSkill(BaseSkill):
    name = "PURCHASE"
    required_slots = []       # 不需要前置槽位，用户可以什么都不说就表达购买意向
    required_tools = [Tool.RAG]
    instruction = (
        "用户表达了购买意向。你的任务是：\n"
        "1. 热情确认用户的购买意向，表达欢迎\n"
        "2. 如果用户已提到车型（model），介绍该车型的核心优势并引导下单\n"
        "3. 如果用户未提到具体车型，主动询问预算、用途等偏好，推荐合适车型\n"
        "4. 结合 Knowledge 中的产品信息进行推荐，不编造\n"
        "5. 在对话中自然引导留资（如果 LEAD_CAPTURE 任务在待办中，配合追问联系方式）"
    )
