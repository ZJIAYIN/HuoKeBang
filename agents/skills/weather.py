"""
天气查询 Skill。

查询指定地点的当前天气和未来预报。
依赖 Tool.WEATHER 调用第三方天气 API（wttr.in）。
"""
from .base import BaseSkill, Tool


class WeatherSkill(BaseSkill):
    """天气查询 Skill — 根据用户提供的城市名查询天气。"""

    name = "WEATHER"
    required_slots = ["location"]
    required_tools = [Tool.WEATHER]
    instruction = (
        "根据【天气信息】中的内容回答用户当地的天气情况。"
        "包括当前温度、天气状况、湿度、风向风速等。"
        "如果有未来预报，可以顺带提及。"
    )
