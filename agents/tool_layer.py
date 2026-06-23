"""
Tool Layer — Orchestrator 统一调度的工具层

职责：
  - 提供统一的工具接口（当前有 RAG + Weather，预留 CRM / Calculator 等）
  - RAG 执行链路：Query Rewrite → Hybrid Search → RRF → TopK
  - Weather 执行链路：调用 wttr.in 第三方天气 API
  - 由 Orchestrator 集中触发，多个 Skill 共享结果

设计原则：
  - 同一 Tool 只执行一次，无论多少个 Skill 需要它
  - Query Rewrite 利用 Planner 的 sub_tasks + slots 构造高质量检索 query
  - 不重复传入 MemoryManager 上下文（Planner 阶段已消费完毕）
"""
import json
import logging
import urllib.parse
from typing import Any, Dict, List, Optional

import httpx

from agents.skills.base import Tool
from mcp.knowledge_base import KnowledgeBase

logger = logging.getLogger(__name__)


class ToolLayer:
    """
    工具层。

    用法：
        tool_layer = ToolLayer(knowledge_base)
        result = await tool_layer.exec_rag(
            user_query="多少钱",
            sub_tasks=["PRICE"],
            slots={"model": "M8"},
        )
    """

    def __init__(self, knowledge_base: KnowledgeBase):
        self._kb = knowledge_base

    # ── 公开接口 ──────────────────────────────────────────────────────────

    async def exec_tools(
        self,
        required_tools: List[Tool],
        user_query: str,
        sub_tasks: List[str],
        slots: Dict[str, Any],
        top_k: int = 5,
    ) -> Dict[str, Any]:
        """
        统一执行所需工具。

        参数：
            required_tools: 需要执行的工具列表
            user_query:     用户原始输入
            sub_tasks:      Planner 输出的子任务列表
            slots:          当前会话槽位

        返回：
            {"rag": [...chunks...]} 或 {"rag": [], "crm": {...}, ...}
        """
        results: Dict[str, Any] = {}

        if Tool.RAG in required_tools:
            results["rag"] = await self.exec_rag(user_query, sub_tasks, slots, top_k)

        if Tool.WEATHER in required_tools:
            results["weather"] = await self.exec_weather(slots)

        # 预留：if Tool.CRM in required_tools: ...
        # 预留：if Tool.CALCULATOR in required_tools: ...

        return results

    async def exec_rag(
        self,
        user_query: str,
        sub_tasks: List[str],
        slots: Dict[str, Any],
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        执行一次完整的 RAG 链路。

        Query Rewrite: 利用 sub_tasks + slots 增强原始 query。
        """
        query = self._rewrite_query(user_query, slots)
        logger.debug(
            f"RAG 检索: 原始={user_query!r} 改写={query!r} "
            f"sub_tasks={sub_tasks} slots={slots}"
        )

        try:
            return self._kb.search_hybrid(query, top_k=top_k)
        except Exception as ex:
            logger.error(f"RAG 检索失败: {ex}")
            return []

    # ── Weather ─────────────────────────────────────────────────────────────

    _WEATHER_API = "https://wttr.in/{location}?format=j1"

    async def exec_weather(self, slots: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        执行天气查询，调用 wttr.in 第三方 API（免费，无需 API Key）。

        从 slots 中读取 location，自动 URL 编码（支持中文地名），
        返回结构化天气数据供 Response Agent 使用。

        Args:
            slots: 当前会话槽位，需包含 location 字段

        Returns:
            格式化的天气 dict，包含地点、当前天气和预报；
            API 失败时返回 None。
        """
        location = slots.get("location", "")
        if not location:
            logger.warning("天气查询缺少 location 槽位")
            return None

        url = self._WEATHER_API.format(location=urllib.parse.quote(location))
        logger.debug(f"天气查询: location={location}")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
        except Exception as ex:
            logger.error(f"天气 API 调用失败: {ex}")
            return None

        return self._parse_weather(data, location)

    @staticmethod
    def _parse_weather(data: Dict[str, Any], raw_location: str) -> Dict[str, Any]:
        """
        解析 wttr.in 的 JSON 响应，提取关键天气信息。

        Args:
            data: wttr.in 返回的原始 JSON
            raw_location: 用户输入的地名（兜底用）

        Returns:
            提取后的天气 dict，包含 location / current / forecast 三个字段。
        """
        result: Dict[str, Any] = {}

        # 地点名（取 API 返回的标准化名称，兜底用用户输入）
        try:
            area = data.get("nearest_area", [{}])[0]
            result["location"] = area.get("areaName", [{}])[0].get("value", raw_location)
        except (IndexError, KeyError, TypeError):
            result["location"] = raw_location

        # 当前天气
        try:
            cc = data.get("current_condition", [{}])[0]
            desc_list = cc.get("weatherDesc", [])
            result["current"] = {
                "temp": cc.get("temp_C", ""),
                "feels_like": cc.get("FeelsLikeC", ""),
                "condition": desc_list[0].get("value", "") if desc_list else "",
                "humidity": cc.get("humidity", ""),
                "wind_dir": cc.get("winddir16Point", ""),
                "wind_speed": cc.get("windspeedKmph", ""),
                "cloudcover": cc.get("cloudcover", ""),
                "pressure": cc.get("pressure", ""),
                "uv_index": cc.get("uvIndex", ""),
            }
        except (IndexError, KeyError, TypeError):
            result["current"] = {}

        # 未来 3 天预报
        forecasts = []
        try:
            for day in data.get("weather", []):
                astro = day.get("astronomy", [{}])[0]
                forecasts.append({
                    "date": day.get("date", ""),
                    "max_temp": day.get("maxtempC", ""),
                    "min_temp": day.get("mintempC", ""),
                    "avg_temp": day.get("avgtempC", ""),
                    "sunrise": astro.get("sunrise", ""),
                    "sunset": astro.get("sunset", ""),
                })
        except (IndexError, KeyError, TypeError):
            pass
        result["forecast"] = forecasts

        return result

    # ── Query Rewrite ────────────────────────────────────────────────────

    @staticmethod
    def _rewrite_query(user_query: str, slots: Dict[str, Any]) -> str:
        """
        Query Rewrite：利用 Planner 提取的槽位信息增强检索 query。

        规则：
          - 如果有 model 槽位，前置到 query（"M8 多少钱" → "M8 多少钱"）
          - 如果有 issue 槽位，追加（"投诉" → "投诉 服务差"）
          - 如果 query 中已经包含槽位值，不重复追加

        当前实现为规则式（不调用 LLM，低成本）。
        """
        parts = []
        model = slots.get("model", "")
        product = slots.get("product", "")

        # 如果 query 中不包含 model，前置以增强召回
        if model and model not in user_query:
            parts.append(model)
        if product and product not in user_query:
            parts.append(product)

        parts.append(user_query)

        # 追加 issue（投诉场景）
        issue = slots.get("issue", "")
        if issue and issue not in user_query:
            parts.append(issue)

        return " ".join(parts)

    # ── 统计 ──────────────────────────────────────────────────────────────

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "kb_docs": getattr(self._kb, "doc_count", 0),
        }
