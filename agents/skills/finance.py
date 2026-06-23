"""金融方案 Skill"""
from .base import BaseSkill, Tool


class FinanceSkill(BaseSkill):
    name = "FINANCE"
    required_slots = ["model", "budget"]
    required_tools = [Tool.RAG]
    instruction = (
        "根据 Knowledge 提供金融分期方案建议。"
        "结合用户的预算给出合理的首付和月供参考。"
        "如果知识库中没有具体金融方案，提供常见分期比例（如30%首付）作为参考。"
    )
