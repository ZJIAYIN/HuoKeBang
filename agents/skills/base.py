"""
Skill 元数据基类。

Skill 在架构中的角色是纯元数据（Metadata-only），没有 execute() 方法。
真正的执行者是 Response Agent（LLM）。

一个 Skill 告诉 Orchestrator 三件事：
  1. required_slots — 我需要什么槽位才能执行
  2. required_tools — 我需要什么工具（RAG / CRM / ...）
  3. instruction    — 我希望 Response Agent 完成什么任务

Orchestrator 收集这些信息后：
  - 检查槽位是否满足 → 决定 completed / pending
  - 收集 tools 决定是否触发 Tool Layer
  - 合并 instruction 传给 Response Agent
"""
from enum import Enum
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING


class Tool(Enum):
    """Orchestrator 统一调度的工具类型"""
    RAG = "rag"          # 知识库检索
    CRM = "crm"          # 客户关系管理（预留）
    CALCULATOR = "calc"  # 金融计算器（预留）
    WEATHER = "weather"  # 天气查询（调用第三方 API）


class BaseSkill:
    """
    Skill 元数据基类。

    子类只需声明类属性，不需要实现 execute()。
    """

    name: str                            # Skill 标识，与 Planner 的 sub_tasks 字段匹配
    required_slots: List[str] = []       # 必需的槽位列表
    optional_slots: List[str] = []       # 可选的槽位列表
    required_tools: List[Tool] = []      # 需要的工具列表
    instruction: str = ""                # 给 Response Agent 的指令

    @classmethod
    def check_slots(cls, slots: Dict[str, Any]) -> List[str]:
        """
        检查必需槽位是否齐全。

        返回缺失槽位列表（空列表 = 全部满足）。
        """
        return [
            s for s in cls.required_slots
            if s not in slots or slots[s] is None
        ]

    @classmethod
    def check_emotion(cls, emotion: str) -> bool:
        """
        情绪条件检查。

        子类可覆盖此方法实现情绪相关的决策逻辑。
        返回 False 表示当前情绪下该 Skill 不应执行。
        """
        return True

    @classmethod
    def can_execute(cls, slots: Dict[str, Any], emotion: str = "") -> bool:
        """综合判断是否可以执行（slots + emotion）。"""
        if not cls.check_emotion(emotion):
            return False
        return len(cls.check_slots(slots)) == 0
