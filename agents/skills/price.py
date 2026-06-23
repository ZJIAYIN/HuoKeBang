"""价格查询 Skill"""
from .base import BaseSkill, Tool


class PriceSkill(BaseSkill):
    name = "PRICE"
    required_slots = ["model"]
    required_tools = [Tool.RAG]
    instruction = (
        "根据 Knowledge 回答车辆价格、优惠活动、金融方案相关问题。"
        "如果知识库中没有明确价格信息，提供参考区间并建议联系门店获取最新报价。"
        "不要编造不存在的价格或优惠。"
    )
