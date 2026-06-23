"""产品查询 Skill"""
from .base import BaseSkill, Tool


class ProductSkill(BaseSkill):
    name = "PRODUCT"
    required_slots = ["model"]
    required_tools = [Tool.RAG]
    instruction = (
        "根据 Knowledge 回答用户关于产品配置、功能、特点的问题。"
        "如果知识库中没有明确答案，坦诚告知用户暂不掌握该信息，不要编造。"
        "回答时突出车型的核心卖点和差异化优势。"
    )
