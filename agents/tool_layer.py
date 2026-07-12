"""
Tool Layer — Orchestrator 统一调度的工具层

职责：
  - 提供统一的工具接口（当前有 RAG + Weather，预留 CRM / Calculator 等）
  - RAG 执行链路（完整版）：规则改写 → LLM 多角度改写 → 3 子查询混合检索
    → 合并去重 → LLM 重排 → Top-K
  - Weather 执行链路：调用 wttr.in 第三方天气 API
  - 由 Orchestrator 集中触发，多个 Skill 共享结果

设计原则：
  - 同一 Tool 只执行一次，无论多少个 Skill 需要它
  - RAG 内部使用 MCPToolManager（缓存 + 熔断 + 改写 + 重排的完整链路）
  - 规则改写在前、LLM 改写在后，先注入 slot 信息再多角度扩展
"""
import json
import logging
import urllib.parse
from typing import Any, Dict, List, Optional

import httpx

from agents.skill_loader import Tool as SkillTool
from mcp.knowledge_base import KnowledgeBase

logger = logging.getLogger(__name__)


class ToolLayer:
    """
    工具层。

    内部维护 MCPToolManager，封装知识库检索（改写+混合检索+重排）和天气查询。
    """

    def __init__(
        self,
        knowledge_base: KnowledgeBase,
        api_key: str = "sk-92f09f3ada494ecd8390763ff293906b",
        base_url: Optional[str] = "https://api.deepseek.com/anthropic",
        model: str = "deepseek-chat",
    ):
        """
        初始化工具层，创建内部 MCPToolManager 并注册 knowledge_search 工具。

        Args:
            knowledge_base: 知识库实例
            api_key: LLM API Key（用于多角度改写 + 重排）
            base_url: LLM API 地址
            model: LLM 模型名
        """
        self._kb = knowledge_base

        # 内部 MCPToolManager（缓存 + 熔断 + 改写 + 重排复用已有实现）
        from mcp.tool_manager import MCPToolManager, Tool as MCPToolDef

        self._mcp = MCPToolManager(api_key=api_key, base_url=base_url, model=model)

        def knowledge_fallback(params: Dict[str, Any], context: Any, error: str) -> List[Dict[str, Any]]:
            """知识库不可用时的降级返回。"""
            query = params.get("query", "")
            return [{
                "title": "知识库降级结果",
                "content": f"知识库暂时不可用，未能完成对“{query}”的语义检索。请稍后重试。",
                "score": 0.0,
                "fallback": True,
                "error": error,
            }]

        self._mcp.register(MCPToolDef(
            name="knowledge_search",
            description="混合检索知识库（向量 + BM25 → RRF）",
            handler=self._kb.search_handler,
            schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer"},
                },
                "required": ["query"],
            },
            cache_ttl=300.0,
            supports_rerank=True,
            fallback=knowledge_fallback,
        ))

    # ── 公开接口 ──────────────────────────────────────────────────────────

    async def exec_tools(
        self,
        required_tools: List[SkillTool],
        user_query: str,
        sub_tasks: List[str],
        slots: Dict[str, Any],
        top_k: int = 5,
    ) -> Dict[str, Any]:
        """
        统一执行所需工具（由 Orchestrator 触发，多个 Skill 共享结果）。

        Args:
            required_tools: 需要执行的工具列表
            user_query:     用户原始输入
            sub_tasks:      Planner 输出的子任务列表
            slots:          当前会话槽位

        Returns:
            {"rag": [...chunks...], "weather": {...}, ...}
        """
        results: Dict[str, Any] = {}

        if SkillTool.RAG in required_tools:
            results["rag"] = await self.exec_rag(user_query, sub_tasks, slots, top_k)

        if SkillTool.WEATHER in required_tools:
            results["weather"] = await self.exec_weather(slots)

        if SkillTool.PHONE_VALIDATE in required_tools:
            results["phone_validate"] = self.validate_phone(slots)

        # 预留：if SkillTool.CRM in required_tools: ...
        # 预留：if SkillTool.CALCULATOR in required_tools: ...

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

        链路：规则改写（注入 slot） → LLM 多角度改写 → 3 子查询混合检索
              → 合并去重 → LLM 重排 → Top-K

        LLM 改写或重排失败时降级为直接混合检索。
        """
        # 第 1 步：规则改写——把 slot 值（车型/预算等）注入 query
        query = self._rewrite_query(user_query, slots)
        logger.debug(
            f"RAG 检索: 原始={user_query!r} "
            f"规则改写={query!r} "
            f"sub_tasks={sub_tasks} slots={slots}"
        )

        # 第 2-4 步：LLM 多角度改写 → 并行混合检索 → 合并且重排
        try:
            result = await self._mcp.search_with_rewrite(
                tool_name="knowledge_search",
                query=query,
                top_k=top_k,
            )
            return result.data if result.success else []
        except Exception as ex:
            logger.error(f"RAG 完整链路（改写+重排）失败，降级为直接混合检索: {ex}")
            try:
                return self._kb.search_hybrid(query, top_k=top_k)
            except Exception:
                return []

    # ── Weather ─────────────────────────────────────────────────────────────

    _WEATHER_API = "https://wttr.in/{location}?format=j1"

    async def exec_weather(self, slots: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        执行天气查询，调用 wttr.in 第三方 API（免费，无需 API Key）。

        Args:
            slots: 当前会话槽位，需包含 location 字段

        Returns:
            格式化的天气 dict（location / current / forecast）；
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

        try:
            area = data.get("nearest_area", [{}])[0]
            result["location"] = area.get("areaName", [{}])[0].get("value", raw_location)
        except (IndexError, KeyError, TypeError):
            result["location"] = raw_location

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

    # ── 规则改写（补充 MCPToolManager 的 LLM 改写）────────────────────────

    @staticmethod
    def _rewrite_query(user_query: str, slots: Dict[str, Any]) -> str:
        """
        规则式 Query Rewrite：利用 Planner 提取的槽位信息增强检索 query。

        在 LLM 多角度改写之前执行，先注入 slot 信息让 LLM 改写更有上下文。

        规则：
          - 有 model 槽位且 query 中没提到 → 前置
          - 有 product 槽位且 query 中没提到 → 前置
          - 有 issue 槽位且 query 中没提到 → 追加
        """
        parts = []
        model = slots.get("model", "")
        product = slots.get("product", "")

        if model and model not in user_query:
            parts.append(model)
        if product and product not in user_query:
            parts.append(product)

        parts.append(user_query)

        issue = slots.get("issue", "")
        if issue and issue not in user_query:
            parts.append(issue)

        return " ".join(parts)

    # ── 手机号校验 ─────────────────────────────────────────────────────────

    @staticmethod
    def validate_phone(slots: Dict[str, Any]) -> Dict[str, Any]:
        """
        校验手机号格式。中国手机号规则：11 位纯数字，以 1 开头。
        返回 {"valid": bool, "raw": str, "message": str}
        """
        import re
        phone = slots.get("phone", "")
        if not phone:
            return {"valid": True, "raw": "", "message": "无手机号"}

        digits_only = re.sub(r"\D", "", str(phone))
        if len(digits_only) == 11 and digits_only.startswith("1"):
            return {"valid": True, "raw": digits_only, "message": "格式正确"}
        return {
            "valid": False,
            "raw": str(phone),
            "message": f"手机号格式错误（需 11 位纯数字，以 1 开头）：{phone}",
        }

    # ── 统计 ──────────────────────────────────────────────────────────────

    @property
    def stats(self) -> Dict[str, Any]:
        """工具层统计信息。"""
        return {
            "kb_docs": getattr(self._kb, "doc_count", 0),
        }
