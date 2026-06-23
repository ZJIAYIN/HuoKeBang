"""
Skill 注册与查找（SkillRegistry）

职责：
  - 注册所有 Skill
  - 根据 sub_task 名称快速查找对应的 Skill 类
  - 提供全量列表供 Orchestrator 遍历

设计：
  使用类名查找而非实例，因为 Skill 是纯元数据（Metadata-only），
  不需要实例化。所有信息都是类属性。
"""
import logging
from typing import Dict, List, Optional, Type

from agents.skills.base import BaseSkill
from agents.skills.product import ProductSkill
from agents.skills.price import PriceSkill
from agents.skills.finance import FinanceSkill
from agents.skills.complaint import ComplaintSkill
from agents.skills.lead_capture import LeadCaptureSkill
from agents.skills.greeting import GreetingSkill
from agents.skills.purchase import PurchaseSkill
from agents.skills.contact_no import ContactNoSkill
from agents.skills.weather import WeatherSkill

logger = logging.getLogger(__name__)


class SkillRegistry:
    """
    Skill 注册表。

    用法：
        skill = SkillRegistry.get("PRICE")
        if skill:
            missing = skill.check_slots(slots)
    """

    # 默认注册所有 Skill（key = skill.name，即 sub_task 标识）
    _registry: Dict[str, Type[BaseSkill]] = {}

    @classmethod
    def register(cls, skill_cls: Type[BaseSkill]) -> Type[BaseSkill]:
        """注册一个 Skill 类。"""
        name = skill_cls.name
        if name in cls._registry:
            logger.warning(f"Skill '{name}' 重复注册，将被覆盖")
        cls._registry[name] = skill_cls
        logger.debug(f"Skill 已注册: {name}")
        return skill_cls

    @classmethod
    def get(cls, name: str) -> Optional[Type[BaseSkill]]:
        """根据 sub_task 名称查找 Skill。"""
        return cls._registry.get(name)

    @classmethod
    def all(cls) -> Dict[str, Type[BaseSkill]]:
        """返回所有已注册的 Skill。"""
        return dict(cls._registry)

    @classmethod
    def names(cls) -> List[str]:
        """返回所有已注册的 Skill 名称列表。"""
        return list(cls._registry.keys())

    @classmethod
    def has(cls, name: str) -> bool:
        """检查指定名称的 Skill 是否已注册。"""
        return name in cls._registry

    @classmethod
    def init_defaults(cls) -> None:
        """注册所有内置 Skill。"""
        skills = [
            ProductSkill,
            PriceSkill,
            FinanceSkill,
            ComplaintSkill,
            LeadCaptureSkill,
            GreetingSkill,
            PurchaseSkill,
            ContactNoSkill,
            WeatherSkill,
        ]
        for s in skills:
            cls.register(s)
        logger.info(f"Skill 注册完成: {len(cls._registry)} 个 — {list(cls._registry.keys())}")


# 模块加载时自动注册默认 Skill
SkillRegistry.init_defaults()
